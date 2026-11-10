"""HTML report: self-contained, escaped, offline, read-only."""

from __future__ import annotations

import re

from adc_lib.html_report import render_analysis_html, render_cleanup_html

CAMERA = "/sdcard/DCIM/Camera"
CACHE = "/sdcard/Android/data/com.installed.app/cache"


def _analysis(**overrides):
    base = {
        "analysis_id": "analysis-test",
        "generated_at": "2026-11-09T12:00:00+08:00",
        "platform": "android",
        "device_identity": {"serial": "MOCK-1"},
        "summary": {
            "device": {"manufacturer": "MockCorp", "model": "MockPhone"},
            "android": "Android 14 (API 34)",
            "stats": {"files": 10, "bytes": 1000},
            "candidate_groups": 1,
            "candidate_files": 2,
            "candidate_bytes": 800,
            "review_required": 0,
            "categories": [
                {"category": "app_cache", "title": "应用缓存", "groups": 1,
                 "files": 2, "bytes": 800, "risk": "SAFE"},
            ],
            "risk_totals": {"SAFE": {"groups": 1, "files": 2, "bytes": 800}},
            "limitations": ["无法读取 /data/data"],
            "protected_items": [],
            "storage": {},
            "coverage": {
                "status": "good",
                "logical_bytes": 1000,
                "unique_bytes": 1000,
                "dir_count": 3,
                "root_status": [{"path": "/sdcard", "state": "scanned", "reason": ""}],
                "largest_dirs": [{"path": CAMERA, "bytes": 900, "files": 5}],
            },
            "space_story": {
                "headline": "设备已用 10 GB，扫描 1 GB。",
                "device_used": 10_000,
                "known_disjoint_bytes": 2_000,
                "known_disjoint_human": "1.95 KB",
                "unexplained_human": "7.81 KB",
                "items": [
                    {"key": "device_used", "label": "设备已用空间", "bytes": 10_000,
                     "human": "9.77 KB", "measure": "块占用", "scope": "设备",
                     "relation": "total"},
                    {"key": "file_scan", "label": "共享存储文件扫描", "bytes": 1_000,
                     "human": "1000 B", "measure": "逻辑大小", "scope": "/sdcard",
                     "relation": "disjoint-with-apk"},
                ],
                "caveats": ["不同来源不能重复相加。"],
            },
            "media_index": {
                "status": "collected",
                "available": True,
                "reason": "",
                "index": {"items": 3, "bytes": 300, "bytes_human": "300 B",
                          "path_known": 3, "trashed": 0, "pending": 0},
                "crosscheck": {
                    "match_ratio": 1.0, "index_in_scope": 3, "index_only_in_scope": 0,
                    "scan_only": 1, "case_mismatch": 0, "found_in_scan": 3,
                    "samples": {"index_only": [], "index_only_outside": [],
                                "size_mismatch": [], "scan_only": []},
                },
                "limitations": [],
            },
            "app_storage": {
                "status": "collected",
                "available": True,
                "reason": "",
                "packages": {"total": 1, "installed": 1, "system": 0},
                "known": {"apk_human": "500 B", "external_data_human": "300 B",
                          "external_media_human": "0 B", "external_obb_human": "0 B",
                          "total_human": "800 B"},
                "top_external": [{"label": "Mock", "package": "com.mock",
                                  "external_total_human": "300 B", "data_human": "300 B",
                                  "media_human": "0 B", "obb_human": "0 B",
                                  "apk_human": "500 B"}],
                "residue": [],
                "limitations": ["内部 data/cache 不可读"],
            },
            "directory_changes": {"baseline": True, "summary": {}, "grew": []},
        },
        "items": [
            {
                "id": "app_cache-abc", "category": "app_cache", "category_title": "应用缓存",
                "title": "应用缓存：/sdcard/Android/data/x/cache", "risk": "SAFE",
                "action": "clean_dir_children", "primary_path": "/sdcard/Android/data/x/cache",
                "sample_paths": ["/sdcard/Android/data/x/cache/a.bin"],
                "total_bytes": 800, "human_size": "800 B", "file_count": 2, "dir_count": 1,
                "confidence": 0.9, "app_pkg": "x", "app_kind": "app",
                "evidence": [{"code": "path", "text": "路径：/sdcard/..."}],
                "reasons": ["缓存目录通常由应用自动重建。"],
                "explain": {"what": "应用缓存目录", "why": "超过保留期",
                            "impact": "应用会重新生成缓存"},
                "keep_suggestion": "离线内容需重新下载。",
                "ai_review_required": False, "selectable": True, "notes": "",
            }
        ],
        "limitations": ["无法读取 /data/data"],
    }
    base.update(overrides)
    return base


