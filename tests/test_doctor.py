"""doctor: device/capability probing must be accurate, honest and read-only."""

from __future__ import annotations

import pytest

from adc_testkit import DEFAULT_CAPS


def test_doctor_reports_device_packages_and_capabilities(env):
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/a.bin", size=120)
    env.phone.dir("/sdcard/Android/obb")
    env.phone.dir("/data/data/com.installed.app/cache")
    env.set_caps(privilege="root", commands={"su": True})

    before = env.snapshot()
    report = env.ok("doctor").json()
    env.assert_unchanged(before)

    assert report["schema"] == 1
    assert report["transport"] == "mock"
    device = report["device"]
    assert device["serial"] == "MOCK-ADC-0001"
    assert device["manufacturer"] == "MockCorp"
    assert device["model"] == "MockPhone"
    assert device["sdk"] == 34
    assert report["android"] == "Android 14 (API 34)"

    caps = report["capabilities"]
    assert caps["privilege"] == "root"
    assert caps["tier"] == "full"
    assert caps["sdcard_read"] is True
    assert caps["sdcard_write"] is True
    assert caps["android_data_read"] is True
    assert caps["android_obb_read"] is True
    assert caps["internal_data_read"] is True
    assert caps["package_manager"] is True
    assert caps["hashing"] is True
    assert caps["quarantine_usable"] is True
    for name in ("find", "stat", "xargs", "sha256sum", "pm", "df"):
        assert caps["commands"][name] is True, name

    assert report["packages"]["installed_count"] == 1
    assert report["packages"]["uninstalled_count"] == 0
    assert report["packages"]["available"] is True

    storage = {entry["path"]: entry for entry in report["storage"]}
    assert storage["/sdcard"]["total_bytes"] == 128_000_000_000
    assert storage["/sdcard"]["used_bytes"] == 90_000_000_000
    assert storage["/sdcard"]["available_bytes"] == 38_000_000_000

    assert report["recommended_roots"] == ["/sdcard", "internal-cache"]
    assert report["probes"]["/sdcard"]["exists"] is True
    assert any("mock 模拟设备" in item for item in report["limitations"])


def test_doctor_degrades_when_permissions_and_tools_are_missing(env):
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/hidden.bin", size=300)
    env.phone.file("/sdcard/Download/visible.bin", size=200)
    env.set_caps(
        privilege="shell",
        inaccessible=["/sdcard/Android/data", "/sdcard/Android/obb"],
        commands={"sha256sum": False, "md5sum": False, "find": False},
    )

    report = env.ok("doctor").json()
    caps = report["capabilities"]

    # /sdcard readable + Android/data unreadable => limited tier.
    assert caps["tier"] == "limited"
    assert caps["privilege"] == "shell"
    assert caps["sdcard_read"] is True
    assert caps["android_data_read"] is False
    assert caps["android_obb_read"] is False
    assert caps["internal_data_read"] is False
    assert caps["hashing"] is False
    assert caps["quarantine_usable"] is True

    limitations = "\n".join(report["limitations"])
    assert "/sdcard/Android/data" in limitations
    assert "/sdcard/Android/obb" in limitations
    assert "sha256sum/md5sum" in limitations
    assert "find/stat/xargs" in limitations
    assert "scoped storage" in limitations

    probe = report["probes"]["/sdcard/Android/data"]
    assert probe["exists"] is True
    assert probe["readable"] is False
    assert probe["listable"] is False
    assert "mock" in probe["error"]


def test_doctor_writes_json_and_markdown_reports(env):
    report = env.ok("doctor").json()
    assert env.doctor["device"]["serial"] == report["device"]["serial"]
    report_path = env.state_path("doctor/report-doctor.md")
    assert report_path.exists()
    markdown = report_path.read_text(encoding="utf-8")
    assert "# Android 存储体检报告" in markdown
    assert "权限级别" in markdown
    assert "只读" in markdown
    assert "/sdcard" in markdown
