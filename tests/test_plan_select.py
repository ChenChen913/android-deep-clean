"""plan/select: review gating, risk policy, token and tamper rejection."""

from __future__ import annotations

import json

import pytest

from adc_lib.executor import _recompute_plan_hash, _recompute_selection_hash

CACHE = "/sdcard/Android/data/com.installed.app/cache"


def _safe_plus_caution_fixture(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file(CACHE + "/b.bin", size=500)
    env.phone.file("/sdcard/Download/legacy_app.apk", size=1500)
    env.prepare_analysis()


def _apk_item(env):
    return env.require_item("apk")


def test_unreviewed_caution_is_not_selectable(env):
    env.phone.file("/sdcard/Download/legacy_app.apk", size=1500)
    env.prepare_analysis()
    plan = env.plan_now(None)

    apk = next(item for item in plan["items"] if item["category"] == "apk")
    assert apk["selectable"] is False
    assert apk["review_status"] == "missing"
    assert "复核" in apk["selectable_reason"]

    env.run("select", "--ids", apk["id"]).expect_error(3)
    assert not env.state_path("selection/selection.json").exists()

    # --all-safe with no SAFE candidates is also rejected.
    env.run("select", "--all-safe").expect_error(3)
    assert not env.state_path("selection/selection.json").exists()


@pytest.mark.parametrize("verdict", ["keep", "uncertain"])
def test_keep_or_uncertain_review_stays_unselectable(env, verdict):
    env.phone.file("/sdcard/Download/legacy_app.apk", size=1500)
    env.prepare_analysis()
    apk = _apk_item(env)
    review = env.write_review(
        decisions=[{"id": apk["id"], "verdict": verdict, "reason": "复核后决定保留，先不动"}]
    )
    plan = env.plan_now(review)
    item = next(i for i in plan["items"] if i["id"] == apk["id"])
    assert item["selectable"] is False
    assert verdict in item["selectable_reason"]
    env.run("select", "--ids", apk["id"]).expect_error(3)


def test_reviewed_trash_caution_becomes_selectable(env):
    env.phone.file("/sdcard/Download/legacy_app.apk", size=1500)
    env.prepare_analysis()
    apk = _apk_item(env)
    review = env.write_review(
        decisions=[{"id": apk["id"], "verdict": "trash", "reason": "已人工检查文件内容，确认是旧安装包"}]
    )
    plan = env.plan_now(review)
    item = next(i for i in plan["items"] if i["id"] == apk["id"])
    assert item["selectable"] is True
    assert item["review_status"] == "reviewed"
    assert item["ai_verdict"] == "trash"

    selection = env.select_ids(apk["id"])
    assert selection["items"][0]["id"] == apk["id"]
    assert selection["summary"]["groups"] == 1
    assert selection["mode"] == "quarantine"


def test_review_validation_rejects_unsafe_or_malformed_input(env):
    env.phone.file("/sdcard/Download/legacy_app.apk", size=1500)
    env.prepare_analysis()
    apk = _apk_item(env)
    good = {"id": apk["id"], "verdict": "trash", "reason": "已检查内容，确认可清理"}

    bad_sets = {
        "unknown_id": [dict(good, id="apk-does-not-exist")],
        "bad_verdict": [dict(good, verdict="delete")],
        "trash_without_reason": [dict(good, reason="x")],
        "risk_downgrade": [dict(good, risk="SAFE")],
        "duplicate_decisions": [good, dict(good)],
    }
    for index, (name, decisions) in enumerate(bad_sets.items()):
        review = env.write_review(decisions=decisions, filename=f"review-bad-{index}-{name}.json")
        env.run("plan", "--review", str(review)).expect_error(5)

    mismatch = env.write_review(decisions=[good], analysis_id="analysis-deadbeef00", filename="review-mismatch.json")
    env.run("plan", "--review", str(mismatch)).expect_error(5)

    assert not env.state_path("plan/plan.json").exists()


def test_select_requires_scope_and_known_ids(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.prepare_analysis()
    env.plan_now(None)

    env.run("select").expect_error(2)                       # no scope flags
    env.run("select", "--ids", "no-such-candidate").expect_error(2)
    env.run("select", "--categories", "no_such_category").expect_error(3)
    assert not env.state_path("selection/selection.json").exists()

    selection = env.select_all_safe()
    assert selection["items"]
    assert all(item["risk"] == "SAFE" for item in selection["items"])


def test_delete_mode_selection_rejects_caution(env):
    _safe_plus_caution_fixture(env)
    apk = _apk_item(env)
    review = env.write_review(
        decisions=[{"id": apk["id"], "verdict": "trash", "reason": "已检查内容，确认可清理"}]
    )
    env.plan_now(review)
    env.run("select", "--ids", apk["id"], "--mode", "delete").expect_error(3)

    safe = env.require_item("app_cache")
    selection = env.select_ids(safe["id"], mode="delete")
    assert selection["mode"] == "delete"
    assert all(item["risk"] == "SAFE" for item in selection["items"])


def test_selection_never_contains_dangerous_items(env):
    _safe_plus_caution_fixture(env)
    analysis = env.analysis
    assert all(item["risk"] in {"SAFE", "CAUTION"} for item in analysis["items"])
    assert all(item["risk"] == "DANGEROUS" for item in analysis["summary"]["protected_items"])

    env.plan_now("auto")
    selection = env.select_all_safe()
    assert all(item["risk"] != "DANGEROUS" for item in selection["items"])
    assert selection["user_confirmation"]["required"] is True
    assert selection["user_confirmation"]["confirmed"] is False


def test_plan_and_selection_reports_written(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.prepare_analysis()
    env.plan_now(None)
    plan_report = env.state_path("plan/report-plan.md").read_text(encoding="utf-8")
    assert "清理计划" in plan_report
    assert "DANGEROUS" in plan_report or "风险" in plan_report

    selection = env.select_all_safe()
    selection_report = env.state_path("selection/report-selection.md").read_text(encoding="utf-8")
    assert selection["token"] in selection_report
    assert "用户" in selection_report


def test_tampered_plan_is_rejected_without_touching_files(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file(CACHE + "/b.bin", size=500)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    before = env.snapshot()

    plan = env.plan
    plan["items"][0]["total_bytes"] += 1
    env.state_path("plan/plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    result = env.execute()
    result.expect_error(3)
    assert "plan.json" in result.stderr and "修改" in result.stderr
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)


def test_tampered_selection_ids_or_mode_are_rejected(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file(CACHE + "/b.bin", size=500)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    before = env.snapshot()
    selection = env.selection

    # (a) attacker appends an extra candidate id
    tampered_ids = json.loads(json.dumps(selection))
    tampered_ids["items"].append(dict(tampered_ids["items"][0], id="evil-candidate-id"))
    env.state_path("selection/selection.json").write_text(json.dumps(tampered_ids, ensure_ascii=False), encoding="utf-8")
    result = env.execute()
    result.expect_error(3)
    assert "selection.json" in result.stderr
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)

    # (b) attacker changes the mode
    tampered_mode = json.loads(json.dumps(selection))
    tampered_mode["mode"] = "delete"
    env.state_path("selection/selection.json").write_text(json.dumps(tampered_mode, ensure_ascii=False), encoding="utf-8")
    env.execute().expect_error(3)
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)


def test_tampered_selection_metadata_must_be_rejected(env):
    """契约要求“被修改的 selection 必须拒绝”，包括人工确认页展示的元数据。"""
    env.phone.file(CACHE + "/a.bin", size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    before = env.snapshot()
    selection = env.selection
    selection["items"][0]["total_bytes"] += 1
    selection["summary"]["bytes"] += 1
    env.state_path("selection/selection.json").write_text(json.dumps(selection, ensure_ascii=False), encoding="utf-8")
    result = env.execute()
    result.expect_error(3)
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)


def test_dangerous_item_in_tampered_plan_cannot_enter_selection(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.prepare_analysis()
    env.plan_now(None)
    plan = env.plan
    victim = plan["items"][0]
    victim["risk"] = "DANGEROUS"
    victim["selectable"] = True
    env.state_path("plan/plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    result = env.run("select", "--ids", victim["id"])
    if result.returncode == 0:
        selection = result.json()
        assert all(item["risk"] != "DANGEROUS" for item in selection["items"])
    else:
        result.expect_error(2, 3)

def test_protected_files_never_become_candidates_or_selections(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file(CACHE + "/app.db", size=4096)
    env.phone.file(CACHE + "/.nomedia", content="")
    env.phone.file(CACHE + "/secret.keystore", size=256)
    env.prepare_analysis()

    candidates = env.all_candidate_paths()
    assert CACHE + "/a.bin" in candidates
    for protected_path in (CACHE + "/app.db", CACHE + "/.nomedia", CACHE + "/secret.keystore"):
        assert protected_path not in candidates, protected_path

    protected = {item["path"] for item in env.analysis["summary"]["protected_items"]}
    assert CACHE + "/app.db" in protected
    assert all(item["risk"] == "DANGEROUS" for item in env.analysis["summary"]["protected_items"])

    env.plan_now(None)
    selection = env.select_all_safe()
    assert selection["items"]
    assert all(item["risk"] != "DANGEROUS" for item in selection["items"])
