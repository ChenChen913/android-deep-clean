"""Scan stderr handling: local permission failures must reach the coverage report.

Issue #2: ``find`` keeps walking after "Permission denied" on a subdirectory,
so a scan could silently miss a whole app's data while the report still said
"good".  These tests pin the three pieces of the fix: the shell script keeps
stderr, the stderr lines become skip records, and coverage degrades to partial.
"""

from __future__ import annotations

import io

from adc_lib import transport as transport_mod
from adc_lib.transport import LocalTransport, ShellTransport, parse_scan_errors


STAT_LINE = "81a4|10|1700000000|1700000000|1700000000|1|1|1|644|1000|/sdcard/a.bin\n"


class _FakeProc:
    """Minimal Popen stand-in: yields canned stdout and stderr."""

    def __init__(self, stdout_lines: list[str], stderr_lines: list[str], returncode: int = 0) -> None:
        self.stdout = io.StringIO("".join(stdout_lines))
        self.stderr = io.StringIO("".join(stderr_lines))
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


def test_parse_scan_errors_collects_permission_denied():
    lines = [
        "find: '/sdcard/Android/data/com.x': Permission denied",
        "find: /sdcard/Android/data/com.y: Permission denied",
        "stat: '/sdcard/a b/c': Permission denied",
        "find: '/sdcard/gone': No such file or directory",
        "xargs: stat: No such file or directory",
        "",
        "garbage line",
    ]

    records, denied = parse_scan_errors(lines, root="/sdcard")

    assert denied == 3
    assert [record["path"] for record in records] == [
        "/sdcard/Android/data/com.x",
        "/sdcard/Android/data/com.y",
        "/sdcard/a b/c",
    ]
    assert all("权限不足" in record["reason"] for record in records)
    assert all(record["root"] == "/sdcard" for record in records)


def test_parse_scan_errors_understands_gnu_wording():
    # The reporter runs --transport local inside proot Ubuntu, where find/stat
    # are GNU coreutils: the wording and the quote characters differ from toybox.
    lines = [
        "find: \u2018/sdcard/Android/data/com.z\u2019: Permission denied",
        "stat: cannot statx \u2018/sdcard/a b\u2019: Permission denied",
        "stat: cannot statx '/sdcard/c d': Permission denied",
    ]

    records, denied = parse_scan_errors(lines, root="/sdcard")

    assert denied == 3
    assert [record["path"] for record in records] == [
        "/sdcard/Android/data/com.z",
        "/sdcard/a b",
        "/sdcard/c d",
    ]


def test_parse_scan_errors_ignores_missing_files_and_dedupes():
    lines = [
        "find: '/sdcard/Android/data/com.x': No such file or directory",
        "find: '/sdcard/Android/data/com.y': Permission denied",
        "find: '/sdcard/Android/data/com.y': Permission denied",
    ]

    records, denied = parse_scan_errors(lines)

    assert denied == 1
    assert [record["path"] for record in records] == ["/sdcard/Android/data/com.y"]
    assert records[0]["root"] == ""


def test_parse_scan_errors_caps_detail_but_counts_everything():
    lines = [f"find: '/sdcard/d{i}': Permission denied" for i in range(300)]

    records, denied = parse_scan_errors(lines)

    assert denied == 300
    assert len(records) == transport_mod._SCAN_SKIP_LIMIT


def test_find_script_no_longer_discards_stderr():
    script = ShellTransport()._find_script("/sdcard", max_depth=0, excludes=[])

    assert "2>/dev/null" not in script
    assert "xargs -0 -n 200 stat -c" in script


def test_iter_tree_turns_stderr_into_skip_records(monkeypatch):
    proc = _FakeProc(
        [STAT_LINE],
        ["find: '/sdcard/Android/data/com.locked': Permission denied\n"],
    )
    monkeypatch.setattr(transport_mod.subprocess, "Popen", lambda *args, **kwargs: proc)
    transport = LocalTransport()
    monkeypatch.setattr(transport, "_shell_argv", lambda command: ["fake-shell", "-c", command])

    skipped: list[dict[str, str]] = []
    entries = list(transport.iter_tree("/sdcard", skip_sink=skipped))

    assert [entry.path for entry in entries] == ["/sdcard/a.bin"]
    assert skipped == [
        {
            "path": "/sdcard/Android/data/com.locked",
            "reason": "权限不足，无法读取（Permission denied）",
            "root": "/sdcard",
        }
    ]


def test_iter_tree_reports_omitted_denied_paths(monkeypatch):
    stderr_lines = [f"find: '/sdcard/d{i}': Permission denied\n" for i in range(10)]
    proc = _FakeProc([STAT_LINE], stderr_lines)
    monkeypatch.setattr(transport_mod.subprocess, "Popen", lambda *args, **kwargs: proc)
    transport = LocalTransport()
    monkeypatch.setattr(transport, "_shell_argv", lambda command: ["fake-shell", "-c", command])
    monkeypatch.setattr(transport_mod, "_SCAN_SKIP_LIMIT", 4)

    skipped: list[dict[str, str]] = []
    list(transport.iter_tree("/sdcard", skip_sink=skipped))

    assert len([item for item in skipped if item["path"].startswith("/sdcard/d")]) == 4
    assert skipped[-1]["path"] == "/sdcard"
    assert "另有 6 个路径权限不足" in skipped[-1]["reason"]


def test_iter_tree_without_skip_sink_still_scans(monkeypatch):
    proc = _FakeProc([STAT_LINE], ["find: '/sdcard/x': Permission denied\n"])
    monkeypatch.setattr(transport_mod.subprocess, "Popen", lambda *args, **kwargs: proc)
    transport = LocalTransport()
    monkeypatch.setattr(transport, "_shell_argv", lambda command: ["fake-shell", "-c", command])

    assert [entry.path for entry in transport.iter_tree("/sdcard")] == ["/sdcard/a.bin"]


def test_blocked_subdir_downgrades_coverage_to_partial(env):
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/a.bin", size=300)
    env.phone.file("/sdcard/Download/visible.bin", size=200)
    env.set_caps(privilege="shell", inaccessible=["/sdcard/Android/data/com.installed.app"])

    env.ok("doctor")
    meta = env.ok("scan").json()
    coverage = meta["coverage"]

    assert coverage["status"] == "partial"
    assert "/sdcard/Android/data/com.installed.app" in coverage["skipped_paths"]
    assert any("权限" in reason for reason in coverage["reasons"])
    summary = env.state_path("scan/scan-summary.md").read_text(encoding="utf-8")
    assert "部分覆盖" in summary or "未扫描" in summary
