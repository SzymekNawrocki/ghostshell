import os
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import get_connection, get_manual_notes, get_osint_scans, init_db, save_manual_note
from scan_tools import EXIFTOOL_SPEC, NMAP_SPEC, SHERLOCK_SPEC, THEHARVESTER_SPEC, perform_scan
from schemas import ManualNoteRequest, NmapRequest, SherlockRequest, TheHarvesterRequest

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB — plenty for a photo, not for abuse


@app.on_event("startup")
def on_startup():
    init_db()


def normalize_engagement(value: str) -> str:
    """A blank engagement field from an HTML form arrives as "", not missing
    — collapse that (and pure whitespace) to the same "adhoc" default the DB
    column already falls back to."""
    value = value.strip()
    return value if value else "adhoc"


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"notes": get_manual_notes(), "scans": get_osint_scans()},
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


@app.post("/scan/sherlock")
async def submit_sherlock_scan(payload: SherlockRequest):
    return await perform_scan(SHERLOCK_SPEC, payload.username, payload.engagement)


@app.post("/scan/sherlock/ui", response_class=HTMLResponse)
async def submit_sherlock_scan_ui(
    request: Request, username: str = Form(...), engagement: str = Form("")
):
    try:
        result = await perform_scan(SHERLOCK_SPEC, username, normalize_engagement(engagement))
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "_sherlock_result.html",
            {"error": exc.detail, "target": username},
        )
    return templates.TemplateResponse(request, "_sherlock_result.html", result)


@app.post("/scan/theharvester")
async def submit_theharvester_scan(payload: TheHarvesterRequest):
    return await perform_scan(THEHARVESTER_SPEC, payload.domain, payload.engagement)


@app.post("/scan/theharvester/ui", response_class=HTMLResponse)
async def submit_theharvester_scan_ui(
    request: Request, domain: str = Form(...), engagement: str = Form("")
):
    try:
        result = await perform_scan(THEHARVESTER_SPEC, domain, normalize_engagement(engagement))
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "_theharvester_result.html",
            {"error": exc.detail, "target": domain},
        )
    return templates.TemplateResponse(request, "_theharvester_result.html", result)


async def _run_exiftool_upload(file: UploadFile, engagement: str) -> dict:
    """Save the upload to a temp file, hand its path to perform_scan as the
    tool's "target" (exiftool doesn't know or care it came from an upload —
    see ScanSpec/display_target in scan_tools.py), then delete the temp file
    — nothing from the upload sticks around on disk once the request is
    done."""
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Plik za duży (limit 25 MB).")

    suffix = os.path.splitext(file.filename or "")[1]
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        return await perform_scan(
            EXIFTOOL_SPEC, tmp_path, engagement, display_target=file.filename or "upload"
        )
    finally:
        os.remove(tmp_path)


@app.post("/scan/exiftool")
async def submit_exiftool_scan(file: UploadFile = File(...)):
    return await _run_exiftool_upload(file, "adhoc")


@app.post("/scan/exiftool/ui", response_class=HTMLResponse)
async def submit_exiftool_scan_ui(
    request: Request, file: UploadFile = File(...), engagement: str = Form("")
):
    try:
        result = await _run_exiftool_upload(file, normalize_engagement(engagement))
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "_exiftool_result.html",
            {"error": exc.detail, "target": file.filename},
        )
    return templates.TemplateResponse(request, "_exiftool_result.html", result)


@app.post("/scan/nmap")
async def submit_nmap_scan(payload: NmapRequest):
    return await perform_scan(NMAP_SPEC, payload.target, payload.engagement)


@app.post("/scan/nmap/ui", response_class=HTMLResponse)
async def submit_nmap_scan_ui(
    request: Request, target: str = Form(...), engagement: str = Form("")
):
    try:
        result = await perform_scan(NMAP_SPEC, target, normalize_engagement(engagement))
    except HTTPException as exc:
        return templates.TemplateResponse(
            request,
            "_nmap_result.html",
            {"error": exc.detail, "target": target},
        )
    return templates.TemplateResponse(request, "_nmap_result.html", result)


@app.post("/notes")
def submit_manual_note(payload: ManualNoteRequest):
    return save_manual_note(payload.category, payload.note)


@app.post("/notes/ui", response_class=HTMLResponse)
def submit_manual_note_ui(
    request: Request, category: str = Form(...), note: str = Form(...)
):
    saved = save_manual_note(category, note)
    return templates.TemplateResponse(request, "_manual_note_item.html", {"note": saved})
