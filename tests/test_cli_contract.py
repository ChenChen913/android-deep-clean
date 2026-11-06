"""CLI contract: exit codes, prerequisites, state validation, inspect/recheck."""

from __future__ import annotations

import json

import pytest

CACHE = "/sdcard/Android/data/com.installed.app/cache"


def _argv_after_entry(env, command, *extra):
    argv = env.build_argv(command, extra)
    return argv[2:]  # drop python + adc.py


def test_missing_prerequisites_return_validation_exit_5(env):
    env.phone.file(CACHE + "/a.bin", size=300)

    # analyze requires a snapshot
    env.run("analyze").expect_error(5)
    # plan requires analysis
    env.run("plan").expect_error(5)
    # select requires plan
    env.run("select", "--all-safe").expect_error(5)
    # execute requires plan
    env.run("execute", "--token", "ADC-DEADBEEF").expect_error(5)
    # verify/restore/purge require an execution record
    env.run("verify").expect_error(5)
    env.run("restore", "--all").expect_error(5)
    env.run(["quarantine", "purge", "--token", "PURGE-00000000"]).expect_error(5)
    # recheck requires an analysis
    env.run("recheck").expect_error(5)
    # inspect requires analysis
    env.run("inspect", "--id", "app_cache-deadbeef00").expect_error(5)

    assert not env.state_path("execution/execution.json").exists()


def test_usage_errors_return_exit_2(env):
    result = env.run_raw(["frobnicate"])
    result.expect_error(2)
    assert "invalid choice" in result.stderr or "frobnicate" in result.stderr

    # execute without the required --token argument
    env.phone.file(CACHE + "/a.bin", size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.run_raw(_argv_after_entry(env, "execute")).expect_error(2)

    # unknown transport choice
    env.run_raw(_argv_after_entry(env, "doctor")[:0] + ["doctor", "--transport", "carrier-pigeon"]).expect_error(2)


def test_transport_error_returns_exit_4(env):
    argv = _argv_after_entry(env, "doctor")
    index = argv.index("--mock-root")
    argv[index + 1] = str(env.tmp / "no-such-device-tree")
    result = env.run_raw(argv)
    result.expect_error(4)
    assert "mock root" in result.stderr or "mock" in result.stderr


def test_invalid_config_returns_exit_5(env):
    # missing config file
    argv = _argv_after_entry(env, "doctor")
    index = argv.index("--config")
    argv[index + 1] = str(env.tmp / "missing-config.json")
    env.run_raw(argv).expect_error(5)

    # malformed JSON
    bad = env.tmp / "bad-config.json"
    bad.write_text("{not-json", encoding="utf-8")
    argv = _argv_after_entry(env, "doctor")
    index = argv.index("--config")
    argv[index + 1] = str(bad)
    env.run_raw(argv).expect_error(5)

    # invalid shape (thresholds must be an object)
    bad_shape = env.tmp / "bad-shape.json"
    bad_shape.write_text(json.dumps({"thresholds": 5}), encoding="utf-8")
    argv = _argv_after_entry(env, "doctor")
    index = argv.index("--config")
    argv[index + 1] = str(bad_shape)
    env.run_raw(argv).expect_error(5)


def test_version_flag(env):
    result = env.run_raw(["--version"])
    result.expect(0)
    assert "android-deep-clean" in result.stdout


def test_inspect_candidate_lists_manifest(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file(CACHE + "/b.bin", size=500)
    env.prepare_analysis()
    item = env.require_item("app_cache")

    payload = env.ok("inspect", "--id", item["id"]).json()
    assert payload["item"]["id"] == item["id"]
    expected = {row["path"] for row in env.candidate_files(item["id"])}
    assert {row["path"] for row in payload["files"]} == expected

    env.run("inspect", "--id", "app_cache-doesnotexist").expect_error(2)


def test_recheck_creates_new_snapshot_and_comparison(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.prepare_analysis()
    recheck = env.ok("recheck").json()
    assert recheck["schema"] == 1
    assert recheck["previous_analysis_id"] == env.analysis["analysis_id"]
    assert recheck["new_analysis_id"]
    summary = recheck["summary"]
    for key in ("candidate_groups", "candidate_bytes", "new_count", "remaining_count", "vanished_count"):
        assert key in summary
    assert env.state_path("recheck/recheck.json").exists()
    assert env.state_path("recheck/report-recheck.md").exists()
    assert env.state_path("recheck/report-recheck.md").read_text(encoding="utf-8").startswith("#")


def test_user_declining_never_executes(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    before = env.snapshot()
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()  # user was shown the list and the token, but never confirmed

    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)

    # A stale/guessed token from another run must also be refused.
    env.execute(token="ADC-12345678").expect_error(3)
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)
