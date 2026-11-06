"""Unit tests: PC backup safety paths and mid-run disconnect aborting.

These use direct imports of the executor with stub transports, so they stay
deterministic on Windows/mock without real adb.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from adc_lib.config import load_config
from adc_lib.executor import Executor
from adc_lib.mock_transport import MockTransport
from adc_lib.models import PackageIndex, StatInfo
from adc_lib.state import State
from adc_lib.transport import TransportError
from adc_lib.util import SafetyError, sha256_text

from adc_testkit import generated_bytes, journal_entries

CACHE = "/sdcard/Android/data/com.installed.app/cache"
ITEM = {"id": "app_cache-test0001", "category": "app_cache", "action": "delete_files"}


class StubTransport:
    def __init__(
        self,
        *,
        content: bytes = b"A" * 300,
        pull_error: Exception | None = None,
        pull_errors_by_path=None,
        pull_written: int | None = None,
        hash_override: str | None = None,
    ):
        self.content = content
        self.pull_error = pull_error
        self.pull_errors_by_path = pull_errors_by_path or {}
        self.pull_written = pull_written
        self.hash_override = hash_override
        self.deleted: list[str] = []
        self.deleted_paths: set[str] = set()
        self.moved: list[tuple[str, str]] = []
        self.pulled: list[tuple[str, str]] = []

    def stat(self, path):
        if path in self.deleted_paths:
            return None
        return StatInfo(path=path, kind="file", size=len(self.content), mtime=100, inode=7, dev=1)

    def realpath(self, path):
        return path

    def pull(self, src, dst, *, max_bytes=None):
        self.pulled.append((src, dst))
        if self.pull_error is not None:
            raise self.pull_error
        if src in self.pull_errors_by_path:
            raise self.pull_errors_by_path[src]
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(self.content)
        return self.pull_written if self.pull_written is not None else len(self.content)

    def hash_files(self, paths, *, timeout=600):
        digest = self.hash_override or hashlib.sha256(self.content).hexdigest()
        return {path: digest for path in paths}

    def health_ok(self):
        return True

    def delete_file(self, path):
        self.deleted.append(path)
        self.deleted_paths.add(path)

    def move(self, src, dst):
        self.moved.append((src, dst))

    def mkdirs(self, path):
        return None

    def df(self, path="/sdcard"):
        return {}


def _executor(tmp_path, transport, *, with_backup=True):
    executor = Executor.__new__(Executor)
    executor.transport = transport
    executor.config = load_config()
    executor.packages = PackageIndex(installed={"com.installed.app"}, available=True)
    executor.failures = []
    executor.backup_manifest = []
    executor.backup_bytes = 0
    executor.backup_dir = (tmp_path / "pc-backup").resolve() if with_backup else None
    executor.backup_root = (executor.backup_dir / "exec-unit") if with_backup else None
    executor.quarantine_roots = set()
    executor.journal_path = tmp_path / "journal.jsonl"
    return executor


def _manifest(size: int = 300, paths=None):
    paths = paths or [CACHE + "/a.bin"]
    return [
        {"path": path, "size": size, "mtime": 100, "inode": 7, "kind": "file", "role": "delete"}
        for path in paths
    ]


def _backup_dest(executor, name="a.bin") -> Path:
    return executor.backup_root / "sdcard" / "Android" / "data" / "com.installed.app" / "cache" / name


def test_backup_success_records_manifest_and_deletes_after(tmp_path):
    transport = StubTransport()
    executor = _executor(tmp_path, transport)
    result = executor._execute_item(None, ITEM, _manifest(), "delete", "")

    assert result["status"] == "success"
    assert result["success_count"] == 1
    assert result["failed_count"] == 0
    assert transport.deleted == [CACHE + "/a.bin"]
    dest = _backup_dest(executor)
    assert dest.is_file()
    assert dest.read_bytes() == transport.content
    assert executor.backup_bytes == 300
    assert len(executor.backup_manifest) == 1
    entry = executor.backup_manifest[0]
    assert entry["path"] == CACHE + "/a.bin"
    assert entry["sha256"] == hashlib.sha256(transport.content).hexdigest()
    assert entry["verified_by"] == "sha256"


def test_backup_pull_failure_skips_cleanup_and_keeps_source(tmp_path):
    transport = StubTransport(pull_error=TransportError("mock: 无法拉取"))
    executor = _executor(tmp_path, transport)
    result = executor._execute_item(None, ITEM, _manifest(), "delete", "")

    assert result["failed_count"] == 1
    assert result["success_count"] == 0
    assert transport.deleted == [], "cleanup must not run when the backup failed"
    assert not _backup_dest(executor).exists()
    reasons = " | ".join(failure["reason"] for failure in executor.failures)
    assert "备份到电脑失败" in reasons


def test_backup_size_mismatch_removes_partial_copy(tmp_path):
    transport = StubTransport(pull_written=5)
    executor = _executor(tmp_path, transport)
    result = executor._execute_item(None, ITEM, _manifest(), "delete", "")

    assert result["failed_count"] == 1
    assert transport.deleted == []
    assert not _backup_dest(executor).exists()
    reasons = " | ".join(failure["reason"] for failure in executor.failures)
    assert "备份大小不一致" in reasons


def test_backup_sha_mismatch_removes_partial_copy(tmp_path):
    transport = StubTransport(hash_override="0" * 64)
    executor = _executor(tmp_path, transport)
    result = executor._execute_item(None, ITEM, _manifest(), "delete", "")

    assert result["failed_count"] == 1
    assert transport.deleted == []
    assert not _backup_dest(executor).exists()
    reasons = " | ".join(failure["reason"] for failure in executor.failures)
    assert "SHA-256" in reasons


def test_backup_failure_only_affects_the_failed_file(tmp_path):
    first = CACHE + "/ok.bin"
    second = CACHE + "/bad.bin"
    transport = StubTransport(pull_errors_by_path={second: TransportError("denied")})
    executor = _executor(tmp_path, transport)
    result = executor._execute_item(None, ITEM, _manifest(paths=[first, second]), "delete", "")

    assert result["success_count"] == 1
    assert result["failed_count"] == 1
    assert transport.deleted == [first]
    assert _backup_dest(executor, "ok.bin").is_file()
    assert not _backup_dest(executor, "bad.bin").exists()


class PartialPullTransport(StubTransport):
    """Simulates a USB disconnect after part of the file was copied."""

    def pull(self, src, dst, *, max_bytes=None):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(self.content[:100])
        raise TransportError("device disconnected mid-pull")


def test_backup_partial_copy_is_cleaned_up(tmp_path):
    transport = PartialPullTransport()
    executor = _executor(tmp_path, transport)
    result = executor._execute_item(None, ITEM, _manifest(), "delete", "")

    assert result["failed_count"] == 1
    assert transport.deleted == [], "source must stay on the phone"
    assert not _backup_dest(executor).exists(), "failed backup must not leave a partial PC copy"


def test_backup_rejects_unsupported_source_paths(tmp_path):
    transport = StubTransport()
    executor = _executor(tmp_path, transport)
    with pytest.raises(SafetyError):
        executor._backup_file("/system/bin/toybox", {"path": "/system/bin/toybox", "size": 10}, "item-1")


# ---------------------------------------------------------------- mid-run disconnect
class _HealthWrapper:
    """Delegates to the real mock transport but simulates a USB disconnect."""

    def __init__(self, inner, fail_after: int) -> None:
        self._inner = inner
        self._fail_after = fail_after
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def health_ok(self) -> bool:
        self.calls += 1
        return self.calls <= self._fail_after


def test_mid_run_disconnect_aborts_remaining_items(env):
    env.set_packages(installed=["com.installed.app", "com.other.app"], uninstalled=[], system=[], available=True)
    first_file = "/sdcard/Android/data/com.other.app/cache/bigger.bin"
    second_file = CACHE + "/smaller.bin"
    env.phone.file(first_file, size=400)
    env.phone.file(second_file, size=300)

    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    assert len(selection["items"]) == 2, selection["items"]

    inner = MockTransport(
        mock_root=str(env.phone.root),
        packages_path=str(env.packages_path),
        caps_path=str(env.caps_path),
        device_path=str(env.device_path),
    )
    wrapper = _HealthWrapper(inner, fail_after=2)  # allow preflight + first item, then disconnect
    executor = Executor(wrapper, load_config(str(env.config_path)), State(str(env.state_dir)))
    execution = executor.run(env.plan, env.selection, env.selection["token"], env.selection["mode"], False)

    assert execution["status"] == "aborted"
    assert "断开" in execution["abort_reason"]
    assert execution["summary"]["aborted_groups"] == 1
    statuses = [item["status"] for item in execution["items"]]
    assert statuses == ["success", "aborted"], statuses
    assert execution["summary"]["success_files"] == 1

    # The first item was cleaned, the aborted one must be untouched.
    assert not env.phone.exists(first_file)
    assert env.phone.exists(second_file)

    quarantined = [entry for entry in journal_entries(env) if entry["event"] == "quarantined"]
    assert len(quarantined) == 1
    assert quarantined[0]["path"] == first_file
    # The abort is surfaced in the report, not silent.
    report = env.state_path("execution/report-after.md").read_text(encoding="utf-8")
    assert "中止" in report or "aborted" in report or "断开" in report

class _DisconnectOnMoveWrapper(_HealthWrapper):
    """Healthy during preflight, then the device vanishes during the first move."""

    def __init__(self, inner, fail_after: int = 2) -> None:
        super().__init__(inner, fail_after)
        self.move_attempts = 0

    def move(self, src, dst):
        self.move_attempts += 1
        raise TransportError("device lost during move")


def test_device_lost_during_move_aborts_and_keeps_files(env):
    env.set_packages(installed=["com.installed.app", "com.other.app"], uninstalled=[], system=[], available=True)
    first_file = "/sdcard/Android/data/com.other.app/cache/bigger.bin"
    second_file = CACHE + "/smaller.bin"
    env.phone.file(first_file, size=400)
    env.phone.file(second_file, size=300)
    env.prepare_analysis()
    env.plan_now(None)
    env.select_all_safe()

    inner = MockTransport(
        mock_root=str(env.phone.root),
        packages_path=str(env.packages_path),
        caps_path=str(env.caps_path),
        device_path=str(env.device_path),
    )
    wrapper = _DisconnectOnMoveWrapper(inner, fail_after=2)
    executor = Executor(wrapper, load_config(str(env.config_path)), State(str(env.state_dir)))
    execution = executor.run(env.plan, env.selection, env.selection["token"], env.selection["mode"], False)

    assert wrapper.move_attempts == 1
    assert execution["status"] == "aborted"
    assert "断开" in execution["abort_reason"]
    assert execution["summary"]["aborted_groups"] == 2
    statuses = [item["status"] for item in execution["items"]]
    assert statuses == ["aborted", "aborted"], statuses
    assert env.phone.exists(first_file)
    assert env.phone.exists(second_file)
    assert env.state_path("execution/execution.json").exists()
    report = env.state_path("execution/report-after.md").read_text(encoding="utf-8")
    assert "中止" in report or "aborted" in report or "断开" in report
