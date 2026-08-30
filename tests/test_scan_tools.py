"""Tests for the run_cli_scan/perform_scan seam and each tool's pure parser.

run_cli_scan is the one place that calls asyncio.create_subprocess_exec for
every tool — mock it once here and every ScanSpec's timeout/error-handling
gets covered, instead of mocking four near-identical subprocess call sites.
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from scan_tools import (
    ScanSpec,
    THEHARVESTER_SPEC,
    parse_exiftool_output,
    parse_nmap_output,
    parse_sherlock_output,
    parse_theharvester_output,
    perform_scan,
    run_cli_scan,
)


class FakeProcess:
    """Stands in for the object asyncio.create_subprocess_exec returns."""

    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0, hang: bool = False):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def make_spec(**overrides) -> ScanSpec:
    defaults = dict(
        tool="fake",
        binary="fake-bin",
        timeout=5,
        build_args=lambda target, _workdir: [target],
        parse=lambda raw: [{"line": raw}],
    )
    defaults.update(overrides)
    return ScanSpec(**defaults)


# --- run_cli_scan ------------------------------------------------------


async def test_run_cli_scan_happy_path_returns_parsed_output():
    spec = make_spec()
    fake_proc = FakeProcess(stdout=b"hello", returncode=0)

    with patch("scan_tools.asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
        result = await run_cli_scan(spec, "world")

    assert result == [{"line": "hello"}]


async def test_run_cli_scan_nonzero_exit_raises_502_with_stderr():
    spec = make_spec()
    fake_proc = FakeProcess(stdout=b"", stderr=b"permission denied", returncode=1)

    with patch("scan_tools.asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
        with pytest.raises(HTTPException) as exc_info:
            await run_cli_scan(spec, "world")

    assert exc_info.value.status_code == 502
    assert "permission denied" in exc_info.value.detail


async def test_run_cli_scan_missing_binary_raises_500():
    spec = make_spec(binary="does-not-exist")

    with patch(
        "scan_tools.asyncio.create_subprocess_exec",
        AsyncMock(side_effect=OSError("No such file or directory")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await run_cli_scan(spec, "world")

    assert exc_info.value.status_code == 500


async def test_run_cli_scan_timeout_kills_the_process():
    spec = make_spec(timeout=0.05)
    fake_proc = FakeProcess(hang=True)

    with patch("scan_tools.asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
        with pytest.raises(HTTPException) as exc_info:
            await run_cli_scan(spec, "world")

    assert exc_info.value.status_code == 504
    assert fake_proc.killed


async def test_run_cli_scan_passes_workdir_to_file_based_spec(tmp_path):
    # theHarvester's shape: build_args writes into workdir, read_output reads
    # back from it — the subprocess itself contributes nothing to the result.
    spec = make_spec(
        needs_workdir=True,
        build_args=lambda target, workdir: ["-o", str(workdir)],
        read_output=lambda _stdout, workdir: (workdir / "out.txt").read_text()
        if (workdir / "out.txt").exists()
        else "",
        parse=lambda raw: {"content": raw},
    )

    async def fake_exec(binary, *args, **kwargs):
        # Simulate the tool writing its output file before exiting.
        workdir = Path(args[1])
        (workdir / "out.txt").write_text("written by fake tool")
        return FakeProcess(returncode=0)

    with patch("scan_tools.asyncio.create_subprocess_exec", fake_exec):
        result = await run_cli_scan(spec, "irrelevant")

    assert result == {"content": "written by fake tool"}


# --- perform_scan --------------------------------------------------------


async def test_perform_scan_persists_and_reports_found_count(monkeypatch):
    saved = {}

    def fake_save_osint_scan(tool, target, found_count, results, engagement):
        saved.update(
            tool=tool, target=target, found_count=found_count, results=results, engagement=engagement
        )

    monkeypatch.setattr("scan_tools.save_osint_scan", fake_save_osint_scan)

    spec = make_spec(parse=lambda raw: [{"x": 1}, {"x": 2}])
    fake_proc = FakeProcess(stdout=b"anything", returncode=0)

    with patch("scan_tools.asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
        result = await perform_scan(spec, "target1", engagement="HTB: Lame")

    assert result == {"target": "target1", "found_count": 2, "results": [{"x": 1}, {"x": 2}]}
    assert saved == {
        "tool": "fake",
        "target": "target1",
        "found_count": 2,
        "results": [{"x": 1}, {"x": 2}],
        "engagement": "HTB: Lame",
    }


async def test_perform_scan_display_target_overrides_persisted_label(monkeypatch):
    # exiftool's case: the actual "target" passed to the tool is a server-side
    # temp file path, but the DB row and response should show the filename
    # the user uploaded, not that path.
    saved = {}
    monkeypatch.setattr(
        "scan_tools.save_osint_scan",
        lambda tool, target, found_count, results, engagement: saved.update(target=target),
    )

    spec = make_spec(parse=lambda raw: [])
    fake_proc = FakeProcess(stdout=b"[]", returncode=0)

    with patch("scan_tools.asyncio.create_subprocess_exec", AsyncMock(return_value=fake_proc)):
        result = await perform_scan(spec, "/tmp/xyz123.jpg", display_target="photo.jpg")

    assert result["target"] == "photo.jpg"
    assert saved["target"] == "photo.jpg"


# --- parse_sherlock_output -------------------------------------------------


def test_parse_sherlock_output_extracts_found_lines():
    stdout = (
        "[+] GitHub: https://github.com/torvalds\n"
        "some unrelated log noise\n"
        "[+] Reddit: https://reddit.com/u/torvalds\n"
    )
    assert parse_sherlock_output(stdout) == [
        {"service": "GitHub", "url": "https://github.com/torvalds"},
        {"service": "Reddit", "url": "https://reddit.com/u/torvalds"},
    ]


def test_parse_sherlock_output_empty_when_nothing_found():
    assert parse_sherlock_output("[-] GitHub: Not Found!\n") == []


# --- parse_theharvester_output ---------------------------------------------


def test_parse_theharvester_output_splits_host_and_ip():
    raw = json.dumps({"hosts": ["a.example.com:1.2.3.4", "b.example.com:"]})
    assert parse_theharvester_output(raw) == [
        {"host": "a.example.com", "ip": "1.2.3.4"},
        {"host": "b.example.com", "ip": None},
    ]


def test_theharvester_spec_read_output_reads_the_result_file(tmp_path):
    (tmp_path / "result.json").write_text(json.dumps({"hosts": ["c.example.com:9.9.9.9"]}))
    raw = THEHARVESTER_SPEC.read_output(b"ignored", tmp_path)
    assert THEHARVESTER_SPEC.parse(raw) == [{"host": "c.example.com", "ip": "9.9.9.9"}]


def test_theharvester_spec_read_output_missing_file_raises_502(tmp_path):
    with pytest.raises(HTTPException) as exc_info:
        THEHARVESTER_SPEC.read_output(b"", tmp_path)
    assert exc_info.value.status_code == 502


# --- parse_exiftool_output --------------------------------------------------


def test_parse_exiftool_output_strips_server_temp_path_fields():
    raw = json.dumps(
        [
            {
                "SourceFile": "/tmp/xyz123.jpg",
                "File:FileName": "xyz123.jpg",
                "File:Directory": "/tmp",
                "EXIF:Make": "Canon",
            }
        ]
    )
    assert parse_exiftool_output(raw) == {"EXIF:Make": "Canon"}


def test_parse_exiftool_output_empty_array_gives_empty_dict():
    assert parse_exiftool_output("[]") == {}


# --- parse_nmap_output -------------------------------------------------------


NMAP_XML = """<?xml version="1.0"?>
<nmaprun>
  <host>
    <ports>
      <port protocol="tcp" portid="22">
        <state state="open"/>
        <service name="ssh" product="OpenSSH" version="8.2"/>
      </port>
      <port protocol="tcp" portid="23">
        <state state="closed"/>
      </port>
    </ports>
  </host>
</nmaprun>
"""


def test_parse_nmap_output_keeps_only_open_ports():
    assert parse_nmap_output(NMAP_XML) == [
        {
            "port": "22",
            "protocol": "tcp",
            "service": "ssh",
            "product": "OpenSSH",
            "version": "8.2",
        }
    ]


def test_parse_nmap_output_invalid_xml_raises_502():
    with pytest.raises(HTTPException) as exc_info:
        parse_nmap_output("not xml at all")
    assert exc_info.value.status_code == 502
