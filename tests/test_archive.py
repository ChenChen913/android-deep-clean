"""Directory archive and coverage: read-only, cross-round, never deletion."""

from __future__ import annotations

import json
import os
import sqlite3

import pytest

CACHE = "/sdcard/Android/data/com.installed.app/cache"


def _diff(env) -> dict:
    path = env.state_path("scan/directory-diff.json")
    assert path.exists(), "scan should write directory-diff.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _dir_stats(env, path: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(str(env.state_path("scan/snapshot.db")))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM dir_stats WHERE path=?", (path,)).fetchone()
    finally:
        conn.close()


def test_first_scan_baseline_then_new_and_grew(env):
    env.phone.file("/sdcard/DCIM/Camera/a.jpg", size=300)
    env.phone.file("/sdcard/Download/x.bin", size=200)

    env.ok("doctor")
    env.ok("scan")
    first = _diff(env)
    assert first["baseline"] is True
    assert first["summary"]["new"] == 0

    env.phone.file("/sdcard/DCIM/Camera/b.jpg", size=500)
    env.phone.file("/sdcard/NewFolder/c.bin", size=400)
    env.ok("scan")
    second = _diff(env)
    assert second["baseline"] is False
    assert any(item["path"] == "/sdcard/DCIM/Camera" for item in second["grew"])
    assert any(item["path"] == "/sdcard/NewFolder" for item in second["new"])
    assert second["environment_changed"] is False

    stats = env.ok(["archive", "stats"]).json()
    assert stats["directories"] > 0
    assert stats["archive_path"].endswith("archive.db")


def test_vanished_then_resurrected(env):
    env.phone.file("/sdcard/Keep/a.bin", size=300)
    env.phone.file("/sdcard/Other/keep.bin", size=200)
    env.ok("doctor")
    env.ok("scan")
    assert _diff(env)["baseline"] is True

    env.phone.remove("/sdcard/Keep")
    env.ok("scan")
    vanished = _diff(env)
    assert any(item["path"] == "/sdcard/Keep" for item in vanished["vanished"])

    env.phone.file("/sdcard/Keep/a.bin", size=300)
    env.ok("scan")
    resurrected = _diff(env)
    assert any(item["path"] == "/sdcard/Keep" for item in resurrected["resurrected"])


