import asyncio
import json
import os
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import get_connection, get_manual_notes, init_db, save_manual_note, save_osint_scan
from schemas import ManualNoteRequest, SherlockRequest, TheHarvesterRequest

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Path to the sherlock executable. Locally (outside Docker) point this at the
# tool's own venv, e.g. C:\Users\hp\tools\sherlock\.venv\Scripts\sherlock.exe.
# In the container it just needs to be "sherlock" once installed on PATH.
SHERLOCK_BIN = os.environ.get("SHERLOCK_BIN", "sherlock")

# Wall-clock budget for the whole scan (subprocess start + all HTTP checks).
# A full run (no --site filter) checks ~400 services and realistically takes
# 2-3 minutes, so the default budget has to cover that, not just the per-site
# --timeout below.
SHERLOCK_TIMEOUT_SECONDS = int(os.environ.get("SHERLOCK_TIMEOUT_SECONDS", "240"))

# Same idea as SHERLOCK_BIN — locally point at the tool's own venv, e.g.
# C:\Users\hp\tools\theHarvester\.venv\Scripts\theHarvester.exe
THEHARVESTER_BIN = os.environ.get("THEHARVESTER_BIN", "theHarvester")

# hackertarget needs no API key and answers in a few seconds — good default.
# Other sources (crtsh, otx, ...) are slower or currently flaky; --source stays
# hardcoded for now, not user-controlled (avoids exposing every upstream
# source, some of which need API keys we don't have configured).
THEHARVESTER_SOURCE = os.environ.get("THEHARVESTER_SOURCE", "hackertarget")
THEHARVESTER_TIMEOUT_SECONDS = int(os.environ.get("THEHARVESTER_TIMEOUT_SECONDS", "90"))

EXIFTOOL_BIN = os.environ.get("EXIFTOOL_BIN", "exiftool")
EXIFTOOL_TIMEOUT_SECONDS = int(os.environ.get("EXIFTOOL_TIMEOUT_SECONDS", "30"))
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB — plenty for a photo, not for abuse


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request, "dashboard.html", {"notes": get_manual_notes()}
    )


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/db-check")
def db_check():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version();")
            row = cur.fetchone()
    return {"postgres_version": row[0]}


def parse_sherlock_output(stdout: str) -> list[dict]:
    """Parse sherlock's `--print-found` stdout lines of the form
    `[+] ServiceName: https://...` into structured hits."""
    hits = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("[+] "):
            continue
        body = line.removeprefix("[+] ")
        if ": " not in body:
            continue
        service, url = body.split(": ", 1)
        hits.append({"service": service, "url": url})
    return hits


async def run_sherlock(username: str) -> list[dict]:
    try:
        proc = await asyncio.create_subprocess_exec(
            SHERLOCK_BIN,
            "--print-found",
            "--timeout",
            "30",
            username,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Nie udało się uruchomić '{SHERLOCK_BIN}': {exc}",
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=SHERLOCK_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HTTPException(status_code=504, detail="Sherlock scan timed out")

    if proc.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=f"Sherlock exited with code {proc.returncode}: "
            f"{stderr.decode(errors='replace').strip()}",
        )

    return parse_sherlock_output(stdout.decode(errors="replace"))


async def perform_sherlock_scan(username: str) -> dict:
    """Run sherlock, persist the result, return everything a caller (JSON API
    or the HTMX dashboard) needs to report back."""
    hits = await run_sherlock(username)
    save_osint_scan("sherlock", username, len(hits), hits)

    return {
        "target": username,
        "found_count": len(hits),
        "results": hits,
    }


@app.post("/scan/sherlock")
async def submit_sherlock_scan(payload: SherlockRequest):
    return await perform_sherlock_scan(payload.username)


@app.post("/scan/sherlock/ui", response_class=HTMLResponse)
async def submit_sherlock_scan_ui(request: Request, username: str = Form(...)):
    try:
        result = await perform_sherlock_scan(username)
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "_sherlock_result.html",
            {"error": exc.detail, "target": username},
        )
    return templates.TemplateResponse(request, "_sherlock_result.html", result)


