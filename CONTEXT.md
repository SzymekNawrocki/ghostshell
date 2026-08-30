# GhostShell — domain glossary

Names for concepts in this codebase that aren't obvious from the code alone.
See `PLAN.md` for the project's build history and roadmap.

## Engagement

A named context a scan belongs to — an HTB/THM box, a CTF, or a real
assessment (e.g. `"HTB: Lame"`). Scans with no engagement given fall back to
`"adhoc"`. Stored as a plain column on `osint_scans`, not its own table —
there's nothing to look up about an engagement beyond its name; it exists
purely to group and sort scan history.

## ScanSpec

The declarative description of one CLI recon/OSINT tool: its binary, how to
build its command-line arguments for a given target, its timeout, how to
read its raw output back (most tools print to stdout; theHarvester writes a
file instead), and how to parse that raw output into structured hits.

`ScanSpec` instances (`SHERLOCK_SPEC`, `THEHARVESTER_SPEC`, `EXIFTOOL_SPEC`,
`NMAP_SPEC` in `scan_tools.py`) are pure data — no behavior of their own.
The actual behavior (start the process, enforce the timeout, map a
non-zero exit code to an error, read the output per the spec, hand it to
the parser) lives in one place: `run_cli_scan`. Adding a new tool (gobuster,
Nikto — see `PLAN.md` phase 2) means writing one `ScanSpec` and a parser
function, not another copy of the process-running boilerplate.

Introduced 2026-08-30 to replace four near-identical `run_*` functions in
`main.py` that had drifted into copy-paste — see the architecture review
that prompted it (candidate 1, "unify the four CLI tool integrations").

## Scan (osint_scans row)

One run of one tool against one target, persisted regardless of whether it
found anything: `tool` (`"sherlock"`, `"nmap"`, ...), `target` (what was
scanned — for exiftool this is the uploaded filename, not the server-side
temp path the tool actually ran against), `engagement`, `found_count`,
`results` (JSONB — whatever shape that tool's parser produces), `created_at`.

## Manual note

A free-text log entry for work no tool can automate (a practiced AD
technique, an ISO chapter read, a completed HTB machine) — deliberately not
merged with `osint_scans`; a manual note has no `tool`/`target`/`results`
shape to share with a real scan.
