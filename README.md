# GhostShell

A recon/OSINT toolkit that runs real security tools for you — no memorizing flags, no juggling
ten terminals. One dashboard, five tools, one shared scan history.

Built for practicing on [TryHackMe](https://tryhackme.com) / [HackTheBox](https://hackthebox.com):
point it at a target, click Scan, get a structured result instead of raw CLI output.

## What it does

| Tool | What it finds |
|---|---|
| [Sherlock](https://github.com/sherlock-project/sherlock) | Username → accounts across ~400 social/dev platforms |
| [theHarvester](https://github.com/laramies/theHarvester) | Domain → subdomains and hosts |
| [ExifTool](https://exiftool.org/) | Uploaded file → embedded metadata (GPS, device, timestamps) |
| [Nmap](https://nmap.org/) | Host → open ports and service versions (`-Pn -sV -T4`) |
| [Gobuster](https://github.com/OJ/gobuster) | URL → hidden directories and files (`dir` mode, common extensions) |

Every scan is saved to Postgres and grouped by **engagement** (e.g. `"HTB: Lame"`), so history for
one box doesn't get lost in a flat list. Long-running scans (Nmap/Sherlock can take minutes) show
a live progress bar and elapsed-time counter instead of looking hung.

## Architecture

```
main.py        — pure HTTP routing (FastAPI), no tool-running logic of its own
scan_tools.py  — ScanSpec: a declarative description of each CLI tool (binary, args,
                 timeout, output parsing). run_cli_scan() is the one place that actually
                 calls the subprocess — every tool is data, not a copy-pasted function.
db.py          — Postgres access (osint_scans, manual_notes)
schemas.py     — Pydantic request models
templates/     — Jinja2 + HTMX dashboard, no build step / no JS framework
```

See [`CONTEXT.md`](CONTEXT.md) for the domain vocabulary (`ScanSpec`, `Engagement`, `Scan`) and
[`PLAN.md`](PLAN.md) for the build history and what's planned next (Nikto, a Blue Team panel).

## Running it

```bash
cp .env.example .env   # fill in a real POSTGRES_PASSWORD
docker compose up -d --build
```

Dashboard: http://localhost:8000

Everything (Sherlock, theHarvester, exiftool, Nmap, Gobuster) is installed inside the container —
no host setup needed beyond Docker. Env vars to point at custom binaries/wordlists/timeouts are
documented in [`.env.example`](.env.example).

## Testing

```bash
python -m venv .venv && .venv/Scripts/activate  # or source .venv/bin/activate on Linux/macOS
pip install -r requirements-dev.txt
pytest
```

Tests mock the subprocess layer (`run_cli_scan`), so they run without any of the actual security
tools installed — see [`tests/test_scan_tools.py`](tests/test_scan_tools.py).

## Status

Actively developed as a learning/practice project — not hardened for exposure beyond
`localhost`. See `PLAN.md` for what's done and what's next.
