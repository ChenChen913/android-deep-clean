"""Unit tests: plan building, hash/token integrity, review gating."""

from __future__ import annotations

import json
import re

import pytest

from adc_lib.executor import _recompute_plan_hash, _recompute_selection_hash
from adc_lib.planner import build_plan
from adc_lib.util import (
    canonical_json,
    compute_plan_hash,
    random_token,
    selection_hash_payload,
    sha256_text,
)


def _synthetic_analysis(*, risk="CAUTION", review_required=True):
    return {
        "analysis_id": "analysis-test0001",
        "items": [
            {
                "id": "apk-test0001",
                "category": "apk",
                "category_title": "旧安装包",
                "title": "旧安装包：/sdcard/Download",
                "risk": risk,
                "action": "delete_files",
                "primary_path": "/sdcard/Download/legacy.apk",
                "sample_paths": ["/sdcard/Download/legacy.apk"],
                "total_bytes": 1500,
                "file_count": 1,
                "dir_count": 0,
                "confidence": 0.75,
                "ai_review_required": review_required,
                "selectable": True,
                "evidence": [],
                "reasons": [],
                "keep_suggestion": "",
                "app_pkg": "",
                "app_kind": "",
                "notes": "",
            }
        ],
    }


def test_build_plan_blocks_unreviewed_caution():
    plan = build_plan(_synthetic_analysis(), {"present": False, "decisions": {}}, {}, "/tmp/a.json")
    item = plan["items"][0]
    assert item["selectable"] is False
    assert item["review_status"] == "missing"
    assert "复核" in item["selectable_reason"]
    assert plan["summary"]["selectable_groups"] == 0
    assert plan["summary"]["review_required"] == 1


def test_build_plan_allows_reviewed_trash_caution():
    review = {
        "present": True,
        "path": "/tmp/review.json",
        "decisions": {"apk-test0001": {"id": "apk-test0001", "verdict": "trash", "risk": "CAUTION", "reason": "人工确认"}},
    }
    plan = build_plan(_synthetic_analysis(), review, {}, "/tmp/a.json")
    item = plan["items"][0]
    assert item["selectable"] is True
    assert item["review_status"] == "reviewed"
    assert item["ai_verdict"] == "trash"


@pytest.mark.parametrize("verdict", ["keep", "uncertain"])
def test_build_plan_blocks_keep_and_uncertain(verdict):
    review = {
        "present": True,
        "decisions": {"apk-test0001": {"id": "apk-test0001", "verdict": verdict, "risk": "CAUTION", "reason": "保留"}},
    }
    plan = build_plan(_synthetic_analysis(), review, {}, "/tmp/a.json")
    assert plan["items"][0]["selectable"] is False
    assert verdict in plan["items"][0]["selectable_reason"]


def test_build_plan_dangerous_never_selectable_and_plan_hash_recomputable():
    analysis = _synthetic_analysis(risk="DANGEROUS", review_required=False)
    plan = build_plan(analysis, {"present": False, "decisions": {}}, {}, "/tmp/a.json")
    assert plan["items"][0]["selectable"] is False
    assert "DANGEROUS" in plan["items"][0]["selectable_reason"]
    assert plan["safety"]["dangerous_selectable"] is False
    assert _recompute_plan_hash(plan) == plan["plan_hash"]


def test_plan_hash_detects_any_material_change():
    plan = build_plan(_synthetic_analysis(), {"present": False, "decisions": {}}, {}, "/tmp/a.json")
    baseline = _recompute_plan_hash(plan)
    assert baseline == plan["plan_hash"]

    mutations = [
        lambda p: p["items"][0].__setitem__("total_bytes", p["items"][0]["total_bytes"] + 1),
        lambda p: p["items"][0].__setitem__("risk", "SAFE"),
        lambda p: p["items"][0].__setitem__("selectable", not p["items"][0]["selectable"]),
        lambda p: p["items"][0].__setitem__("primary_path", "/sdcard/Other"),
        lambda p: p.__setitem__("analysis_id", "analysis-other000"),
    ]
    for mutate in mutations:
        modified = json.loads(json.dumps(plan))
        mutate(modified)
        assert _recompute_plan_hash(modified) != baseline


def test_random_token_format():
    token = random_token("ADC")
    assert re.fullmatch(r"ADC-[0-9A-F]{8}", token)
    assert random_token("PURGE").startswith("PURGE-")
    assert random_token("ADC") != random_token("ADC")


# ---------------------------------------------------------------- end-to-end hashes
def test_cli_plan_and_selection_hashes_are_recomputable(env):
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/a.bin", size=300)
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/b.bin", size=500)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()

    plan = env.plan
    selection = env.selection
    assert _recompute_plan_hash(plan) == plan["plan_hash"]
    assert _recompute_selection_hash(selection) == selection["selection_hash"]
    assert selection["plan_hash"] == plan["plan_hash"]
    assert selection["analysis_id"] == plan["analysis_id"]
    assert selection["mode"] == "quarantine"
    assert re.fullmatch(r"ADC-[0-9A-F]{8}", selection["token"])
    assert selection["user_confirmation"]["confirmed"] is False
    assert selection["summary"]["groups"] == len(selection["items"])

    # selection hash commits to plan hash, mode, item metadata and summary totals
    expected = sha256_text(canonical_json(selection_hash_payload(selection)))
    assert selection["selection_hash"] == expected
    assert compute_plan_hash(plan) == plan["plan_hash"]

    extra_item = json.loads(json.dumps(selection))
    extra_item["items"] = extra_item["items"] + [dict(extra_item["items"][0], id="evil-id")]
    assert _recompute_selection_hash(extra_item) != selection["selection_hash"]

    mode_changed = json.loads(json.dumps(selection))
    mode_changed["mode"] = "delete"
    assert _recompute_selection_hash(mode_changed) != selection["selection_hash"]


def test_selection_hash_covers_item_metadata_and_summary(env):
    """selection_hash must bind the user-facing confirmation metadata.

    Fields in util.selection_hash_payload (id/risk/action/primary_path/
    total_bytes/file_count/selectable + summary groups/files/bytes) are bound;
    display-only fields (title/evidence) are intentionally not execution scope.
    """
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/a.bin", size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    selection = env.selection

    total_tamper = json.loads(json.dumps(selection))
    total_tamper["items"][0]["total_bytes"] += 1
    assert _recompute_selection_hash(total_tamper) != selection["selection_hash"]

    summary_tamper = json.loads(json.dumps(selection))
    summary_tamper["summary"]["bytes"] += 1
    assert _recompute_selection_hash(summary_tamper) != selection["selection_hash"]

    risk_tamper = json.loads(json.dumps(selection))
    risk_tamper["items"][0]["risk"] = "CAUTION"
    assert _recompute_selection_hash(risk_tamper) != selection["selection_hash"]

    path_tamper = json.loads(json.dumps(selection))
    path_tamper["items"][0]["primary_path"] = "/sdcard/Other"
    assert _recompute_selection_hash(path_tamper) != selection["selection_hash"]
