import asyncio
import json
import os

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import get_connection, init_db
from schemas import SherlockRequest

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


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html")


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

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO osint_scans (tool, target, found_count, results)
                VALUES (%s, %s, %s, %s)
                """,
                ("sherlock", username, len(hits), json.dumps(hits)),
            )
        conn.commit()

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