def parse_theharvester_output(json_path: str) -> list[dict]:
    """theHarvester's `-f <prefix>` writes `<prefix>.json` with a `hosts` list
    of `"subdomain:ip"` strings (ip is empty when it couldn't resolve one)."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    hits = []
    for entry in data.get("hosts", []):
        host, _, ip = entry.partition(":")
        hits.append({"host": host, "ip": ip or None})
    return hits


async def run_theharvester(domain: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_prefix = os.path.join(tmp_dir, "result")

        try:
            proc = await asyncio.create_subprocess_exec(
                THEHARVESTER_BIN,
                "-d",
                domain,
                "-b",
                THEHARVESTER_SOURCE,
                "-f",
                out_prefix,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Nie udało się uruchomić '{THEHARVESTER_BIN}': {exc}",
            )

        try:
            _, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=THEHARVESTER_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(status_code=504, detail="theHarvester scan timed out")

        if proc.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=f"theHarvester exited with code {proc.returncode}: "
                f"{stderr.decode(errors='replace').strip()}",
            )

        json_path = out_prefix + ".json"
        if not os.path.exists(json_path):
            raise HTTPException(
                status_code=502,
                detail="theHarvester nie zapisał pliku wynikowego (nieoczekiwane).",
            )

        return parse_theharvester_output(json_path)


async def perform_theharvester_scan(domain: str) -> dict:
    """Run theHarvester, persist the result, return everything a caller (JSON
    API or the HTMX dashboard) needs to report back."""
    hits = await run_theharvester(domain)
    save_osint_scan("theharvester", domain, len(hits), hits)

    return {
        "target": domain,
        "found_count": len(hits),
        "results": hits,
    }


@app.post("/scan/theharvester")
async def submit_theharvester_scan(payload: TheHarvesterRequest):
    return await perform_theharvester_scan(payload.domain)


@app.post("/scan/theharvester/ui", response_class=HTMLResponse)
async def submit_theharvester_scan_ui(request: Request, domain: str = Form(...)):
    try:
        result = await perform_theharvester_scan(domain)
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "_theharvester_result.html",
            {"error": exc.detail, "target": domain},
        )
    return templates.TemplateResponse(request, "_theharvester_result.html", result)


def parse_exiftool_output(stdout: str) -> dict:
    """exiftool's `-j -G` prints a JSON array with one object per file — we
    only ever pass it one file."""
    data = json.loads(stdout)
    metadata = data[0] if data else {}
    # These three reflect our server-side temp file, not anything the caller
    # uploaded — drop them so a random tmp filename doesn't leak into the UI.
    for key in ("SourceFile", "File:FileName", "File:Directory"):
        metadata.pop(key, None)
    return metadata


async def run_exiftool(file_path: str) -> dict:
    try:
        proc = await asyncio.create_subprocess_exec(
            EXIFTOOL_BIN,
            "-j",
            "-G",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Nie udało się uruchomić '{EXIFTOOL_BIN}': {exc}",
        )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=EXIFTOOL_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HTTPException(status_code=504, detail="exiftool scan timed out")

    if proc.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=f"exiftool exited with code {proc.returncode}: "
            f"{stderr.decode(errors='replace').strip()}",
        )

    return parse_exiftool_output(stdout.decode(errors="replace"))


async def perform_exiftool_scan(file: UploadFile) -> dict:
    """Save the upload to a temp file, run exiftool against it, persist the
    result, then delete the temp file — nothing from the upload sticks around
    on disk once the request is done."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Plik za duży (limit 25 MB).")

    suffix = os.path.splitext(file.filename or "")[1]
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        metadata = await run_exiftool(tmp_path)
    finally:
        os.remove(tmp_path)

    target = file.filename or "upload"
    save_osint_scan("exiftool", target, len(metadata), metadata)

    return {
        "target": target,
        "found_count": len(metadata),
        "results": metadata,
    }


@app.post("/scan/exiftool")
async def submit_exiftool_scan(file: UploadFile = File(...)):
    return await perform_exiftool_scan(file)


@app.post("/scan/exiftool/ui", response_class=HTMLResponse)
async def submit_exiftool_scan_ui(request: Request, file: UploadFile = File(...)):
    try:
        result = await perform_exiftool_scan(file)
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "_exiftool_result.html",
            {"error": exc.detail, "target": file.filename},
        )
    return templates.TemplateResponse(request, "_exiftool_result.html", result)


@app.post("/notes")
def submit_manual_note(payload: ManualNoteRequest):
    return save_manual_note(payload.category, payload.note)


@app.post("/notes/ui", response_class=HTMLResponse)
def submit_manual_note_ui(
    request: Request, category: str = Form(...), note: str = Form(...)
):
    saved = save_manual_note(category, note)
    return templates.TemplateResponse(request, "_manual_note_item.html", {"note": saved})
