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


def test_split_apk_paths_are_summed(env):
    split = "/data/app/com.installed.app/split_config.arm64_v8a.apk"
    env.phone.file(APK, size=500)
    env.phone.file(split, size=200)
    env.phone.file(DATA + "/cache/a.bin", size=300)
    env.set_packages(
        installed=[PKG],
        uninstalled=[],
        system=["android"],
        available=True,
        apk_paths={PKG: [APK, split]},
        versions={PKG: "1.0"},
    )
    env.prepare_analysis()
    app = _app(env)
    assert app["known"]["apk_base_bytes"] == 500
    assert app["known"]["apk_split_bytes"] == 200
    assert app["known"]["apk_bytes"] == 700
    assert app["known"]["apk_file_count"] == 2
    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    assert manifest["apk"]["split_status"] == "collected"
    assert manifest["packages"][0]["apk_file_count"] == 2
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "Split APK" in report


def test_root_internal_data_is_collected_read_only(env):
    _setup(env)
    env.phone.file("/data/data/com.installed.app/databases/app.db", size=1000)
    env.phone.file("/data/data/com.installed.app/cache/c.bin", size=400)
    env.phone.file("/data/data/com.installed.app/code_cache/d.bin", size=200)
    env.set_caps(privilege="root")
    env.prepare_analysis()
    app = _app(env)
    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    assert manifest["internal"]["status"] == "collected"
    assert manifest["internal"]["data_bytes"] >= 1600
    assert app["known"]["internal_data_bytes"] >= 1600
    assert app["known"]["internal_cache_bytes"] >= 400
    assert "只读 du" in app["unknown"]["internal_data"]
    assert app["capabilities"]["root"] is True
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "内部 data / cache" in report


def test_root_permission_denied_is_not_reported_as_zero(env):
    _setup(env)
    env.set_caps(privilege="root", inaccessible=["/data/data"])
    env.prepare_analysis()
    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    assert manifest["internal"]["status"] == "permission_denied"
    assert manifest["packages"][0]["internal_status"] == "permission_denied"
    assert manifest["packages"][0]["internal_data_bytes"] is None
    app = _app(env)
    assert app["unknown"]["internal_data"].startswith("不可读")


def test_apk_permission_denied_is_explicit(env):
    _setup(env)
    env.set_caps(inaccessible=["/data/app"])
    env.prepare_analysis()
    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    entry = manifest["packages"][0]
    assert entry["status"] == "permission_denied"
    assert entry["apk_bytes"] is None
    assert manifest["status"] in {"partial", "unavailable"}
    assert any("权限" in item or "permission" in item.lower() for item in manifest["limitations"])


# ---------------------------------------------------------------- internal status
# du prints a total for whatever it could read.  A permission error or an
# unexplained non-zero exit must therefore downgrade the app, never be reported
# as a complete measurement.


def test_internal_partial_permission_keeps_data_but_marks_partial(env):
    _setup(env)
    env.phone.file("/data/data/com.installed.app/databases/app.db", size=1000)
    env.phone.file("/data/data/com.installed.app/cache/c.bin", size=400)
    env.set_caps(privilege="root", inaccessible=["/data/data/com.installed.app/cache"])
    env.prepare_analysis()

    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    entry = manifest["packages"][0]
    assert entry["internal_status"] == "partial", "读到部分数据不等于完整采集"
    assert entry["internal_data_bytes"] is not None, "已读到的有效数据必须保留"
    assert "权限" in entry["internal_error"] or "Permission" in entry["internal_error"]
    assert manifest["internal"]["status"] == "partial"
    assert any("下限" in item for item in manifest["limitations"])


def test_internal_command_failure_without_data_is_unavailable(env):
    _setup(env)
    env.set_caps(
        privilege="root",
        du_exit_code=1,
        du_error="du: cannot access '/data/data/com.installed.app': Input/output error",
    )
    env.prepare_analysis()

    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    entry = manifest["packages"][0]
    assert entry["internal_status"] == "unavailable"
    assert entry["internal_data_bytes"] is None
    assert manifest["internal"]["status"] == "unavailable"


