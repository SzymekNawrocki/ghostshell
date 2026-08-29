import asyncio
import json
import os

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import get_connection, get_total_xp, init_db
from schemas import ScanResult, SherlockRequest

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


def calculate_level(total_xp: int) -> int:
    return total_xp // 500 + 1


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    total_xp = get_total_xp()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"total_xp": total_xp, "level": calculate_level(total_xp)},
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


def calculate_xp(high_count: int, critical_count: int) -> int:
    penalty = high_count * 5 + critical_count * 10
    return max(100 - penalty, 10)


def calculate_osint_xp(found_count: int) -> int:
    if found_count == 0:
        return 10  # still XP for running the recon, even with no hits
    return min(found_count * 15, 200)


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


@app.post("/quest/scan")
def submit_scan(result: ScanResult):
    xp_earned = calculate_xp(result.high_count, result.critical_count)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scans (image_name, high_count, critical_count, xp_earned)
                VALUES (%s, %s, %s, %s)
                """,
                (result.image_name, result.high_count, result.critical_count, xp_earned),
            )
            cur.execute("SELECT COALESCE(SUM(xp_earned), 0) FROM scans;")
            total_xp = cur.fetchone()[0]
        conn.commit()

    return {"xp_earned": xp_earned, "total_xp": total_xp}


async def perform_sherlock_scan(username: str) -> dict:
    """Run sherlock, persist the result, return everything a caller (JSON API
    or the HTMX dashboard) needs to report back."""
    hits = await run_sherlock(username)
    xp_earned = calculate_osint_xp(len(hits))

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO osint_scans (tool, target, found_count, results, xp_earned)
                VALUES (%s, %s, %s, %s, %s)
                """,
                ("sherlock", username, len(hits), json.dumps(hits), xp_earned),
            )
            cur.execute(
                "SELECT COALESCE(SUM(xp_earned), 0) FROM osint_scans WHERE tool = %s;",
                ("sherlock",),
            )
            total_xp = cur.fetchone()[0]
        conn.commit()

    grand_total_xp = get_total_xp()

    return {
        "target": username,
        "found_count": len(hits),
        "results": hits,
        "xp_earned": xp_earned,
        "total_xp": total_xp,
        "grand_total_xp": grand_total_xp,
        "level": calculate_level(grand_total_xp),
    }


@app.post("/quest/sherlock")
async def submit_sherlock_scan(payload: SherlockRequest):
    return await perform_sherlock_scan(payload.username)


@app.post("/quest/sherlock/ui", response_class=HTMLResponse)
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