def test_incomplete_scan_never_marks_directories_vanished(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.ok("doctor")
    env.ok("scan")
    assert _diff(env)["baseline"] is True

    env.phone.remove(CACHE)
    env.set_caps(privilege="shell", inaccessible=["/sdcard/Android/data"])
    env.ok("doctor")  # force capability refresh after changing mock caps
    env.ok("scan")
    diff = _diff(env)
    assert diff["scan_complete"] is False
    assert diff["vanished"] == []
    assert any("不完整" in item for item in diff["limitations"])


def test_environment_change_resets_baseline_without_flooding_new(env):
    env.phone.file("/sdcard/DCIM/Camera/a.jpg", size=300)
    env.ok("doctor")
    env.ok("scan")
    assert _diff(env)["baseline"] is True

    env.device["fingerprint"] = "mock/MockPhone/mock:15/UP2A/2:user/test-keys"
    env.flush()
    env.ok("doctor")
    env.ok("scan")
    diff = _diff(env)
    assert diff["environment_changed"] is True
    assert diff["baseline"] is True
    assert diff["summary"]["new"] == 0


def test_archive_mark_is_evidence_only(env):
    env.phone.file("/sdcard/DCIM/Camera/a.jpg", size=300)
    env.prepare_analysis()

    marked = env.ok([
        "archive", "mark",
        "--path", "/sdcard/DCIM/Camera",
        "--decision", "keep",
        "--note", "用户相片目录",
    ]).json()
    assert marked["decision"] == "keep"
    assert marked["note"] == "用户相片目录"

    stats = env.ok(["archive", "stats"]).json()
    assert stats["reviewed"] >= 1

    cleared = env.ok([
        "archive", "mark",
        "--path", "/sdcard/DCIM/Camera",
        "--decision", "neutral",
    ]).json()
    assert cleared["decision"] == "neutral"

    env.run(["archive", "mark", "--path", "/sdcard/does-not-exist",
             "--decision", "keep"]).expect_error(5)
    env.run(["archive", "mark", "--path", "/sdcard/DCIM/Camera",
             "--decision", "delete"]).expect_error(2)


def test_archive_diff_requires_a_scan(env):
    env.run(["archive", "diff"]).expect_error(5)


def test_analysis_has_archive_evidence_but_no_new_candidates(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    analysis = env.prepare_analysis()
    items = [item for item in analysis["items"] if item["primary_path"] == CACHE]
    assert items, "app cache candidate should exist"
    assert any(
        evidence.get("code") == "history"
        for evidence in items[0].get("evidence") or []
    )
    # The archive is evidence only: it must not create a cleanup category.
    assert not [item for item in analysis["items"] if item["category"] == "archive"]


def test_recheck_reports_directory_changes(env):
    env.phone.file("/sdcard/DCIM/Camera/a.jpg", size=300)
    env.prepare_analysis()
    env.phone.file("/sdcard/DCIM/Camera/b.jpg", size=500)

    recheck = env.ok("recheck").json()
    changes = recheck["directory_changes"]
    assert changes["baseline"] is False
    assert changes["summary"]["grew"] >= 1
    report = env.state_path("recheck/report-recheck.md").read_text(encoding="utf-8")
    assert "跨轮目录变化" in report


def test_dir_stats_and_unique_bytes(env):
    env.phone.file("/sdcard/DCIM/Camera/a.jpg", size=300)
    env.phone.file("/sdcard/DCIM/Camera/b.jpg", size=500)
    env.phone.dir("/sdcard/Documents/empty")

    meta = env.ok("doctor") and env.ok("scan").json()
    coverage = meta["coverage"]
    assert coverage["status"] == "good"
    assert coverage["unique_bytes"] == 800
    assert coverage["hardlink_saved_bytes"] == 0

    camera = _dir_stats(env, "/sdcard/DCIM/Camera")
    assert camera is not None
    assert camera["direct_bytes"] == 800
    assert camera["subtree_bytes"] == 800
    assert camera["subtree_files"] == 2
    root = _dir_stats(env, "/sdcard")
    assert root is not None
    assert root["subtree_bytes"] == 800

    summary = env.state_path("scan/scan-summary.md").read_text(encoding="utf-8")
    assert "扫描覆盖" in summary
    assert "覆盖率状态" in summary


def test_hardlinked_file_is_counted_once(env):
    first = env.phone.file("/sdcard/DCIM/Camera/a.bin", size=300)
    second = env.phone.host("/sdcard/DCIM/Camera/b.bin")
    os.link(first, second)
    os.utime(second, (first.stat().st_atime, first.stat().st_mtime))

    meta = env.ok("doctor") and env.ok("scan").json()
    coverage = meta["coverage"]
    assert meta["stats"]["bytes"] == 600
    assert coverage["unique_bytes"] == 300
    assert coverage["hardlink_saved_bytes"] == 300


def test_coverage_reports_inaccessible_root(env):
    env.phone.file("/sdcard/Download/visible.bin", size=200)
    env.phone.file(CACHE + "/hidden.bin", size=300)
    env.set_caps(privilege="shell", inaccessible=["/sdcard/Android/data"])

    env.ok("doctor")
    meta = env.ok("scan").json()
    coverage = meta["coverage"]
    assert coverage["status"] == "partial"
    assert "/sdcard/Android/data" in coverage["skipped_paths"]
    summary = env.state_path("scan/scan-summary.md").read_text(encoding="utf-8")
    assert "部分覆盖" in summary or "未扫描" in summary


def test_app_version_change_is_reported_without_orphan_claim(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.set_packages(versions={"com.installed.app": "1.0"})
    env.ok("doctor")
    env.ok("scan")
    assert _diff(env)["baseline"] is True

    env.set_packages(versions={"com.installed.app": "2.0"})
    env.ok("scan")
    diff = _diff(env)
    assert any(item["path"] == CACHE for item in diff["app_updated"])
    assert diff["orphan_suspect"] == []


def test_archive_mark_persists_across_scans(env):
    env.phone.file("/sdcard/DCIM/Camera/a.jpg", size=300)
    env.ok("doctor")
    env.ok("scan")
    env.ok([
        "archive", "mark", "--path", "/sdcard/DCIM/Camera",
        "--decision", "keep", "--note", "相片",
    ])
    env.ok("scan")
    diff = _diff(env)
    assert not [item for item in diff["unreviewed"]
                if item["path"] == "/sdcard/DCIM/Camera"]
    analysis = env.ok("analyze").json()
    item = next(item for item in analysis["items"]
                if item["primary_path"] == "/sdcard/DCIM/Camera")
    assert any("档案标记 keep" in evidence.get("text", "")
               for evidence in item["evidence"])


def test_no_package_manager_never_invents_orphans(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.set_packages(available=False)
    env.ok("doctor")
    env.ok("scan")
    diff = _diff(env)
    assert diff["orphan_suspect"] == []


def test_recheck_keeps_platform_and_does_not_reset_baseline(env):
    """Regression: recheck rebuilt scan meta without `platform`, so a
    HarmonyOS compat device was archived as `android` and every recheck
    reported a false environment change."""
    env.phone.file("/sdcard/DCIM/Camera/a.jpg", size=300)
    env.device.update({
        "platform": "harmonyos_compat",
        "sdk": 29,
        "release": "10",
        "os_version": "3.0.0",
        "harmony_version": "3.0.0",
    })
    env.flush()
    env.ok("doctor")
    env.ok("scan")
    env.ok("analyze")
    first = _diff(env)
    assert first["baseline"] is True

    recheck = env.ok("recheck").json()
    changes = recheck["directory_changes"]
    assert changes["environment_changed"] is False
    assert changes["baseline"] is False
    assert changes["environment"]["platform"] == "harmonyos_compat"


def test_old_snapshot_without_dir_stats_still_analyzes(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.prepare_analysis()

    conn = sqlite3.connect(str(env.state_path("scan/snapshot.db")))
    try:
        conn.execute("DROP TABLE dir_stats")
        conn.commit()
    finally:
        conn.close()
    meta_path = env.state_path("scan/snapshot-meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.pop("coverage", None)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    analysis = env.ok("analyze").json()
    assert analysis["summary"]["storage"]["coverage_status"] in {"unknown", "partial", "suspect"}


def test_archive_disabled_still_scans(env):
    env.phone.file("/sdcard/DCIM/Camera/a.jpg", size=300)
    env.ok("doctor")
    env.ok("scan", "--no-archive")
    assert not env.state_path("history/archive.db").exists()
    assert env.scan_meta["coverage"]["status"] == "good"
