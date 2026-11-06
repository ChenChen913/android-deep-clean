"""analyze: candidate detection, grouping accuracy and read-only report artifacts."""

from __future__ import annotations

import json

import pytest


def _analyze(env):
    env.ok("doctor")
    env.ok("scan")
    return env.ok("analyze").json()


def _file_paths(env, item):
    return {
        row["path"]
        for row in env.candidate_files(item["id"])
        if row["kind"] == "file"
    }


def test_app_cache_evidence_matches_filtered_manifest(env):
    cache = "/sdcard/Android/data/com.installed.app/cache"
    env.phone.file(cache + "/a.bin", size=300)
    env.phone.file(cache + "/msg.db", size=100)
    env.phone.file(cache + "/.nomedia", content="")

    _analyze(env)
    item = env.require_item("app_cache")
    assert item["file_count"] == 1
    assert item["total_bytes"] == 300
    evidence = {entry["code"]: entry["text"] for entry in item["evidence"]}
    assert "文件 1 个" in evidence["count"]
    assert item["human_size"] in evidence["size"]


def test_app_cache_group_reports_exact_size_and_file_count(env):
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/a.bin", size=300)
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/b.bin", size=500)
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/sub/c.bin", size=700)

    analysis = _analyze(env)
    item = env.require_item("app_cache")
    assert item["primary_path"] == "/sdcard/Android/data/com.installed.app/cache"
    assert item["risk"] == "SAFE"
    assert item["file_count"] == 3
    assert item["total_bytes"] == 1500
    assert item["ai_review_required"] is False
    assert item["selectable"] is True
    assert item["app_pkg"] == "com.installed.app"

    rows = env.candidate_files(item["id"])
    files = [row for row in rows if row["kind"] == "file"]
    dirs = [row for row in rows if row["kind"] == "dir"]
    assert len(files) == 3
    assert sum(row["size"] for row in files) == 1500
    assert {row["path"] for row in files} == {
        "/sdcard/Android/data/com.installed.app/cache/a.bin",
        "/sdcard/Android/data/com.installed.app/cache/b.bin",
        "/sdcard/Android/data/com.installed.app/cache/sub/c.bin",
    }
    assert any(row["role"] == "target_dir" for row in dirs)

    # aggregate summary must be internally consistent
    summary = analysis["summary"]
    assert summary["candidate_bytes"] == sum(i["total_bytes"] for i in analysis["items"])
    assert summary["candidate_files"] == sum(i["file_count"] for i in analysis["items"])
    assert summary["candidate_groups"] == len(analysis["items"])


def test_uninstall_residue_for_data_obb_and_media(env):
    env.set_packages(installed=["com.installed.app"], uninstalled=["com.gone.app"], system=[], available=True)
    env.phone.file("/sdcard/Android/data/com.gone.app/cache/left.bin", size=700)
    env.phone.file("/sdcard/Android/obb/com.gone.app/main.obb", size=1100)
    env.phone.file("/sdcard/Android/media/com.gone.app/download.bin", size=900)
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/live.bin", size=300)

    _analyze(env)
    residues = env.analysis_items("residue")
    primaries = {item["primary_path"] for item in residues}
    assert primaries == {
        "/sdcard/Android/data/com.gone.app",
        "/sdcard/Android/obb/com.gone.app",
        "/sdcard/Android/media/com.gone.app",
    }
    for item in residues:
        assert item["risk"] == "CAUTION"
        assert item["ai_review_required"] is True
        assert item["app_pkg"] == "com.gone.app"
        assert "com.gone.app" in item["title"]

    obb = next(item for item in residues if "/obb/" in item["primary_path"])
    assert obb["file_count"] == 1
    assert obb["total_bytes"] == 1100

    # An installed app's external data is not residue.
    assert all("com.installed.app" not in path for path in primaries)
    cache = env.require_item("app_cache")
    assert "com.installed.app" in cache["primary_path"]