def test_html_is_self_contained_and_has_all_sections():
    html = render_analysis_html(_analysis())
    assert html.startswith("<!DOCTYPE html>")
    for section in ("空间去向速览", "扫描覆盖", "媒体索引核对", "应用级存储",
                    "清理候选", "限制与缺口"):
        assert section in html, section
    assert 'id="space"' in html and 'id="candidates"' in html
    assert "<details" in html
    assert "只读" in html
    assert "尚未删除" in html


def test_html_has_no_external_resources():
    html = render_analysis_html(_analysis())
    assert "http://" not in html
    assert "https://" not in html
    assert "<script" not in html.lower()
    assert "src=" not in html


def test_html_escapes_untrusted_paths():
    payload = '<img src=x onerror=alert(1)>.tmp'
    analysis = _analysis()
    analysis["items"][0]["sample_paths"] = [f"/sdcard/{payload}"]
    analysis["items"][0]["title"] = f"坏路径 {payload}"
    analysis["items"][0]["evidence"] = [{"code": "path", "text": payload}]
    html = render_analysis_html(analysis)
    assert "<img src=x" not in html
    assert "&lt;img" in html
    assert "onerror=alert(1)" not in html or "&lt;img" in html


def test_html_shows_partial_coverage_in_chinese():
    """issue #2: a locked subdirectory must not look like a complete scan."""
    analysis = _analysis()
    analysis["summary"]["coverage"] = {
        "status": "partial",
        "logical_bytes": 1000,
        "unique_bytes": 1000,
        "dir_count": 3,
        "reasons": ["扫描过程中跳过 1 个路径（权限不足/无法读取），结果不完整"],
        "skipped_paths": ["/sdcard/Android/data/com.locked.app"],
        "root_status": [{"path": "/sdcard", "state": "scanned", "reason": ""}],
    }

    html = render_analysis_html(analysis)

    assert "部分覆盖" in html
    assert ">partial<" not in html
    assert "覆盖限制" in html
    assert "/sdcard/Android/data/com.locked.app" in html


def test_html_without_optional_sections_does_not_crash():
    analysis = _analysis()
    analysis["summary"]["media_index"] = {}
    analysis["summary"]["app_storage"] = {}
    analysis["summary"]["space_story"] = {}
    analysis["summary"]["directory_changes"] = {}
    analysis["items"] = []
    html = render_analysis_html(analysis)
    assert "清理候选" in html
    assert "未发现候选" in html


def test_analyze_writes_html_and_keeps_device_untouched(env):
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/a.bin", size=300)
    before = env.snapshot()
    env.prepare_analysis()
    path = env.state_path("analysis/report-before.html")
    assert path.exists()
    html = path.read_text(encoding="utf-8")
    assert "空间去向速览" in html
    assert "清理候选" in html
    assert re.search(r"app_cache", html)
    assert env.state_json("state.json")["files"]["report_html"].endswith("report-before.html")
    env.assert_unchanged(before)


# ---------------------------------------------------------------- cleanup report
def _cleanup_bundle():
    analysis = _analysis()
    selection = {
        "mode": "quarantine",
        "summary": {"groups": 1, "files": 2, "bytes": 800, "human_bytes": "800 B"},
        "items": [{
            "id": "app_cache-abc",
            "title": "应用缓存：/sdcard/Android/data/x/cache",
            "category": "app_cache",
            "category_title": "应用缓存",
            "risk": "SAFE",
            "action": "clean_dir_children",
            "file_count": 2,
            "total_bytes": 800,
            "human_size": "800 B",
        }],
    }
    execution = {
        "execution_id": "exec-test",
        "platform": "android",
        "transport": "mock",
        "started_at": "2026-11-09T12:00:00+08:00",
        "finished_at": "2026-11-09T12:00:05+08:00",
        "mode": "quarantine",
        "status": "completed",
        "quarantine_root": "/sdcard/.android-deep-clean/quarantine/exec-test",
        "device_identity": {"serial": "MOCK-1"},
        "summary": {
            "success_files": 2, "failed_files": 0, "processed_bytes": 800,
            "selected_bytes": 800, "released_bytes": 0,
            "quarantine_files": 2, "quarantine_bytes": 800,
            "quarantine_occupied_bytes": 800,
            "backup_files": 0, "backup_bytes": 0, "df_delta_bytes": -4096,
        },
        "items": [{
            "id": "app_cache-abc", "category": "app_cache", "status": "success",
            "success_count": 2, "failed_count": 0, "processed_bytes": 800, "error": "",
        }],
        "failures": [],
        "free_before": {"available_bytes": 1_000_000},
        "free_after": {"available_bytes": 995_904},
        "warnings": ["隔离模式不会立即释放空间。"],
        "next_actions": ["运行 verify 验证清理结果；"],
    }
    verification = {
        "summary": {
            "checked": 2, "passed": 2, "failed": 0,
            "source_present": 0, "quarantine_missing": 0, "free_delta": -4096,
        },
        "failures": [],
        "free_now": {"available_bytes": 996_000},
        "note": "隔离模式在 purge 前不会释放空间。",
    }
    return analysis, selection, execution, verification


