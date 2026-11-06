"""Unit tests: device identity binding and its coverage in plan/selection hashes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from adc_lib.config import load_config
from adc_lib.device import device_identity, transport_device_identity
from adc_lib.executor import _recompute_plan_hash, _recompute_selection_hash
from adc_lib.models import DeviceInfo
from adc_lib.planner import build_plan, build_selection
from adc_lib.util import compute_plan_hash, compute_selection_hash


def _device(serial="R58M12345", **overrides):
    base = dict(
        serial=serial,
        sdk=34,
        release="14",
        manufacturer="MockCorp",
        model="MockPhone",
        fingerprint="mock/MockPhone/mock:14/UP1A/1:userdebug/test-keys",
    )
    base.update(overrides)
    return DeviceInfo(**base)


def _analysis(identity_id="device-aaaa1111bb", **identity_overrides):
    identity = {
        "id": identity_id,
        "serial": "R58M12345",
        "manufacturer": "MockCorp",
        "model": "MockPhone",
        "sdk": 34,
        "android": "Android 14 (API 34)",
        "fingerprint": "mock/fp",
        "hash_material_sha256": "0" * 64,
    }
    identity.update(identity_overrides)
    return {
        "analysis_id": "analysis-test0001",
        "device_identity": identity,
        "items": [
            {
                "id": "temp_logs-test0001",
                "category": "temp_logs",
                "category_title": "临时文件与日志",
                "title": "临时文件/日志：/sdcard/Misc/logs",
                "risk": "SAFE",
                "action": "delete_files",
                "primary_path": "/sdcard/Misc/logs/a.log",
                "sample_paths": ["/sdcard/Misc/logs/a.log"],
                "total_bytes": 10,
                "file_count": 1,
                "dir_count": 0,
                "confidence": 0.8,
                "ai_review_required": False,
                "selectable": True,
                "evidence": [],
                "reasons": [],
                "keep_suggestion": "",
                "app_pkg": "",
                "app_kind": "",
                "notes": "",
            }
        ],
        "limitations": [],
    }


def _select_args(**overrides):
    base = dict(
        ids="",
        categories="",
        app_kinds="",
        risk="",
        all_safe=True,
        exclude_ids="",
        exclude_categories="",
        mode="quarantine",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- device_identity
def test_device_identity_is_stable_and_complete():
    first = device_identity(_device())
    second = device_identity(_device())
    assert first == second
    assert first["id"].startswith("device-")
    assert first["serial"] == "R58M12345"
    assert first["manufacturer"] == "MockCorp"
    assert first["model"] == "MockPhone"
    assert first["sdk"] == 34
    assert first["android"] == "Android 14 (API 34)"
    assert len(first["hash_material_sha256"]) == 64


def test_device_identity_strips_serial_and_handles_missing_serial():
    identity = device_identity(_device(serial="  R58M12345  "))
    assert identity["serial"] == "R58M12345"
    missing = device_identity(_device(serial=""))
    assert missing["serial"] == "unknown"
    assert missing["id"] != identity["id"]


def test_device_identity_changes_with_identity_material():
    base_id = device_identity(_device())["id"]
    for overrides in (
        {"serial": "OTHER-SERIAL"},
        {"fingerprint": "other/fp"},
        {"manufacturer": "OtherCorp"},
        {"model": "OtherPhone"},
        {"sdk": 33},
    ):
        assert device_identity(_device(**overrides))["id"] != base_id, overrides
    # release/android label is display-only: must not change identity
    assert device_identity(_device(release="15"))["id"] == base_id


def test_device_identity_within_same_device_but_different_serial():
    a = device_identity(_device(serial="AAA"))
    b = device_identity(_device(serial="BBB"))
    assert a["hash_material_sha256"] != b["hash_material_sha256"]


def test_transport_identity_applies_platform_detection():
    """execute must hash the same identity doctor does on HarmonyOS devices."""
    def build(platform=""):
        return DeviceInfo(
            serial="HONOR123", sdk=29, release="10", manufacturer="HUAWEI",
            model="HLK-AL00", fingerprint="honor/fp", platform=platform,
            harmony_version="3.0.0", os_version="3.0.0",
        )

    transport = SimpleNamespace(device_info=lambda: build(""))
    detected = transport_device_identity(transport)
    assert detected["platform"] == "harmonyos_compat"
    assert detected["id"] == device_identity(build("harmonyos_compat"))["id"]


# ---------------------------------------------------------------- plan hash
def test_plan_hash_covers_device_identity():
    analysis = _analysis()
    config = load_config()
    plan = build_plan(analysis, {"present": False, "decisions": {}}, config, "/tmp/a.json")
    assert plan["device_identity"]["id"] == "device-aaaa1111bb"
    assert _recompute_plan_hash(plan) == plan["plan_hash"] == compute_plan_hash(plan)

    tampered = json.loads(json.dumps(plan))
    tampered["device_identity"]["id"] = "device-evil000000"
    assert _recompute_plan_hash(tampered) != plan["plan_hash"]
    assert compute_plan_hash(tampered) != plan["plan_hash"]


def test_plan_hash_differs_between_devices_even_with_same_items():
    config = load_config()
    plan_a = build_plan(_analysis("device-aaaa1111bb"), {"present": False, "decisions": {}}, config, "/tmp/a.json")
    plan_b = build_plan(_analysis("device-bbbb2222cc"), {"present": False, "decisions": {}}, config, "/tmp/a.json")
    assert plan_a["plan_hash"] != plan_b["plan_hash"]
    assert plan_a["plan_id"] != plan_b["plan_id"]


# ---------------------------------------------------------------- selection hash
def test_selection_hash_covers_device_identity():
    config = load_config()
    plan = build_plan(_analysis(), {"present": False, "decisions": {}}, config, "/tmp/a.json")
    selection = build_selection(plan, config, _select_args())
    assert selection["device_identity"]["id"] == plan["device_identity"]["id"]
    assert _recompute_selection_hash(selection) == selection["selection_hash"] == compute_selection_hash(selection)

    tampered = json.loads(json.dumps(selection))
    tampered["device_identity"]["id"] = "device-evil000000"
    assert _recompute_selection_hash(tampered) != selection["selection_hash"]


def test_selection_from_different_device_plan_differs():
    config = load_config()
    selection_a = build_selection(
        build_plan(_analysis("device-aaaa1111bb"), {"present": False, "decisions": {}}, config, "/tmp/a.json"),
        config,
        _select_args(),
    )
    selection_b = build_selection(
        build_plan(_analysis("device-bbbb2222cc"), {"present": False, "decisions": {}}, config, "/tmp/a.json"),
        config,
        _select_args(),
    )
    assert selection_a["device_identity"]["id"] != selection_b["device_identity"]["id"]
    assert selection_a["plan_hash"] != selection_b["plan_hash"]
    assert selection_a["selection_hash"] != selection_b["selection_hash"]
