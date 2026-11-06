"""scan: read-only walk of the mock device; statistics must be exact."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass
class SdcardTotals:
    entries: int
    files: int
    bytes: int
    paths: set[str]


def _expected_sdcard_totals(phone) -> SdcardTotals:
    """Independent os.walk-based ground truth for /sdcard."""
    base = phone.host("/sdcard")
    files = 0
    total = 0
    dirs = 0
    paths: set[str] = set()
    excluded = ("/.android-deep-clean", "/Android/.android-deep-clean")

    def device(host: Path) -> str:
        rel = host.relative_to(base).as_posix()
        return "/sdcard" if rel == "." else "/sdcard/" + rel

    def is_excluded(rel: str) -> bool:
        return any(rel == item.lstrip("/") or rel.startswith(item.lstrip("/") + "/") for item in excluded)

    for dirpath, dirnames, filenames in os.walk(base):
        current = Path(dirpath)
        rel = current.relative_to(base).as_posix()
        if rel != "." and is_excluded(rel):
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if not is_excluded(f"{rel}/{d}" if rel != "." else d))
        if rel == ".":
            paths.add("/sdcard")
        else:
            dirs += 1
            paths.add(device(current))
        for name in sorted(filenames):
            file_rel = name if rel == "." else f"{rel}/{name}"
            paths.add("/sdcard/" + file_rel)
            files += 1
            total += (current / name).stat().st_size
    return SdcardTotals(entries=1 + dirs + files, files=files, bytes=total, paths=paths)


def _db_entries(env) -> dict[str, tuple]:
    conn = sqlite3.connect(str(env.state_path("scan/snapshot.db")))
    try:
        rows = conn.execute("SELECT path, kind, size FROM entries ORDER BY path").fetchall()
    finally:
        conn.close()
    return {row[0]: (row[1], row[2]) for row in rows}


def test_scan_stats_match_os_walk(env):
    env.phone.file("/sdcard/DCIM/Camera/IMG_0001.jpg", size=1234)
    env.phone.file("/sdcard/DCIM/Camera/IMG_0002.jpg", size=0)
    env.phone.file("/sdcard/Download/tool.apk", size=5000)
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/a.bin", size=777)
    env.phone.dir("/sdcard/Documents/empty")
    env.phone.file("/sdcard/Documents/notes.txt", size=88)

    env.ok("doctor")
    before = env.snapshot()
    meta = env.ok("scan").json()
    env.assert_unchanged(before)

    expected = _expected_sdcard_totals(env.phone)
    assert meta["stats"] == {"entries": expected.entries, "files": expected.files, "bytes": expected.bytes}
    assert meta["roots_requested"] == ["/sdcard"]
    assert meta["roots_scanned"] == ["/sdcard"]
    assert meta["truncated"] is False
    assert meta["transport"] == "mock"

    db = _db_entries(env)
    assert set(db) == expected.paths
    for path, (kind, size) in db.items():
        if kind == "file":
            assert size == env.phone.host(path).stat().st_size, path
        else:
            assert kind == "dir", path

    summary = (env.state_path("scan/scan-summary.md")).read_text(encoding="utf-8")
    assert "# 存储扫描摘要" in summary
    assert "只读" in summary
    assert "- 截断：否" in summary


def test_scan_and_downstream_read_only_steps_do_not_touch_device(env):
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/one.bin", size=300)
    env.phone.file("/sdcard/DCIM/Camera/photo.jpg", size=400)

    before = env.snapshot()
    env.prepare_analysis()          # doctor + scan + analyze
    env.plan_now()                  # plan
    env.select_all_safe()           # select
    env.assert_unchanged(before)

    # Read-only phases must never create an execution record.
    assert not env.state_path("execution/execution.json").exists()
    assert not env.state_path("execution/journal.jsonl").exists()


def test_scan_excludes_skill_own_quarantine_area(env):
    env.phone.file("/sdcard/.android-deep-clean/quarantine/old-exec/files/x.bin", content="quarantined")
    env.phone.file("/sdcard/DCIM/keep.jpg", content="keep-me")

    meta = env.ok("doctor") and env.ok("scan").json()
    paths = set(_db_entries(env))
    assert "/sdcard/DCIM/keep.jpg" in paths
    assert not any(".android-deep-clean" in path for path in paths)
    assert meta["stats"]["files"] == 1


def test_scan_records_inaccessible_subtree_and_analyze_degrades(env):
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/hidden.bin", size=300)
    env.phone.file("/sdcard/Download/visible.bin", size=200)
    env.set_caps(privilege="shell", inaccessible=["/sdcard/Android/data"])

    doctor = env.ok("doctor").json()
    assert doctor["capabilities"]["android_data_read"] is False

    meta = env.ok("scan").json()
    assert meta["stats"]["files"] == 1
    skipped_paths = [item["path"] for item in meta["skipped"]]
    assert "/sdcard/Android/data" in skipped_paths
    assert any("权限" in item["reason"] for item in meta["skipped"])
    assert meta["limitations"], "scan-meta must keep doctor limitations"

    # analyze must not crash and must surface the limitation in its report.
    analysis = env.ok("analyze").json()
    assert analysis["limitations"]
    assert any("/sdcard/Android/data" in item for item in analysis["limitations"])
    summary = (env.state_path("scan/scan-summary.md")).read_text(encoding="utf-8")
    assert "/sdcard/Android/data" in summary


def test_scan_max_files_truncation_is_reported(env):
    for index in range(8):
        env.phone.file(f"/sdcard/DCIM/Camera/img_{index}.jpg", size=10 + index)
    env.set_config(scan={"max_files": 4})

    meta = env.ok("doctor") and env.ok("scan").json()
    assert meta["truncated"] is True
    assert meta["stats"]["entries"] <= 4
    assert any("max_files" in item["reason"] or "截断" in item["reason"] for item in meta["skipped"])