def test_cleanup_html_covers_the_whole_process():
    analysis, selection, execution, verification = _cleanup_bundle()
    html = render_cleanup_html(analysis, selection, execution, verification)
    assert html.startswith("<!DOCTYPE html>")
    for section in ("清理过程", "用户选择了什么", "实际执行了什么", "验证结果",
                    "隔离区、释放与恢复", "清理前发现了什么", "剩余问题与下一步"):
        assert section in html, section
    assert 'id="process"' in html and 'id="release"' in html
    assert "已运行" in html
    assert "2 / 2" in html
    assert "尚未 purge" in html
    assert "隔离区仍占用设备空间" in html
    assert "-4.00 KB" in html, "negative free-space changes must not collapse to '-'"
    assert "分享前请自行脱敏" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "<script" not in html.lower()
    assert "src=" not in html


def test_cleanup_html_escapes_failure_paths():
    analysis, selection, execution, verification = _cleanup_bundle()
    payload = '<img src=x onerror=alert(1)>.tmp'
    execution["failures"] = [{"path": f"/sdcard/{payload}", "reason": payload, "item": "app_cache-abc"}]
    html = render_cleanup_html(analysis, selection, execution, verification)
    assert "<img" not in html
    assert "&lt;img" in html


def test_cleanup_html_without_verify_or_selection_degrades():
    analysis, _, execution, _ = _cleanup_bundle()
    html = render_cleanup_html(analysis, {}, execution, {})
    assert "尚未运行 verify" in html
    assert "没有找到选择清单" in html
    assert "清理结果报告" in html


def test_cleanup_html_zero_checks_is_not_reported_as_passed():
    analysis, selection, execution, _ = _cleanup_bundle()
    verification = {
        "summary": {
            "checked": 0, "passed": 0, "failed": 0,
            "source_present": 0, "quarantine_missing": 0, "free_delta": 0,
        },
        "failures": [],
        "note": "",
    }
    html = render_cleanup_html(analysis, selection, execution, verification)
    assert "没有可验证项目" in html
    assert "这不等于验证通过" in html
    assert "所有检查项通过" not in html


def test_cleanup_html_has_mobile_layout_rules():
    analysis, selection, execution, verification = _cleanup_bundle()
    html = render_cleanup_html(analysis, selection, execution, verification)
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in html
    assert "@media (max-width:640px)" in html
    assert "@media (max-width:340px)" in html
    assert "overflow-x:auto" in html
    assert "width:max-content" in html
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in html
    assert "overflow-wrap:anywhere" in html
    assert "flex-wrap:nowrap" in html


def test_analysis_and_cleanup_share_the_same_responsive_css():
    analysis, selection, execution, verification = _cleanup_bundle()
    analysis_html = render_analysis_html(analysis, {})
    cleanup_html = render_cleanup_html(analysis, selection, execution, verification)
    assert "@media (max-width:640px)" in analysis_html
    assert "overflow-x:auto" in analysis_html
    assert "width:max-content" in analysis_html
    analysis_css = analysis_html.split("<style>", 1)[1].split("</style>", 1)[0]
    cleanup_css = cleanup_html.split("<style>", 1)[1].split("</style>", 1)[0]
    assert analysis_css == cleanup_css


def test_execute_and_verify_write_cleanup_html(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file(CACHE + "/b.bin", size=500)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    env.execute_ok()

    path = env.state_path("execution/report-cleanup.html")
    assert path.exists()
    html = path.read_text(encoding="utf-8")
    assert "清理结果报告" in html
    assert "清理过程" in html
    assert "尚未运行 verify" in html
    assert "成功 2 个" in html
    assert "http://" not in html
    assert env.state_json("state.json")["files"]["cleanup_report_html"].endswith("report-cleanup.html")

    env.ok("verify")
    html = path.read_text(encoding="utf-8")
    assert "验证结果" in html
    assert "所有检查项通过" in html
    assert "2 / 2" in html
    assert "尚未运行 verify" not in html


def test_cleanup_html_reports_purge(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file(CACHE + "/b.bin", size=500)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    env.execute_ok()
    token = str(env.execution.get("purge_token") or "")
    assert token
    env.run(["quarantine", "purge", "--token", token]).expect(0)

    html = env.state_path("execution/report-cleanup.html").read_text(encoding="utf-8")
    assert "永久释放（purge）" in html
    assert "已永久释放" in html
    assert "永久删除" in html
    assert "无法恢复" in html


def test_cleanup_html_reports_restore(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file(CACHE + "/b.bin", size=500)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    env.execute_ok()
    env.ok("restore", "--all")

    html = env.state_path("execution/report-cleanup.html").read_text(encoding="utf-8")
    assert "恢复（restore）" in html
    assert "已恢复" in html
    assert "2 个" in html
    assert env.phone.exists(CACHE + "/a.bin")