def test_internal_command_failure_with_permission_is_denied(env):
    _setup(env)
    env.set_caps(
        privilege="root",
        du_exit_code=1,
        du_error="du: cannot access '/data/data/com.installed.app': Permission denied",
    )
    env.prepare_analysis()

    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    entry = manifest["packages"][0]
    assert entry["internal_status"] == "permission_denied"
    assert entry["internal_data_bytes"] is None
    assert manifest["internal"]["status"] == "permission_denied"


def test_internal_missing_optional_subdirs_stays_collected(env):
    """A missing cache/code_cache is normal and must not fake a partial result."""
    _setup(env)
    env.phone.file("/data/data/com.installed.app/databases/app.db", size=1000)
    env.set_caps(
        privilege="root",
        du_exit_code=1,
        du_error="du: cannot access '/data/data/com.installed.app/cache': No such file or directory",
    )
    env.prepare_analysis()

    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    entry = manifest["packages"][0]
    assert entry["internal_status"] == "collected"
    assert entry["internal_data_bytes"] is not None
    assert manifest["internal"]["status"] == "collected"


# ---------------------------------------------------------------- base vs split APK
# A readable split APK must never be counted as the base APK.


def test_unreadable_base_apk_is_not_replaced_by_a_split(env):
    split = "/data/app/com.installed.app/split_config.arm64_v8a.apk"
    env.phone.file(APK, size=500)
    env.phone.file(split, size=200)
    env.set_packages(
        installed=[PKG], uninstalled=[], system=["android"], available=True,
        apk_paths={PKG: [APK, split]}, versions={PKG: "1.0"},
    )
    env.set_caps(inaccessible=[APK])
    env.prepare_analysis()

    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    entry = manifest["packages"][0]
    assert entry["apk_base_bytes"] is None, "基础 APK 不可读时必须标记未知，不能用 split 顶替"
    assert entry["apk_split_bytes"] == 200
    assert entry["apk_bytes"] == 200
    assert entry["status"] == "partial"
    assert any("基础 APK 无法读取" in item for item in manifest["limitations"])
    app = _app(env)
    assert app["known"]["apk_base_bytes"] == 0
    assert "合计为下限" in app["unknown"]["apk_base"]
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "合计为下限" in report
    html = (env.state_dir / "analysis" / "report-before.html").read_text(encoding="utf-8")
    assert "基础 APK：1 个应用的基础 APK 不可读" in html


def test_split_only_package_keeps_base_unknown(env):
    splits = [
        "/data/app/com.installed.app/split_config.arm64_v8a.apk",
        "/data/app/com.installed.app/split_config.xxhdpi.apk",
    ]
    for index, path in enumerate(splits):
        env.phone.file(path, size=100 + index * 100)
    env.set_packages(
        installed=[PKG], uninstalled=[], system=["android"], available=True,
        apk_paths={PKG: splits}, versions={PKG: "1.0"},
    )
    env.prepare_analysis()

    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    entry = manifest["packages"][0]
    assert entry["apk_base_bytes"] is None
    assert entry["apk_split_bytes"] == 300
    assert entry["apk_bytes"] == 300
    assert entry["apk_file_count"] == 2
    assert entry["status"] == "partial"


def test_legacy_single_apk_without_base_name_is_the_base(env):
    legacy = "/data/app/com.installed.app/com.installed.app-1.apk"
    env.phone.file(legacy, size=500)
    env.set_packages(
        installed=[PKG], uninstalled=[], system=["android"], available=True,
        apk_paths={PKG: [legacy]}, versions={PKG: "1.0"},
    )
    env.prepare_analysis()

    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    entry = manifest["packages"][0]
    assert entry["apk_base_bytes"] == 500
    assert entry["apk_split_bytes"] == 0
    assert entry["status"] == "ok"


