"""HarmonyOS platform detection, guards and mock end-to-end coverage."""

from __future__ import annotations

import json

from adc_lib.models import DeviceInfo
from adc_lib.platform import (
    ANDROID,
    HARMONY_COMPAT,
    HARMONY_NEXT,
    HARMONY_PUBLIC_ROOT,
    detect_platform,
)
from adc_lib.rules import guard_path

PUBLIC = HARMONY_PUBLIC_ROOT


def _harmony_env(env):
    env.device.update({
        "serial": "MOCK-HOS-0001",
        "sdk": 0,
        "release": "",
        "manufacturer": "HUAWEI",
        "model": "MockMate",
        "platform": "harmonyos_next",
        "os_version": "5.0.0",
        "api_version": "12",
        "distribution": "HarmonyOS",
        "harmony_version": "5.0.0",
        "shell": "hdc",
    })
    env.set_caps(
        privilege="shell",
        inaccessible=[],
        commands={"find": True, "stat": True, "xargs": True, "sha256sum": True,
                  "df": True, "rm": True, "mv": True, "rmdir": True, "bm": True},
    )
    env.set_packages(installed=["com.example.notes"], uninstalled=[], system=[], available=True)
    env.flush()
    return env


def test_platform_detection_keeps_android_and_splits_harmony():
    assert detect_platform(DeviceInfo(sdk=34, release="14")) == ANDROID
    assert detect_platform(DeviceInfo(sdk=34, release="12", harmony_version="4.2.0")) == HARMONY_COMPAT
    assert detect_platform(DeviceInfo(sdk=0, platform="harmonyos_next", api_version="12")) == HARMONY_NEXT
    assert detect_platform(DeviceInfo(sdk=0, os_version="5.0.0", distribution="HarmonyOS")) == HARMONY_NEXT


def test_harmony_guard_only_allows_public_storage():
    allowed = guard_path(PUBLIC + "/Docs/Download/old.zip", "archives", kind="file", platform=HARMONY_NEXT)
    assert allowed["allowed"] is True
    assert allowed["risk_floor"] == "CAUTION"
    for blocked in (
        "/data/app/el2/100/base/com.example/cache/a.bin",
        "/storage/media/100/local/files/Docs/Download/secret.db",
        "/storage/media/100/local/files/Docs/Download/.nomedia",
        PUBLIC,
    ):
        result = guard_path(blocked, "archives", kind="file", platform=HARMONY_NEXT)
        assert result["allowed"] is False, blocked


def test_harmony_next_mock_full_flow(env):
    _harmony_env(env)
    env.phone.file(PUBLIC + "/Docs/Download/old.zip", size=800, age_days=900)
    env.phone.file(PUBLIC + "/Docs/Download/setup.apk", size=700, age_days=900)
    env.phone.file(PUBLIC + "/Docs/Download/old.log", size=300, age_days=900)
    env.phone.file(PUBLIC + "/Docs/Download/secret.db", size=120, age_days=900)
    env.phone.file(PUBLIC + "/Docs/Download/.nomedia", content="")
    dup = b"H" * (2 * 1024 * 1024)
    env.phone.file(PUBLIC + "/Docs/Download/dup_a.bin", content=dup)
    env.phone.file(PUBLIC + "/Docs/Download/dup_b.bin", content=dup)
    env.phone.dir(PUBLIC + "/Docs/Download/empty_sub", age_days=40)
    env.phone.file("/data/app/el2/100/base/com.example/cache/private.bin", size=500)

    doctor = env.ok("doctor").json()
    assert doctor["platform"] == HARMONY_NEXT
    assert doctor["capabilities"]["public_read"] is True
    assert doctor["capabilities"]["android_data_read"] is False
    assert doctor["capabilities"]["supports_shizuku"] is False
    assert doctor["capabilities"]["supports_termux"] is False
    assert doctor["capabilities"]["supports_bm_clean"] is True
    assert any("bm clean" in item for item in doctor["limitations"])

    scan = env.ok("scan").json()
    assert scan["platform"] == HARMONY_NEXT
    assert scan["roots_scanned"] == [PUBLIC]

    analysis = env.ok("analyze").json()
    assert analysis["platform"] == HARMONY_NEXT
    paths = {
        path
        for item in analysis["items"]
        for path in item.get("sample_paths") or []
    }
    assert PUBLIC + "/Docs/Download/old.zip" in paths
    assert PUBLIC + "/Docs/Download/secret.db" not in paths
    assert PUBLIC + "/Docs/Download/.nomedia" not in paths
    assert not any(path.startswith("/sdcard/") for path in paths)
    assert not any(path.startswith("/data/app/") for path in paths)
    assert analysis["summary"]["categories"]

    review = env.write_review()
    assert review is not None
    plan = env.plan_now(review)
    assert plan["platform"] == HARMONY_NEXT
    selection = env.select("--categories", "archives,temp_logs").json()
    assert selection["platform"] == HARMONY_NEXT

    result = env.execute()
    result.expect(0)
    execution = result.json()
    assert execution["platform"] == HARMONY_NEXT
    assert execution["status"] == "completed"
    assert execution["quarantine_root"].startswith(PUBLIC + "/Docs/.android-deep-clean/quarantine/")
    assert not env.phone.exists(PUBLIC + "/Docs/Download/old.zip")
    assert env.phone.exists(PUBLIC + "/Docs/Download/secret.db")
    assert env.phone.exists("/data/app/el2/100/base/com.example/cache/private.bin")

    verification = env.ok("verify").json()
    assert verification["summary"]["failed"] == 0

    restored = env.ok("restore", "--all").json()
    assert restored.get("restored_files", 0) > 0
    assert restored.get("failed", restored.get("failed_files", 0)) == 0
    assert env.phone.exists(PUBLIC + "/Docs/Download/old.zip")
