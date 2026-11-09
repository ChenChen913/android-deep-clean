"""HTML report: self-contained, escaped, offline, read-only."""

from __future__ import annotations

import re

from adc_lib.html_report import render_analysis_html

CAMERA = "/sdcard/DCIM/Camera"


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