def test_duplicates_keep_suggestion_and_same_size_different_content(env):
    payload = "ADC-DUPLICATE-CONTENT-" * 20  # 440 bytes, above the 100B threshold
    env.phone.file("/sdcard/DCIM/Camera/photo_keep.jpg", content=payload)
    env.phone.file("/sdcard/Pictures/photo_copy.jpg", content=payload)
    env.phone.file("/sdcard/Download/photo_copy2.jpg", content=payload)
    # Same size, different bytes: must NOT be treated as duplicates.
    env.phone.file("/sdcard/Misc/same_size_a.bin", size=4096)
    env.phone.file("/sdcard/Misc/same_size_b.bin", size=4096)

    _analyze(env)
    duplicates = env.analysis_items("duplicates")
    assert len(duplicates) == 1
    item = duplicates[0]
    assert item["file_count"] == 2
    assert item["total_bytes"] == len(payload) * 2
    assert item["risk"] == "CAUTION"
    assert "photo_keep.jpg" in item["keep_suggestion"]

    files = _file_paths(env, item)
    assert files == {
        "/sdcard/Pictures/photo_copy.jpg",
        "/sdcard/Download/photo_copy2.jpg",
    }
    assert "/sdcard/DCIM/Camera/photo_keep.jpg" not in files
    # same-size-different-content must not be reported as duplicates
    assert not any("/sdcard/Misc/" in path for path in files)
    all_dup_paths = set()
    for group in duplicates:
        all_dup_paths |= _file_paths(env, group)
    assert "/sdcard/Misc/same_size_a.bin" not in all_dup_paths
    assert "/sdcard/Misc/same_size_b.bin" not in all_dup_paths


def test_large_old_file_is_detected(env):
    env.phone.file("/sdcard/Documents/forgotten_archive.bin", size=2_000_000)

    _analyze(env)
    items = env.analysis_items("large_old")
    assert len(items) == 1
    item = items[0]
    assert item["primary_path"] == "/sdcard/Documents/forgotten_archive.bin"
    assert item["file_count"] == 1
    assert item["total_bytes"] == 2_000_000
    assert item["risk"] == "CAUTION"


def test_old_apk_is_detected(env):
    env.phone.file("/sdcard/Download/legacy_app.apk", size=1500)

    _analyze(env)
    items = env.analysis_items("apk")
    assert len(items) == 1
    item = items[0]
    assert item["primary_path"] == "/sdcard/Download"
    assert item["file_count"] == 1
    assert item["total_bytes"] == 1500
    assert "/sdcard/Download/legacy_app.apk" in item["sample_paths"]
    assert item["risk"] == "CAUTION"


def test_old_archive_is_detected(env):
    env.phone.file("/sdcard/Documents/backup_2020.zip", size=500)

    _analyze(env)
    items = env.analysis_items("archives")
    assert len(items) == 1
    item = items[0]
    assert item["primary_path"] == "/sdcard/Documents"
    assert item["total_bytes"] == 500
    assert item["file_count"] == 1


def test_old_screenshot_is_detected(env):
    env.phone.file("/sdcard/DCIM/Screenshots/Screenshot_20200101-120000.png", size=600)

    _analyze(env)
    items = env.analysis_items("screenshots")
    assert len(items) == 1
    item = items[0]
    assert item["primary_path"] == "/sdcard/DCIM/Screenshots"
    assert item["file_count"] == 1
    assert item["total_bytes"] == 600
    assert item["risk"] == "CAUTION"


def test_thumbnail_cache_is_detected(env):
    env.phone.file("/sdcard/.thumbnails/thumb_0001.jpg", size=300)

    _analyze(env)
    items = env.analysis_items("thumbnails")
    assert len(items) == 1
    item = items[0]
    assert item["primary_path"] == "/sdcard/.thumbnails"
    assert item["file_count"] == 1
    assert item["total_bytes"] == 300
    assert item["risk"] == "SAFE"
    assert item["ai_review_required"] is False


def test_temp_logs_are_detected(env):
    env.phone.file("/sdcard/Misc/logs/app.log", size=200)
    env.phone.file("/sdcard/Misc/logs/crash.dmp", size=100)

    _analyze(env)
    items = env.analysis_items("temp_logs")
    assert len(items) == 1
    item = items[0]
    assert item["primary_path"] == "/sdcard/Misc/logs"
    assert item["file_count"] == 2
    assert item["total_bytes"] == 300
    assert item["risk"] == "SAFE"
    assert item["selectable"] is True


