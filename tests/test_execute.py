"""execute/verify/restore/purge: real file moves inside the mock device.

Every test asserts both the report/exit code contract and the fixture state
(nothing extra deleted, failed sources still present).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from adc_lib.config import load_config
from adc_lib.executor import Executor
from adc_lib.models import PackageIndex, StatInfo

from adc_testkit import NOW, generated_bytes, journal_entries, quarantine_file_paths

CACHE = "/sdcard/Android/data/com.installed.app/cache"


# ---------------------------------------------------------------- helpers
def _cache_fixture(env, sizes=None, *, plan=True):
    sizes = sizes or {"a.bin": 300, "b.bin": 500}
    for name, size in sizes.items():
        env.phone.file(CACHE + "/" + name, size=size)
    env.phone.file("/sdcard/DCIM/Camera/unrelated_keep.jpg", size=123)
    env.prepare_analysis()
    if plan:
        env.plan_now(None)
    return sizes


def _quarantined_entries(env):
    return [entry for entry in journal_entries(env) if entry["event"] == "quarantined"]


def _run_purge(env, token):
    return env.run(["quarantine", "purge", "--token", token])


def _expected_bytes(device_path: str, size: int) -> bytes:
    return generated_bytes(size, seed=device_path)


def _rewrite_journal(env, mutate):
    """Rewrite every journal line through ``mutate`` (tamper simulation)."""
    path = Path(str(env.execution.get("journal_path") or ""))
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(mutate(json.loads(line)))
    path.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        encoding="utf-8",
    )


# ---------------------------------------------------------------- happy paths
def test_quarantine_execute_moves_files_and_verify(env):
    sizes = _cache_fixture(env)
    selection = env.select_all_safe()
    assert selection["mode"] == "quarantine"

    result = env.execute()
    result.expect(0)
    execution = result.json()

    assert execution["mode"] == "quarantine"
    assert execution["status"] == "completed"
    assert execution["quarantine_root"].startswith("/sdcard/.android-deep-clean/quarantine/")
    assert re.fullmatch(r"PURGE-[0-9A-F]{8}", execution["purge_token"])
    summary = execution["summary"]
    assert summary["success_files"] == 2
    assert summary["failed_files"] == 0
    assert summary["quarantine_files"] == 2
    assert summary["quarantine_bytes"] == 800
    assert summary["discovered_bytes"] >= summary["selected_bytes"]
    assert summary["selected_bytes"] == 800
    assert summary["processed_bytes"] == 800
    assert summary["unprocessed_bytes"] == 0
    assert summary["released_bytes"] == 0
    assert summary["quarantine_occupied_bytes"] == 800
    assert "空间账目" in env.state_path("execution/report-after.md").read_text(encoding="utf-8")

    for name in sizes:
        assert not env.phone.exists(CACHE + "/" + name), name
    assert env.phone.exists("/sdcard/DCIM/Camera/unrelated_keep.jpg")

    entries = _quarantined_entries(env)
    assert len(entries) == 2
    for entry in entries:
        name = entry["path"].rsplit("/", 1)[1]
        stored = env.phone.host(entry["quarantine_path"])
        assert stored.is_file()
        assert stored.stat().st_size == sizes[name]
        assert stored.read_bytes() == _expected_bytes(entry["path"], sizes[name])

    assert env.state_path("execution/execution.json").exists()
    assert env.state_path("execution/journal.jsonl").exists()
    report = env.state_path("execution/report-after.md").read_text(encoding="utf-8")
    assert "隔离" in report
    assert "执行" in report

    verification = env.ok("verify").json()
    vsummary = verification["summary"]
    assert vsummary["checked"] == 2
    assert vsummary["passed"] == 2
    assert vsummary["failed"] == 0
    assert env.state_path("execution/report-verify.md").exists()


def test_permanent_delete_mode(env):
    sizes = _cache_fixture(env)
    selection = env.select_all_safe(mode="delete")
    assert selection["mode"] == "delete"

    result = env.execute(mode="delete", allow_permanent=True)
    result.expect(0)
    execution = result.json()
    assert execution["status"] == "completed"
    summary = execution["summary"]
    assert summary["success_files"] == 2
    assert summary["failed_files"] == 0
    assert summary["quarantine_files"] == 0
    assert summary["released_bytes"] == 800
    assert execution["quarantine_root"] == ""
    for name in sizes:
        assert not env.phone.exists(CACHE + "/" + name), name
    assert quarantine_file_paths(env) == []
    assert env.ok("verify").json()["summary"]["failed"] == 0


def test_permanent_delete_requires_flag_and_matching_mode(env):
    _cache_fixture(env)
    env.select_all_safe()  # quarantine mode
    before = env.snapshot()
    env.execute(mode="delete", allow_permanent=True).expect_error(3)
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)

    env.select_all_safe(mode="delete")
    env.execute(mode="delete", allow_permanent=False).expect_error(3)
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)


# ---------------------------------------------------------------- token / selection contract
def test_execute_requires_selection_and_valid_token(env):
    _cache_fixture(env)
    before = env.snapshot()

    # No selection at all -> validation error (5).
    env.run("execute", "--token", "ADC-DEADBEEF").expect_error(5)
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)

    env.select_all_safe()

    # Wrong token -> safety rejection (3).
    result = env.execute(token="ADC-00000000")
    result.expect_error(3)
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)

    # Missing --token argument -> usage error (2).
    argv = env.build_argv("execute")
    env.run_raw(argv[2:]).expect_error(2)
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)


def test_confirmation_token_cannot_be_reused(env):
    """A used token must never move files a second time after a restore."""
    _cache_fixture(env)
    env.select_all_safe()
    env.execute_ok()
    token = str(env.selection.get("token") or "")
    selection_id = str(env.selection.get("selection_id") or "")
    assert token

    # Restore puts the files back; the old token must still be refused.
    env.ok("restore", "--all")
    assert env.phone.exists(CACHE + "/a.bin")

    env.execute(token=token).expect_error(3)
    assert env.phone.exists(CACHE + "/a.bin"), "a replayed token must not move files again"
    consumed = env.state_json("execution/consumed-tokens.json")
    assert consumed and selection_id in {item.get("selection_id") for item in consumed.values()}

    # A fresh selection produces a usable new token.
    env.select_all_safe()
    env.execute_ok()


# ---------------------------------------------------------------- denied operations
def test_denied_move_reports_failure_and_keeps_source(env):
    blocked = CACHE + "/blocked.bin"
    env.phone.file(CACHE + "/ok.bin", size=300)
    env.phone.file(blocked, size=500)
    env.set_caps(denied_ops=["move:" + blocked])
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()

    result = env.execute()
    result.expect_error(6)
    execution = env.execution
    assert execution["status"] == "partial"
    assert execution["summary"]["success_files"] == 1
    assert execution["summary"]["failed_files"] == 1
    assert env.phone.exists(blocked), "failed move must leave the source file in place"
    assert not env.phone.exists(CACHE + "/ok.bin")
    assert len(execution["failures"]) == 1
    assert execution["failures"][0]["path"] == blocked
    assert "拒绝" in execution["failures"][0]["reason"]
    report = env.state_path("execution/report-after.md").read_text(encoding="utf-8")
    assert "blocked.bin" in report


def test_denied_delete_reports_failure_and_keeps_source(env):
    blocked = CACHE + "/blocked.bin"
    env.phone.file(CACHE + "/ok.bin", size=300)
    env.phone.file(blocked, size=500)
    env.set_caps(denied_ops=["delete:" + blocked])
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe(mode="delete")

    result = env.execute(mode="delete", allow_permanent=True)
    result.expect_error(6)
    execution = env.execution
    assert execution["status"] == "partial"
    assert execution["summary"]["failed_files"] == 1
    assert execution["summary"]["success_files"] == 1
    assert env.phone.exists(blocked)
    assert not env.phone.exists(CACHE + "/ok.bin")


# ---------------------------------------------------------------- changed files after plan
def test_changed_or_deleted_files_are_skipped_after_plan(env):
    paths = {name: CACHE + "/" + name for name in ("m1.bin", "m2.bin", "m3.bin", "m4.bin")}
    env.phone.file("/sdcard/DCIM/Camera/unrelated_keep.jpg", size=123)
    env.phone.file(paths["m1.bin"], size=300)
    env.phone.file(paths["m2.bin"], size=400)
    env.phone.file(paths["m3.bin"], size=500)
    env.phone.file(paths["m4.bin"], size=600)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    before = env.snapshot()

    env.phone.set_mtime(paths["m1.bin"], NOW - 2 * 86400)                 # mtime changed
    env.phone.file(paths["m2.bin"], content=generated_bytes(999, seed="changed"), age_days=5)  # size changed
    env.phone.remove(paths["m3.bin"])                                     # deleted
    # m4 stays untouched and must be processed.

    result = env.execute()
    result.expect_error(6)
    execution = env.execution
    assert execution["status"] == "partial"
    assert execution["summary"]["success_files"] == 1
    assert execution["summary"]["failed_files"] == 3
    reasons = " | ".join(failure["reason"] for failure in execution["failures"])
    assert "修改时间已变化" in reasons
    assert "大小已变化" in reasons
    assert "已不存在" in reasons

    assert env.phone.exists(paths["m1.bin"])
    assert env.phone.exists(paths["m2.bin"])
    assert not env.phone.exists(paths["m3.bin"])
    assert not env.phone.exists(paths["m4.bin"])
    assert env.phone.exists("/sdcard/DCIM/Camera/unrelated_keep.jpg")

    events = [entry["event"] for entry in journal_entries(env)]
    assert events.count("skipped") == 3
    assert events.count("quarantined") == 1
    assert env.ok("verify").json()["summary"]["failed"] == 0


def test_missing_target_directory_is_reported_as_failure(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file("/sdcard/DCIM/Camera/unrelated_keep.jpg", size=123)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    env.phone.remove(CACHE)

    result = env.execute()
    result.expect_error(6)  # contract: partial failure must exit 6
    execution = env.execution
    item = execution["items"][0]
    assert item["status"] == "failed"
    assert "目标目录" in item["error"]
    assert env.state_path("execution/report-after.md").exists()
    assert env.phone.exists("/sdcard/DCIM/Camera/unrelated_keep.jpg")


# ---------------------------------------------------------------- tampered manifests
def _inject_manifest_row(env, candidate_id, path, *, size=0, mtime=0, kind="file", role="delete"):
    conn = env.analysis_db_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO candidate_files(candidate_id,path,size,mtime,inode,dev,kind,role) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (candidate_id, path, size, mtime, 0, 0, kind, role),
        )
        conn.commit()
    finally:
        conn.close()
    _rebind_manifest_hashes(env, candidate_id)


def _rebind_manifest_hashes(env, candidate_id):
    """Simulate an attacker who rewrites analysis.db and recomputes the hashes.

    The per-file guards must still hold in that case; the digest gate is covered
    separately by test_manifest_digest_mismatch_is_refused_before_execution.
    """
    from adc_lib.util import compute_files_digest, compute_plan_hash, compute_selection_hash

    digest = compute_files_digest(env.candidate_files(candidate_id))
    plan = env.plan
    for item in plan["items"]:
        if item["id"] == candidate_id:
            item["files_digest"] = digest
    plan["plan_hash"] = compute_plan_hash(plan)
    env.state_path("plan/plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    if env.state_path("selection/selection.json").exists():
        selection = env.selection
        for item in selection["items"]:
            if item["id"] == candidate_id:
                item["files_digest"] = digest
        selection["plan_hash"] = plan["plan_hash"]
        selection["selection_hash"] = compute_selection_hash(selection)
        env.state_path("selection/selection.json").write_text(
            json.dumps(selection, ensure_ascii=False), encoding="utf-8"
        )


def test_manifest_digest_mismatch_is_refused_before_execution(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    conn = env.analysis_db_connection()
    try:
        conn.execute("UPDATE candidate_files SET size=size+1 WHERE path=?", (CACHE + "/a.bin",))
        conn.commit()
    finally:
        conn.close()

    result = env.execute()
    result.expect_error(3)
    assert "文件清单" in result.stderr
    assert env.phone.exists(CACHE + "/a.bin"), "execution must stop before touching any file"
    assert not env.state_path("execution/execution.json").exists()


def test_path_traversal_in_manifest_is_refused_with_report(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file("/sdcard/DCIM/Camera/unrelated_keep.jpg", size=123)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    candidate_id = selection["items"][0]["id"]
    _inject_manifest_row(env, candidate_id, "/sdcard/../../etc/passwd")

    result = env.execute()
    result.expect_error(6)  # contract: reported per-file failure => partial exit 6
    execution = env.execution
    assert execution["status"] == "partial"
    reasons = " | ".join(failure["reason"] for failure in execution["failures"])
    assert "非法路径" in reasons or "traversal" in reasons
    assert env.state_path("execution/report-after.md").exists()
    assert env.phone.exists("/sdcard/DCIM/Camera/unrelated_keep.jpg")


def test_protected_and_system_paths_in_manifest_are_refused(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    nomedia = CACHE + "/.nomedia"
    env.phone.file(nomedia, content="")
    decoy = "/system/bin/decoy"
    env.phone.file(decoy, content="system")
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    candidate_id = selection["items"][0]["id"]
    _inject_manifest_row(env, candidate_id, nomedia)
    _inject_manifest_row(env, candidate_id, decoy, size=len("system"))

    result = env.execute()
    result.expect_error(6)
    execution = env.execution
    failures = {failure["path"]: failure["reason"] for failure in execution["failures"]}
    assert nomedia in failures
    assert decoy in failures
    assert "保护" in failures[nomedia] or "敏感" in failures[nomedia]
    assert "系统" in failures[decoy] or "硬保护" in failures[decoy]
    assert env.phone.exists(nomedia)
    assert env.phone.exists(decoy)
    assert not env.phone.exists(CACHE + "/a.bin"), "the safe file should still be processed"


# ---------------------------------------------------------------- symlinks (unit + conditional integration)
class _StubTransport:
    def __init__(self, *, kind="file", size=10, mtime=100, inode=7, realpath_value=None):
        self.kind = kind
        self.size = size
        self.mtime = mtime
        self.inode = inode
        self.realpath_value = realpath_value

    def stat(self, path):
        return StatInfo(path=path, kind=self.kind, size=self.size, mtime=self.mtime, inode=self.inode, dev=1)

    def realpath(self, path):
        return self.realpath_value if self.realpath_value is not None else path


def _bare_executor(transport):
    executor = Executor.__new__(Executor)
    executor.transport = transport
    executor.config = load_config()
    executor.packages = PackageIndex(installed={"com.installed.app"}, available=True)
    return executor


def _row(path, *, size=10, mtime=100, inode=7):
    return {"path": path, "size": size, "mtime": mtime, "inode": inode}


def test_executor_refuses_symlink_entry():
    path = CACHE + "/link.bin"
    executor = _bare_executor(_StubTransport(kind="symlink"))
    reason = executor._validate_file(path, _row(path), "app_cache", "")
    assert "不是普通文件" in reason


def test_executor_refuses_realpath_redirect():
    path = CACHE + "/redirect.bin"
    executor = _bare_executor(_StubTransport(realpath_value="/sdcard/Other/target.bin"))
    reason = executor._validate_file(path, _row(path), "app_cache", "")
    assert "realpath" in reason


def test_executor_refuses_changed_size_mtime_inode():
    path = CACHE + "/x.bin"
    assert "大小已变化" in _bare_executor(_StubTransport(size=11))._validate_file(path, _row(path), "app_cache", "")
    assert "修改时间已变化" in _bare_executor(_StubTransport(mtime=999))._validate_file(path, _row(path), "app_cache", "")
    assert "inode 已变化" in _bare_executor(_StubTransport(inode=99))._validate_file(path, _row(path), "app_cache", "")


def test_executor_refuses_file_outside_target_directory():
    path = CACHE + "/nested/x.bin"
    executor = _bare_executor(_StubTransport())
    reason = executor._validate_file(path, _row(path), "app_cache", "/sdcard/Android/data/com.installed.app/files")
    assert "不在目标目录内" in reason


def test_execute_refuses_real_symlink_from_tampered_manifest(env):
    target = "/sdcard/Misc/real_target.bin"
    env.phone.file(target, size=300)
    env.phone.file(CACHE + "/a.bin", size=300)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    candidate_id = selection["items"][0]["id"]

    # Create the link only after analyze/plan, then inject it into the manifest:
    # the link itself must be refused and its target must survive.
    link_host = env.phone.host(CACHE + "/link.bin")
    real_host = env.phone.host(target)
    try:
        os.symlink(real_host, link_host)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"当前环境无法创建符号链接（Windows 需要开发者模式/管理员）: {exc}")
    _inject_manifest_row(env, candidate_id, CACHE + "/link.bin")

    result = env.execute()
    result.expect_error(6)
    execution = env.execution
    failures = {failure["path"]: failure["reason"] for failure in execution["failures"]}
    assert CACHE + "/link.bin" in failures
    assert "不是普通文件" in failures[CACHE + "/link.bin"]
    assert link_host.is_symlink()
    assert real_host.exists()


def _try_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and Path(link).exists()


def _make_dir_link(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return _try_junction(link, target)


def test_execute_refuses_parent_directory_link(env):
    env.phone.file("/sdcard/Misc/realcache/a.bin", size=300)
    cache_host = env.phone.host(CACHE)
    cache_host.parent.mkdir(parents=True, exist_ok=True)
    if cache_host.exists():
        shutil.rmtree(cache_host)
    if not _try_junction(cache_host, env.phone.host("/sdcard/Misc/realcache")):
        pytest.skip("当前环境无法创建目录联接（mklink /J 不可用）")

    env.prepare_analysis()
    env.plan_now(None)
    cache_item = env.item("app_cache")
    if cache_item is None:
        pytest.skip("目录联接未被扫描/分类为缓存目录（平台差异）")
    env.select_ids(cache_item["id"])

    result = env.execute()
    execution = env.execution if env.state_path("execution/execution.json").exists() else None
    # Contract: a redirected (symlinked) target directory must be reported as
    # failed and no real file may be moved/deleted.
    real_file = env.phone.host("/sdcard/Misc/realcache/a.bin")
    assert real_file.exists(), "real target file must not be moved through a linked directory"
    if execution is not None:
        item = execution["items"][0]
        assert item["status"] == "failed"
        assert "realpath" in item["error"]
    else:
        result.expect_error(3, 5, 6)


def test_purge_and_restore_refuse_symlink_quarantine_file(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    execution = env.execute().json()
    stored = quarantine_file_paths(env)
    assert len(stored) == 1

    outside = env.phone.host("/sdcard/Misc/outside.bin")
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"outside")
    stored[0].unlink()
    try:
        os.symlink(outside, stored[0])
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"当前环境无法创建符号链接：{exc}")

    purged = env.run(["quarantine", "purge", "--token", execution["purge_token"]])
    purged.expect_error(6)
    assert "不是普通文件" in purged.json()["failures"][0]["reason"]
    assert outside.read_bytes() == b"outside"

    restored = env.run("restore", "--all")
    restored.expect_error(6)
    assert "不是普通文件" in restored.json()["failures"][0]["reason"]
    assert outside.read_bytes() == b"outside"


def test_purge_and_restore_refuse_symlink_quarantine_parent(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    execution = env.execute().json()
    stored = quarantine_file_paths(env)
    assert len(stored) == 1
    qfile = stored[0]
    qparent = qfile.parent

    outside = env.phone.host("/sdcard/Misc/quarantine-escape")
    outside.mkdir(parents=True, exist_ok=True)
    moved = outside / qparent.name
    shutil.move(str(qparent), str(moved))
    if not _make_dir_link(qparent, moved):
        pytest.skip("当前环境无法创建目录符号链接/联接")
    assert qfile.exists(), "quarantine file should still resolve through the link"

    purged = env.run(["quarantine", "purge", "--token", execution["purge_token"]])
    purged.expect_error(6)
    assert "父目录真实路径不在隔离区内" in purged.json()["failures"][0]["reason"]
    assert qfile.exists()

    restored = env.run("restore", "--all")
    restored.expect_error(6)
    assert "父目录真实路径不在隔离区内" in restored.json()["failures"][0]["reason"]
    assert qfile.exists()


def test_purge_refuses_replaced_quarantine_root(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    execution = env.execute().json()
    stored = quarantine_file_paths(env)
    assert len(stored) == 1

    root_host = env.phone.host(execution["quarantine_root"])
    moved = root_host.parent / (root_host.name + "-moved")
    shutil.move(str(root_host), str(moved))
    if not _make_dir_link(root_host, moved):
        pytest.skip("当前环境无法创建目录符号链接/联接")
    assert stored[0].exists(), "quarantine file should still resolve through the replaced root"

    purged = env.run(["quarantine", "purge", "--token", execution["purge_token"]])
    purged.expect_error(3)
    assert "隔离区真实路径已变化" in (purged.stderr or "") + (purged.stdout or "")
    assert stored[0].exists()


def test_restore_refuses_symlinked_target_parent(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    env.execute_ok()
    stored = quarantine_file_paths(env)
    assert len(stored) == 1

    cache_host = env.phone.host(CACHE)
    outside = env.phone.host("/sdcard/Misc/escape-cache")
    outside.mkdir(parents=True, exist_ok=True)
    moved = outside / cache_host.name
    if cache_host.exists():
        shutil.move(str(cache_host), str(moved))
    else:
        moved.mkdir(parents=True, exist_ok=True)
    if not _make_dir_link(cache_host, moved):
        pytest.skip("当前环境无法创建目录符号链接/联接")

    result = env.run("restore", "--all")
    result.expect_error(6)
    payload = result.json()
    assert "恢复目标父目录被重定向" in payload["failures"][0]["reason"]
    assert stored[0].exists()
    assert not env.phone.exists(path)


# ---------------------------------------------------------------- empty dirs / rmdir
def _empty_dir_flow(env):
    env.phone.dir("/sdcard/Misc/EmptyDir", age_days=5)
    env.prepare_analysis()
    env.plan_now("auto")  # empty dirs are CAUTION -> review required
    item = env.require_item("empty_dirs")
    env.select_ids(item["id"])
    return item


def test_empty_dir_rmdir_removes_only_empty_directory(env):
    item = _empty_dir_flow(env)
    target = "/sdcard/Misc/EmptyDir"
    assert env.phone.exists(target)

    result = env.execute()
    result.expect(0)
    execution = env.execution
    assert execution["status"] == "completed"
    assert not env.phone.exists(target)
    events = [entry["event"] for entry in journal_entries(env)]
    assert "rmdir" in events
    assert env.ok("verify").json()["summary"]["failed"] == 0


def test_empty_dir_rmdir_refuses_non_empty_directory_without_recursion(env):
    item = _empty_dir_flow(env)
    target = "/sdcard/Misc/EmptyDir"
    intruder = target + "/intruder.bin"
    env.phone.file(intruder, content="do not delete me")

    result = env.execute()
    result.expect_error(6)
    execution = env.execution
    assert execution["status"] == "partial"
    assert execution["summary"]["failed_files"] >= 1
    assert env.phone.exists(target), "non-empty directory must not be removed"
    assert env.phone.exists(intruder), "rmdir must never recurse into directory contents"
    failures = " | ".join(failure["reason"] for failure in execution["failures"])
    assert "rmdir 失败" in failures


# ---------------------------------------------------------------- verify
def test_verify_detects_source_recreated_after_quarantine(env):
    sizes = {"a.bin": 300}
    _cache_fixture(env, sizes=sizes)
    env.select_all_safe()
    env.execute().expect(0)

    first = env.ok("verify").json()
    assert first["summary"]["failed"] == 0

    env.phone.file(CACHE + "/a.bin", content="recreated by app")
    result = env.run("verify")
    result.expect_error(6)
    verification = result.json()
    assert verification["summary"]["failed"] >= 1
    assert verification["summary"]["source_present"] >= 1
    assert any("源文件仍然存在" in failure["reason"] for failure in verification["failures"])


def test_verify_detects_missing_quarantine_file(env):
    _cache_fixture(env, sizes={"a.bin": 300})
    env.select_all_safe()
    env.execute().expect(0)
    stored = quarantine_file_paths(env)
    assert len(stored) == 1
    stored[0].unlink()

    result = env.run("verify")
    result.expect_error(6)
    verification = result.json()
    assert verification["summary"]["quarantine_missing"] >= 1


# ---------------------------------------------------------------- restore
def test_restore_recovers_quarantined_files(env):
    path = CACHE + "/a.bin"
    expected = _expected_bytes(path, 300)
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    env.execute().expect(0)
    assert not env.phone.exists(path)
    stored = quarantine_file_paths(env)
    assert len(stored) == 1

    restored = env.ok("restore", "--all").json()
    assert restored["restored_files"] == 1
    assert restored["failed"] == 0
    assert env.phone.exists(path)
    assert env.phone.host(path).read_bytes() == expected
    assert not any(item.exists() for item in stored)
    journal = journal_entries(env)
    assert any(entry.get("restore_status") == "restored" for entry in journal)

    again = env.ok("restore", "--all").json()
    assert again["restored_files"] == 0


def test_restore_by_candidate_id(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    candidate_id = selection["items"][0]["id"]
    env.execute().expect(0)

    restored = env.ok("restore", "--ids", candidate_id).json()
    assert restored["restored_files"] == 1
    assert env.phone.exists(path)


# ---------------------------------------------------------------- purge
def test_purge_requires_token_and_deletes_permanently(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    execution = env.execute().json()
    purge_token = execution["purge_token"]
    stored = quarantine_file_paths(env)
    assert len(stored) == 1

    wrong = _run_purge(env, "PURGE-WRONG000")
    wrong.expect_error(3)
    assert stored[0].exists(), "wrong purge token must not delete quarantine files"

    purged = _run_purge(env, purge_token)
    purged.expect(0)
    payload = purged.json()
    assert payload["purged_files"] == 1
    assert payload["failed"] == 0
    assert not stored[0].exists()
    assert not env.phone.exists(path), "purged files are gone permanently"
    assert env.state_path("execution/report-purge.md").exists()

    restored = env.ok("restore", "--all").json()
    assert restored["restored_files"] == 0
    assert not env.phone.exists(path)

    again = _run_purge(env, purge_token)
    again.expect(0)
    assert again.json()["purged_files"] == 0


def test_repeated_purge_keeps_history_and_verify_stays_clean(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file(CACHE + "/b.bin", size=500)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    execution = env.execute().json()
    token = execution["purge_token"]

    first = _run_purge(env, token)
    first.expect(0)
    assert first.json()["purged_files"] == 2

    second = _run_purge(env, token)
    second.expect(0)
    payload = second.json()
    assert payload["purged_files"] == 0, "the second run itself deletes nothing"
    assert payload["cumulative"]["purged_files"] == 2
    assert payload["cumulative"]["purged_bytes"] == 800

    stored = env.execution
    assert stored["purge"]["purged_files"] == 2, "history must not collapse to 0"
    assert stored["purge"]["purged_bytes"] == 800
    assert stored["purge"]["last_run"]["purged_files"] == 0
    assert len(stored["purge"]["runs"]) == 2
    assert stored["quarantine_state"]["remaining_files"] == 0

    entries = _quarantined_entries(env)
    assert len(entries) == 2
    assert all(entry.get("purge_status") == "purged" for entry in entries)
    assert not any(entry.get("purge_status") == "missing" for entry in entries)

    verification = env.ok("verify").json()
    assert verification["summary"]["checked"] == 0
    assert verification["summary"]["failed"] == 0
    assert verification["summary"]["quarantine_missing"] == 0

    html = env.state_path("execution/report-cleanup.html").read_text(encoding="utf-8")
    assert "永久删除（累计）" in html
    assert "没有可验证项目" in html
    assert "所有检查项通过" not in html
    assert "隔离区仍位于手机上" not in html


def test_purge_after_restore_does_not_mark_restored_entries_missing(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    execution = env.execute().json()
    env.ok("restore", "--all")

    result = _run_purge(env, execution["purge_token"])
    result.expect(0)
    assert result.json()["purged_files"] == 0
    assert result.json()["quarantine_remaining"]["remaining_files"] == 0

    entries = _quarantined_entries(env)
    assert entries[0].get("restore_status") == "restored"
    assert "purge_status" not in entries[0], "restored files are not missing quarantine files"
    assert env.phone.exists(path)
    assert env.ok("verify").json()["summary"]["failed"] == 0


def test_multiple_restore_runs_keep_cumulative_totals(env):
    env.set_packages(installed=["com.installed.app", "com.other.app"])
    env.phone.file(CACHE + "/a.bin", size=300)
    env.phone.file("/sdcard/Android/data/com.other.app/cache/b.bin", size=500)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    ids = [item["id"] for item in selection["items"]]
    assert len(ids) == 2
    env.execute_ok()

    first = env.ok("restore", "--ids", ids[0]).json()
    second = env.ok("restore", "--ids", ids[1]).json()
    assert first["restored_files"] == 1
    assert second["restored_files"] == 1
    assert second["cumulative"]["restored_files"] == 2
    assert second["cumulative"]["restored_bytes"] == 800
    assert second["quarantine_remaining"]["remaining_files"] == 0

    stored = env.execution
    assert stored["restore"]["restored_files"] == 2, "batched restores must be added up"
    assert stored["restore"]["restored_bytes"] == 800
    assert stored["restore"]["last_run"]["restored_files"] == 1
    assert len(stored["restore"]["runs"]) == 2

    html = env.state_path("execution/report-cleanup.html").read_text(encoding="utf-8")
    assert "已恢复（累计）" in html
    assert "restore 执行记录" in html
    assert "隔离区已清空" in html


def test_purge_refuses_quarantine_file_whose_size_changed(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    execution = env.execute().json()
    stored = quarantine_file_paths(env)
    assert len(stored) == 1
    stored[0].write_bytes(generated_bytes(500, seed="changed-size"))

    result = _run_purge(env, execution["purge_token"])
    result.expect_error(6)
    payload = result.json()
    assert payload["purged_files"] == 0
    assert payload["failed"] == 1
    assert "大小已变化" in payload["failures"][0]["reason"]
    assert stored[0].exists(), "a changed quarantine file must never be deleted"
    assert not env.phone.exists(path)
    assert env.execution["purge"]["last_run"]["failed"] == 1
    assert env.execution["status"] == "partial"


def test_restore_refuses_quarantine_file_whose_size_changed(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    env.execute_ok()
    stored = quarantine_file_paths(env)
    assert len(stored) == 1
    stored[0].write_bytes(generated_bytes(500, seed="changed-size"))

    result = env.run("restore", "--all")
    result.expect_error(6)
    payload = result.json()
    assert payload["restored_files"] == 0
    assert payload["failed"] == 1
    assert "大小已变化" in payload["failures"][0]["reason"]
    assert stored[0].exists(), "a changed quarantine file must stay in quarantine"
    assert not env.phone.exists(path)


def test_purge_and_restore_refuse_quarantine_file_whose_content_changed(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    backup_dir = env.tmp / "pc-backup"
    env.run("execute", "--token", selection["token"], "--backup-to", str(backup_dir)).expect(0)

    execution = env.execution
    entries = _quarantined_entries(env)
    assert entries and entries[0].get("backup_sha256"), "backup hash is the content reference"
    stored = quarantine_file_paths(env)
    assert len(stored) == 1
    stored[0].write_bytes(b"x" * 300)  # same size, different content

    purged = _run_purge(env, execution["purge_token"])
    purged.expect_error(6)
    payload = purged.json()
    assert payload["purged_files"] == 0
    assert "SHA-256" in payload["failures"][0]["reason"]
    assert stored[0].exists()

    restored = env.run("restore", "--all")
    restored.expect_error(6)
    restore_payload = restored.json()
    assert restore_payload["restored_files"] == 0
    assert "SHA-256" in restore_payload["failures"][0]["reason"]
    assert stored[0].exists()
    assert not env.phone.exists(path)


def test_verify_reports_quarantine_content_change(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    backup_dir = env.tmp / "pc-backup"
    env.run("execute", "--token", selection["token"], "--backup-to", str(backup_dir)).expect(0)
    stored = quarantine_file_paths(env)
    assert len(stored) == 1
    stored[0].write_bytes(b"x" * 300)  # same size, different content

    result = env.run("verify")
    result.expect_error(6)
    payload = result.json()
    assert payload["summary"]["failed"] == 1
    assert "SHA-256" in payload["failures"][0]["reason"]


def test_purge_history_keeps_previous_failure_details(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    execution = env.execute().json()
    token = execution["purge_token"]
    stored = quarantine_file_paths(env)
    assert len(stored) == 1

    stored[0].write_bytes(generated_bytes(500, seed="changed-size"))
    failed = _run_purge(env, token)
    failed.expect_error(6)
    assert failed.json()["failed"] == 1
    assert stored[0].exists()

    stored[0].write_bytes(generated_bytes(300, seed=path))
    retried = _run_purge(env, token)
    retried.expect(0)
    assert retried.json()["purged_files"] == 1
    assert retried.json()["cumulative"]["purged_files"] == 1

    execution = env.execution
    runs = execution["purge"]["runs"]
    assert len(runs) == 2
    assert runs[0]["failed"] == 1
    assert "大小已变化" in runs[0]["failures"][0]["reason"]
    assert runs[1]["purged_files"] == 1
    assert execution["purge"]["purged_files"] == 1

    html = env.state_path("execution/report-cleanup.html").read_text(encoding="utf-8")
    assert "purge 执行记录" in html
    assert "purge 历史失败明细" in html
    assert "大小已变化" in html


def test_previous_runs_migrates_failed_only_record():
    from adc_lib.executor import _previous_runs

    runs = _previous_runs(
        {
            "purged_files": 0,
            "purged_bytes": 0,
            "failed": 1,
            "failures": [{"path": "/sdcard/a", "reason": "old failure"}],
        },
        files_key="purged_files",
        bytes_key="purged_bytes",
        fallback_at="2026-01-01T00:00:00+08:00",
    )
    assert len(runs) == 1
    assert runs[0]["at"] == "2026-01-01T00:00:00+08:00"
    assert runs[0]["failed"] == 1
    assert runs[0]["failures"][0]["reason"] == "old failure"
    assert _previous_runs(
        {"purged_files": 0, "failed": 0},
        files_key="purged_files",
        bytes_key="purged_bytes",
    ) == []


@pytest.mark.parametrize("tampered", ["outside", "traversal"])
def test_restore_refuses_tampered_quarantine_path(env, tampered):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    env.execute_ok()
    stored = quarantine_file_paths(env)
    assert len(stored) == 1

    outside = "/sdcard/DCIM/Camera/keep.jpg"
    env.phone.file(outside, size=123)
    original = env.phone.host(outside).read_bytes()
    root = str(env.execution["quarantine_root"])
    forged = outside if tampered == "outside" else root + "/files/../../../../DCIM/Camera/keep.jpg"
    _rewrite_journal(
        env,
        lambda entry: (
            {**entry, "quarantine_path": forged}
            if entry.get("event") == "quarantined" else entry
        ),
    )

    verified = env.run("verify")
    verified.expect_error(6)
    assert "隔离路径不在本次隔离区内" in verified.json()["failures"][0]["reason"]

    result = env.run("restore", "--all")
    result.expect_error(6)
    payload = result.json()
    assert payload["restored_files"] == 0
    assert payload["failed"] == 1
    reason = payload["failures"][0]["reason"]
    assert "隔离路径不在本次隔离区内" in reason or "记录中的路径非法" in reason
    assert stored[0].exists(), "the real quarantine file must stay"
    assert env.phone.host(outside).read_bytes() == original, "outside file must not be moved"
    assert not env.phone.exists(path)


def test_restore_refuses_unsafe_target_path(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()
    env.execute_ok()
    stored = quarantine_file_paths(env)
    assert len(stored) == 1

    unsafe = "/system/etc/hosts"
    _rewrite_journal(
        env,
        lambda entry: {**entry, "path": unsafe} if entry.get("event") == "quarantined" else entry,
    )
    result = env.run("restore", "--all")
    result.expect_error(6)
    payload = result.json()
    assert payload["restored_files"] == 0
    assert "恢复目标不符合安全路径规则" in payload["failures"][0]["reason"]
    assert stored[0].exists()
    assert not env.phone.exists(unsafe)


def test_hash_calculation_error_is_incomplete_not_passed(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    backup_dir = env.tmp / "pc-backup"
    env.run("execute", "--token", selection["token"], "--backup-to", str(backup_dir)).expect(0)
    stored = quarantine_file_paths(env)
    assert len(stored) == 1

    env.set_caps(hash_mode="error")
    verified = env.run("verify")
    verified.expect_error(6)
    verification = verified.json()
    assert verification["summary"]["hash_incomplete"] == 1
    assert verification["summary"]["failed"] == 1
    assert "完整性校验未完成" in verification["failures"][0]["reason"]
    html = env.state_path("execution/report-cleanup.html").read_text(encoding="utf-8")
    assert "校验未完成" in html
    assert "所有检查项通过" not in html

    purged = env.run(["quarantine", "purge", "--token", env.execution["purge_token"]])
    purged.expect_error(6)
    payload = purged.json()
    assert payload["purged_files"] == 0
    assert payload["failed"] == 1
    assert payload["hash_incomplete_files"] == 1
    assert "完整性校验未完成" in payload["failures"][0]["reason"]
    assert stored[0].exists()


def test_missing_hash_tool_degrades_to_size_and_is_marked(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    backup_dir = env.tmp / "pc-backup"
    env.run("execute", "--token", selection["token"], "--backup-to", str(backup_dir)).expect(0)

    env.set_caps(hash_mode="unavailable")
    verified = env.ok("verify").json()
    summary = verified["summary"]
    assert summary["hash_unavailable"] == 1
    assert summary["hash_verified"] == 0
    assert summary["failed"] == 0
    html = env.state_path("execution/report-cleanup.html").read_text(encoding="utf-8")
    assert "仅大小校验" in html
    assert "未做内容校验" in html

    purged = env.run(["quarantine", "purge", "--token", env.execution["purge_token"]])
    purged.expect(0)
    payload = purged.json()
    assert payload["purged_files"] == 1
    assert payload["hash_unavailable_files"] == 1
    html = env.state_path("execution/report-cleanup.html").read_text(encoding="utf-8")
    assert "未做内容校验" in html


def test_hash_none_is_incomplete(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    backup_dir = env.tmp / "pc-backup"
    env.run("execute", "--token", selection["token"], "--backup-to", str(backup_dir)).expect(0)
    stored = quarantine_file_paths(env)
    assert len(stored) == 1

    env.set_caps(hash_mode="none")
    purged = env.run(["quarantine", "purge", "--token", env.execution["purge_token"]])
    purged.expect_error(6)
    payload = purged.json()
    assert payload["hash_incomplete_files"] == 1
    assert "无法计算隔离文件哈希" in payload["failures"][0]["reason"]
    assert stored[0].exists()


def test_md5_only_device_does_not_false_reject(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    backup_dir = env.tmp / "pc-backup"
    env.run("execute", "--token", selection["token"], "--backup-to", str(backup_dir)).expect(0)

    env.set_caps(hash_mode="md5")
    purged = env.run(["quarantine", "purge", "--token", env.execution["purge_token"]])
    purged.expect(0)
    payload = purged.json()
    assert payload["purged_files"] == 1
    assert payload["hash_unavailable_files"] == 1, "md5 cannot be compared with a SHA-256 baseline"


def test_special_cache_under_files_can_be_quarantined(env):
    """issue #1: the executor re-guards every child file, so a cache below
    files/ must stay executable end to end, not just visible in the report."""
    cache = "/sdcard/Android/data/com.installed.app/files/GPUCache"
    env.phone.file(cache + "/data_0", size=9_000)
    env.prepare_analysis()
    item = env.require_item("app_cache")
    assert item["primary_path"] == cache

    env.plan_now(env.write_review())
    env.select_ids(item["id"])
    env.execute_ok()

    assert not env.phone.exists(cache + "/data_0")
    assert env.phone.exists("/sdcard/Android/data/com.installed.app/files")
