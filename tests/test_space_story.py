"""Space story and candidate explanation: readable, honest, never additive."""

from __future__ import annotations

import json

PKG = "com.installed.app"
APK = "/data/app/com.installed.app/base.apk"
CACHE = "/sdcard/Android/data/com.installed.app/cache"


def _setup(env):
    env.phone.file(APK, size=1000)
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file("/sdcard/DCIM/Camera/a.jpg", size=500)
    env.set_packages(
        installed=[PKG],
        uninstalled=[],
        system=["android"],
        available=True,
        apk_paths={PKG: APK},
        versions={PKG: "1.0"},
    )


def test_space_story_separates_disjoint_and_overlapping(env):
    _setup(env)
    env.prepare_analysis()
    story = env.analysis["summary"]["space_story"]
    items = {item["key"]: item for item in story["items"]}
    assert set(items) >= {"device_used", "file_scan", "app_apk", "app_external"}
    assert items["app_external"]["relation"] == "subset-of-file-scan"
    assert items["app_apk"]["relation"] == "disjoint-with-file-scan"
    assert items["file_scan"]["bytes"] == 800
    assert items["app_apk"]["bytes"] == 1000
    assert story["known_disjoint_bytes"] == 1800
    assert story["unexplained_bytes"] == 90_000_000_000 - 1800
    assert story["scan_ratio"] is not None
    assert any("不能重复相加" in item for item in story["caveats"])


def test_space_story_report_section(env):
    _setup(env)
    env.prepare_analysis()
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "空间去向速览" in report
    assert "已知且不重叠合计" in report
    assert "未解释差额" in report
    assert "包含在文件扫描内" in report
    assert "不能重复相加" in report


def test_candidate_explain_is_present_and_does_not_change_selection(env):
    _setup(env)
    env.prepare_analysis()
    item = env.require_item("app_cache")
    explain = item["explain"]
    assert explain["what"]
    assert explain["why"]
    assert explain["impact"]
    assert "缓存" in explain["what"] or "cache" in explain["what"].lower()

    summary = json.loads(env.state_path("analysis/analysis-summary.json").read_text(encoding="utf-8"))
    summary_item = next(entry for entry in summary["items"] if entry["id"] == item["id"])
    assert summary_item["explain"]["impact"] == explain["impact"]

    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "这是什么：" in report
    assert "为什么列出：" in report
    assert "删除影响：" in report


def test_empty_dirs_explain_mentions_rmdir(env):
    from adc_lib.rules import category_impact

    assert "非空目录会失败" in category_impact("empty_dirs", "rmdir")
    assert "无法恢复" in category_impact("media_old", "delete_files")
    assert "不可恢复" in category_impact("unknown_category", "delete_files")


def test_space_story_degrades_without_app_storage(env):
    _setup(env)
    env.ok("doctor")
    env.ok("scan", "--no-app-storage")
    analysis = env.ok("analyze").json()
    story = analysis["summary"]["space_story"]
    keys = {item["key"] for item in story["items"]}
    assert "app_apk" not in keys
    assert story["known_disjoint_bytes"] == 800
    assert any("APK" in item for item in story["caveats"])