def test_vendor_bundle_without_base_name_is_recognised(env):
    """Huawei system bundles list Settings.apk first, then feature splits.

    ``pm path`` prints the base first, so the first listed file is the base even
    when the vendor did not name it base.apk.  Splits must still be separated.
    """
    base_apk = "/system/priv-app/Settings/Settings.apk"
    split_apk = "/system/priv-app/Settings/Resolution.apk"
    env.phone.file(base_apk, size=1000)
    env.phone.file(split_apk, size=200)
    env.set_packages(
        installed=[PKG], uninstalled=[], system=["android"], available=True,
        apk_paths={PKG: [base_apk, split_apk]}, versions={PKG: "1.0"},
    )
    env.prepare_analysis()

    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    entry = manifest["packages"][0]
    assert entry["apk_base_bytes"] == 1000
    assert entry["apk_split_bytes"] == 200
    assert entry["apk_bytes"] == 1200
    assert entry["status"] == "ok"


# ---------------------------------------------------------------- internal cache
# A missing or empty cache is a real 0 B; an unreadable one is unknown and must
# never be turned into a fake 0 B by the collector, the index or the reports.


def _internal_entry(env) -> dict:
    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    return manifest["packages"][0]


def test_internal_cache_with_data_is_measured(env):
    _setup(env)
    env.phone.file("/data/data/com.installed.app/cache/c.bin", size=400)
    env.set_caps(privilege="root")
    env.prepare_analysis()

    entry = _internal_entry(env)
    assert entry["internal_cache_bytes"] >= 400
    assert entry["internal_code_cache_bytes"] == 0, "目录不存在时才是真正的 0"
    assert entry["internal_status"] == "collected"


def test_internal_cache_absent_is_zero(env):
    _setup(env)
    env.phone.file("/data/data/com.installed.app/databases/app.db", size=1000)
    env.set_caps(privilege="root")
    env.prepare_analysis()

    entry = _internal_entry(env)
    assert entry["internal_cache_bytes"] == 0
    assert entry["internal_code_cache_bytes"] == 0
    assert entry["internal_status"] == "collected"


def test_internal_cache_present_but_empty_is_zero(env):
    _setup(env)
    env.phone.file("/data/data/com.installed.app/databases/app.db", size=1000)
    env.phone.dir("/data/data/com.installed.app/cache")
    env.set_caps(privilege="root")
    env.prepare_analysis()

    entry = _internal_entry(env)
    assert entry["internal_cache_bytes"] == 0
    assert entry["internal_status"] == "collected"


def test_internal_cache_unreadable_is_unknown_not_zero(env):
    _setup(env)
    env.phone.file("/data/data/com.installed.app/databases/app.db", size=1000)
    env.phone.file("/data/data/com.installed.app/cache/c.bin", size=400)
    env.set_caps(privilege="root", inaccessible=["/data/data/com.installed.app/cache"])
    env.prepare_analysis()

    entry = _internal_entry(env)
    assert entry["internal_cache_bytes"] is None, "权限不足不能写成 0 B"
    assert entry["internal_status"] == "partial"
    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    assert manifest["internal"]["cache_bytes"] is None, "汇总层也不能把未知写成 0"
    assert manifest["internal"]["cache_unknown"] == 1
    app = _app(env)
    assert app["known"]["internal_cache_human"] == "未知"
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "含内部 cache 未知" in report
    assert "du 未返回 cache 大小" in report
    html = (env.state_dir / "analysis" / "report-before.html").read_text(encoding="utf-8")
    assert "du 未返回 cache 大小" in html


def test_internal_code_cache_unreadable_keeps_cache_known(env):
    _setup(env)
    env.phone.file("/data/data/com.installed.app/databases/app.db", size=1000)
    env.phone.file("/data/data/com.installed.app/cache/c.bin", size=400)
    env.phone.file("/data/data/com.installed.app/code_cache/d.bin", size=200)
    env.set_caps(privilege="root", inaccessible=["/data/data/com.installed.app/code_cache"])
    env.prepare_analysis()

    entry = _internal_entry(env)
    assert entry["internal_cache_bytes"] >= 400
    assert entry["internal_code_cache_bytes"] is None
    assert entry["internal_status"] == "partial", "code_cache 不可读时不能标为完整采集"
    manifest = json.loads(env.state_path("scan/aux/app-storage.json").read_text(encoding="utf-8"))
    assert manifest["internal"]["cache_bytes"] >= 400
    # cache 本身可读，未知的是 code_cache；它只在逐应用字段里记 NULL。
    assert manifest["internal"]["cache_unknown"] == 0
