"""Everything about *how to run a CLI recon/OSINT tool* lives here, in one
place — main.py only knows "call perform_scan with this spec and this
target" and stays pure HTTP routing. See CONTEXT.md for the "ScanSpec"
domain term and why this module exists.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import HTTPException

from db import save_osint_scan


@dataclass
class ScanSpec:
    """Everything needed to run one CLI tool and turn its raw output into
    structured hits. One instance per tool; `run_cli_scan` is the only place
    that knows *how* to invoke a process — a spec only describes *what* to
    run. `build_args` and `read_output` both receive an optional `workdir`
    (a Path, only set when `needs_workdir=True`) so a file-based tool like
    theHarvester can agree with itself on where it wrote its output."""

    tool: str
    binary: str
    timeout: int
    build_args: Callable[[str, Path | None], list[str]]
    parse: Callable[[str], list[dict] | dict]
    read_output: Callable[[bytes, Path | None], str] = (
        lambda stdout, _workdir: stdout.decode(errors="replace")
    )
    needs_workdir: bool = False


async def run_cli_scan(spec: ScanSpec, target: str):
    """Run one CLI tool per its ScanSpec: start the process, enforce the
    timeout, map a non-zero exit code to an HTTPException, read the output
    (stdout or a file — whichever the spec asks for) and hand it to the
    tool's own parser. The only place asyncio.create_subprocess_exec is
    called for any tool."""
    workdir_ctx = tempfile.TemporaryDirectory() if spec.needs_workdir else None
    workdir = Path(workdir_ctx.name) if workdir_ctx else None

    try:
        args = spec.build_args(target, workdir)

        try:
            proc = await asyncio.create_subprocess_exec(
                spec.binary,
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Nie udało się uruchomić '{spec.binary}': {exc}",
            )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=spec.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(status_code=504, detail=f"{spec.tool} scan timed out")

        if proc.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=f"{spec.tool} exited with code {proc.returncode}: "
                f"{stderr.decode(errors='replace').strip()}",
            )

        raw = spec.read_output(stdout, workdir)
    finally:
        if workdir_ctx:
            workdir_ctx.cleanup()

    return spec.parse(raw)


async def perform_scan(
    spec: ScanSpec, target: str, engagement: str = "adhoc", display_target: str | None = None
) -> dict:
    """Run a scan, persist it, return everything a caller (JSON API or the
    HTMX dashboard) needs to report back. `display_target` lets a caller
    persist/report a different label than the string actually fed to the
    tool — exiftool's `target` is a server-side temp file path, but the
    history and the response should show the filename the user uploaded."""
    hits = await run_cli_scan(spec, target)
    label = display_target or target
    found_count = len(hits)
    save_osint_scan(spec.tool, label, found_count, hits, engagement)

    return {
        "target": label,
        "found_count": found_count,
        "results": hits,
    }


# ---------------------------------------------------------------------------
# sherlock — username recon across ~400 social/dev platforms
# ---------------------------------------------------------------------------

# Path to the sherlock executable. Locally (outside Docker) point this at the
# tool's own venv, e.g. C:\Users\hp\tools\sherlock\.venv\Scripts\sherlock.exe.
# In the container it just needs to be "sherlock" once installed on PATH.
SHERLOCK_BIN = os.environ.get("SHERLOCK_BIN", "sherlock")

# Wall-clock budget for the whole scan (subprocess start + all HTTP checks).
# A full run (no --site filter) checks ~400 services and realistically takes
# 2-3 minutes, so the default budget has to cover that, not just the per-site
# --timeout below.
SHERLOCK_TIMEOUT_SECONDS = int(os.environ.get("SHERLOCK_TIMEOUT_SECONDS", "240"))


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


SHERLOCK_SPEC = ScanSpec(
    tool="sherlock",
    binary=SHERLOCK_BIN,
    timeout=SHERLOCK_TIMEOUT_SECONDS,
    build_args=lambda target, _workdir: ["--print-found", "--timeout", "30", target],
    parse=parse_sherlock_output,
)


# ---------------------------------------------------------------------------
# theHarvester — subdomains/hosts for a domain (writes its result to a file,
# not stdout — the one tool that needs a workdir)
# ---------------------------------------------------------------------------

# Same idea as SHERLOCK_BIN — locally point at its own venv, e.g.
# C:\Users\hp\tools\theHarvester\.venv\Scripts\theHarvester.exe
THEHARVESTER_BIN = os.environ.get("THEHARVESTER_BIN", "theHarvester")

# hackertarget needs no API key and answers in a few seconds — good default.
# Other sources (crtsh, otx, ...) are slower or currently flaky; --source stays
# hardcoded for now, not user-controlled (avoids exposing every upstream
# source, some of which need API keys we don't have configured).
THEHARVESTER_SOURCE = os.environ.get("THEHARVESTER_SOURCE", "hackertarget")
THEHARVESTER_TIMEOUT_SECONDS = int(os.environ.get("THEHARVESTER_TIMEOUT_SECONDS", "90"))


def parse_theharvester_output(raw_json: str) -> list[dict]:
    """theHarvester's `-f <prefix>` writes `<prefix>.json` with a `hosts` list
    of `"subdomain:ip"` strings (ip is empty when it couldn't resolve one)."""
    data = json.loads(raw_json)
    hits = []
    for entry in data.get("hosts", []):
        host, _, ip = entry.partition(":")
        hits.append({"host": host, "ip": ip or None})
    return hits


def _theharvester_build_args(target: str, workdir: Path) -> list[str]:
    out_prefix = str(workdir / "result")
    return ["-d", target, "-b", THEHARVESTER_SOURCE, "-f", out_prefix]


def _theharvester_read_output(_stdout: bytes, workdir: Path) -> str:
    json_path = workdir / "result.json"
    if not json_path.exists():
        raise HTTPException(
            status_code=502,
            detail="theHarvester nie zapisał pliku wynikowego (nieoczekiwane).",
        )
    return json_path.read_text(encoding="utf-8")


THEHARVESTER_SPEC = ScanSpec(
    tool="theharvester",
    binary=THEHARVESTER_BIN,
    timeout=THEHARVESTER_TIMEOUT_SECONDS,
    build_args=_theharvester_build_args,
    read_output=_theharvester_read_output,
    parse=parse_theharvester_output,
    needs_workdir=True,
)


# ---------------------------------------------------------------------------
# exiftool — file metadata (target is a server-side temp file path; see
# perform_scan's `display_target` for how the real filename still gets shown)
# ---------------------------------------------------------------------------

EXIFTOOL_BIN = os.environ.get("EXIFTOOL_BIN", "exiftool")
EXIFTOOL_TIMEOUT_SECONDS = int(os.environ.get("EXIFTOOL_TIMEOUT_SECONDS", "30"))


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


EXIFTOOL_SPEC = ScanSpec(
    tool="exiftool",
    binary=EXIFTOOL_BIN,
    timeout=EXIFTOOL_TIMEOUT_SECONDS,
    build_args=lambda target, _workdir: ["-j", "-G", target],
    parse=parse_exiftool_output,
)


# ---------------------------------------------------------------------------
# nmap — open ports/services
# ---------------------------------------------------------------------------

NMAP_BIN = os.environ.get("NMAP_BIN", "nmap")
# -sV (service/version detection) is the whole point of running nmap here
# (feeds the next step — searchsploit-by-eye, picking an exploit) but makes
# a full run much slower than a bare connect scan, hence the generous
# default budget. -Pn skips host discovery: plenty of HTB/THM boxes drop
# ICMP, and without -Pn nmap would report them as down and scan nothing.
NMAP_TIMEOUT_SECONDS = int(os.environ.get("NMAP_TIMEOUT_SECONDS", "300"))


def parse_nmap_output(xml_str: str) -> list[dict]:
    """Parse nmap's `-oX -` XML into one dict per open port. Closed/filtered
    ports are dropped — for the HTB workflow this feeds, "not open" carries
    no information worth storing."""
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as exc:
        raise HTTPException(
            status_code=502, detail=f"Nie udało się sparsować wyniku nmap: {exc}"
        )

    open_ports = []
    for host in root.findall("host"):
        for port in host.findall("./ports/port"):
            state_el = port.find("state")
            if state_el is None or state_el.get("state") != "open":
                continue
            service_el = port.find("service")
            open_ports.append(
                {
                    "port": port.get("portid"),
                    "protocol": port.get("protocol"),
                    "service": service_el.get("name") if service_el is not None else None,
                    "product": service_el.get("product") if service_el is not None else None,
                    "version": service_el.get("version") if service_el is not None else None,
                }
            )
    return open_ports


NMAP_SPEC = ScanSpec(
    tool="nmap",
    binary=NMAP_BIN,
    timeout=NMAP_TIMEOUT_SECONDS,
    build_args=lambda target, _workdir: ["-Pn", "-sV", "-T4", "-oX", "-", target],
    parse=parse_nmap_output,
)
