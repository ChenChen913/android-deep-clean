"""App-level storage: partial, read-only, evidence only."""

from __future__ import annotations

import json

PKG = "com.installed.app"
APK = "/data/app/com.installed.app/base.apk"
DATA = "/sdcard/Android/data/com.installed.app"
MEDIA = "/sdcard/Android/media/com.installed.app"
OBB = "/sdcard/Android/obb/com.installed.app"


def _setup(env, *, apk_size: int = 500):
    env.phone.file(APK, size=apk_size)
    env.phone.file(DATA + "/cache/a.bin", size=300)
    env.phone.file(DATA + "/files/b.bin", size=200)
    env.phone.file(MEDIA + "/m.bin", size=700)
    env.phone.file(OBB + "/o.bin", size=900)
    env.set_packages(
        installed=[PKG],
        uninstalled=[],
        system=["android"],
        available=True,
        apk_paths={PKG: APK},
        versions={PKG: "1.0"},
    )


def _app(env) -> dict:
    return env.analysis["summary"]["app_storage"]


def _signature(env) -> list[tuple]:
    return sorted(
        (item["id"], item["category"], item["risk"], item["selectable"],
         item["file_count"], item["total_bytes"], item["files_digest"])
        for item in env.analysis.get("items") or []
    )


def test_app_storage_known_and_unknown_scopes(env):
    _setup(env)
    analysis = env.prepare_analysis()
    app = _app(env)
    assert app["status"] == "collected"
    assert app["packages"]["installed"] == 1
    assert app["known"]["apk_bytes"] == 500
    assert app["known"]["external_data_bytes"] == 500
    assert app["known"]["external_media_bytes"] == 700
    assert app["known"]["external_obb_bytes"] == 900
    assert app["known"]["external_cache_bytes"] == 300
    assert app["known"]["external_total_bytes"] == 2100
    assert app["known"]["total_bytes"] == 2600
    assert app["unknown"]["internal_data"].startswith("不可读")
    assert app["top_external"][0]["package"] == PKG

    conn = env.analysis_db_connection()
    try:
        row = conn.execute("SELECT * FROM app_storage WHERE package=?", (PKG,)).fetchone()
        source = conn.execute("SELECT status FROM source_status WHERE name='app_storage'").fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["apk_bytes"] == 500
    assert row["total_known_bytes"] == 2600
    assert source is not None and source[0] == "collected"

    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "应用级存储（部分口径）" in report
    assert "内部 data / cache" in report
    assert "不能与设备已用空间相加" in report


def test_app_cache_evidence_contains_app_storage(env):
    _setup(env)
    env.prepare_analysis()
    item = env.require_item("app_cache")
    evidence = {entry["code"]: entry["text"] for entry in item["evidence"]}
    assert "app_storage" in evidence
    assert "基础 APK 500 B" in evidence["app_storage"]
    assert "外部 data 500 B" in evidence["app_storage"]
    assert "内部 data/cache 不可读" in evidence["app_storage"]


def test_uninstalled_app_with_external_data_is_reported(env):
    env.set_packages(installed=[], uninstalled=["com.gone.app"], available=True)
    env.phone.file("/sdcard/Android/data/com.gone.app/cache/left.bin", size=400)
    env.prepare_analysis()
    app = _app(env)
    assert app["packages"]["residue"] == 1
    assert any(item["package"] == "com.gone.app" for item in app["residue"])
    item = env.require_item("residue")
    assert any(entry["code"] == "app_storage" for entry in item["evidence"])
    assert any("包未安装" in entry["text"] for entry in item["evidence"])
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "未安装应用仍保留的外部目录" in report


def test_no_app_storage_disables_apk_collection(env):
    _setup(env)
    env.ok("doctor")
    env.ok("scan", "--no-app-storage")
    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "disabled"
    assert manifest["items"] == 0
    analysis = env.ok("analyze").json()
    app = analysis["summary"]["app_storage"]
    assert app["status"] == "disabled"
    assert app["available"] is False
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "应用级存储（部分口径）" in report
    assert "不可用" in report or "未采集" in report


def test_app_storage_unavailable_does_not_break_scan(env):
    env.set_packages(installed=[PKG], available=False)
    env.ok("doctor")
    meta = env.ok("scan").json()
    app = meta["aux"]["app_storage"]
    assert app["status"] == "unavailable"
    analysis = env.ok("analyze").json()
    assert analysis["summary"]["app_storage"]["status"] in {"unavailable", "partial"}
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "应用级存储（部分口径）" in report


def test_app_storage_never_changes_candidates(env):
    _setup(env)
    env.prepare_analysis()
    baseline = _signature(env)
    env.ok("scan", "--no-app-storage")
    env.ok("analyze")
    after_disable = _signature(env)
    assert baseline == after_disable
    env.ok("scan")
    env.ok("analyze")
    after_enable = _signature(env)
    assert baseline == after_enable
    assert not [item for item in env.analysis["items"] if item["category"] == "app_storage"]