def test_chat_cache_only_includes_cache_dirs_not_media(env):
    env.phone.file("/sdcard/tencent/MicroMsg/abc/cache/wx_a.bin", size=800)
    env.phone.file("/sdcard/tencent/MicroMsg/abc/cache/wx_b.bin", size=900)
    env.phone.file("/sdcard/tencent/MicroMsg/abc/image2/keep.jpg", size=400)
    env.phone.file("/sdcard/tencent/MicroMsg/abc/video2/keep.mp4", size=500)

    _analyze(env)
    chat_items = env.analysis_items("chat_cache")
    assert chat_items, "expected chat cache candidates for tencent cache dirs"
    chat_paths = set()
    for item in chat_items:
        assert item["risk"] == "CAUTION"
        assert item["ai_review_required"] is True
        assert item["app_pkg"] == "com.tencent.mm"
        chat_paths |= _file_paths(env, item)
    assert chat_paths == {
        "/sdcard/tencent/MicroMsg/abc/cache/wx_a.bin",
        "/sdcard/tencent/MicroMsg/abc/cache/wx_b.bin",
    }
    # Media folders are never part of the chat cache candidate, in any category.
    for item in env.analysis_items():
        if item["category"] != "chat_cache":
            continue
        paths = _file_paths(env, item)
        assert not any("/image2/" in path or "/video2/" in path for path in paths)


def test_empty_dir_candidate_and_grouping(env):
    env.phone.dir("/sdcard/Misc/EmptyDirA", age_days=5)
    env.phone.dir("/sdcard/Misc/EmptyDirB", age_days=5)
    env.phone.dir("/sdcard/Misc/NotEmpty", age_days=5)
    env.phone.file("/sdcard/Misc/NotEmpty/x.bin", size=10)

    _analyze(env)
    items = env.analysis_items("empty_dirs")
    assert len(items) == 1
    item = items[0]
    assert item["action"] == "rmdir"
    assert item["primary_path"] == "/sdcard/Misc"
    assert item["dir_count"] == 2
    rows = env.candidate_files(item["id"])
    assert {row["path"] for row in rows} == {
        "/sdcard/Misc/EmptyDirA",
        "/sdcard/Misc/EmptyDirB",
    }
    assert all(row["kind"] == "dir" for row in rows)
    assert "/sdcard/Misc/NotEmpty" not in {row["path"] for row in rows}


def test_analysis_writes_reports_and_review_template(env):
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/a.bin", size=300)
    env.phone.file("/sdcard/Download/legacy_app.apk", size=1500)
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/app.db", size=4000)

    analysis = _analyze(env)

    for relative in (
        "analysis/analysis.db",
        "analysis/analysis.json",
        "analysis/analysis-summary.json",
        "analysis/review-template.json",
        "analysis/report-before.md",
    ):
        assert env.state_path(relative).exists(), relative

    summary_json = json.loads(env.state_path("analysis/analysis-summary.json").read_text(encoding="utf-8"))
    assert summary_json["analysis_id"] == analysis["analysis_id"]
    assert summary_json["summary"]["candidate_groups"] == analysis["summary"]["candidate_groups"]

    template = json.loads(env.state_path("analysis/review-template.json").read_text(encoding="utf-8"))
    assert template["analysis_id"] == analysis["analysis_id"]
    review_ids = {item["id"] for item in analysis["review_items"]}
    assert {decision["id"] for decision in template["decisions"]} == review_ids
    assert all(decision["verdict"] == "" for decision in template["decisions"])

    report = env.state_path("analysis/report-before.md").read_text(encoding="utf-8")
    assert "# Android 深度清理分析报告（清理前）" in report
    assert "## 分类总览" in report
    assert "旧安装包" in report
    assert "应用缓存" in report
    assert "受保护内容" in report
    assert "app.db" in report


def test_fresh_files_are_not_flagged_by_age_rules(env):
    env.phone.file("/sdcard/Download/new.apk", size=1500, age_days=0)
    env.phone.file("/sdcard/Documents/fresh.bin", size=2_000_000, age_days=0)
    env.phone.file("/sdcard/DCIM/Screenshots/Screenshot_fresh.png", size=600, age_days=0)

    _analyze(env)
    assert not env.analysis_items("apk")
    assert not env.analysis_items("large_old")
    assert not env.analysis_items("screenshots")
