"""Shared harness for the android-deep-clean (ADC) pytest suite.

The harness builds a device-like directory tree in a temporary directory and
runs the real CLI (python android-deep-clean/scripts/adc.py ...) against it
through the mock transport. No adb, no Android device and no network are
required.

Device path mapping (mirrors adc_lib.mock_transport):

    /sdcard/...            -> <tmp>/phone/sdcard/...
    /storage/emulated/0/.. -> <tmp>/phone/sdcard/...
    /data/...              -> <tmp>/phone/data/...
    /system/...            -> <tmp>/phone/system/...

See tests/fixtures/README.md for the fixture patterns used by the suite.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "android-deep-clean" / "scripts"
ADC_ENTRY = SCRIPTS_DIR / "adc.py"
TESTS_DIR = Path(__file__).resolve().parent

# Make both the product package and this helper module importable from tests.
for _path in (str(SCRIPTS_DIR), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# All fixtures are built relative to a single reference instant. "old" files
# use ages far above the tiny thresholds below so tests never sit on a boundary.
NOW = time.time()

DEFAULT_THRESHOLDS: dict[str, int] = {
    "min_duplicate_bytes": 100,
    "large_file_bytes": 1_000_000,
    "old_file_days": 1,
    "apk_old_days": 1,
    "archive_old_days": 1,
    "temp_old_days": 1,
    "log_old_days": 1,
    "screenshot_old_days": 1,
    "media_old_days": 1,
    "trash_old_days": 1,
    "empty_dir_old_days": 1,
    "download_old_days": 1,
    "chat_cache_old_days": 1,
    "browser_cache_old_days": 1,
    "min_archive_bytes": 100,
    "min_download_bytes": 100,
    "min_media_bytes": 100,
    "large_cache_caution_bytes": 10_000_000,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "schema": 1,
    "thresholds": dict(DEFAULT_THRESHOLDS),
    "scan": {
        "max_files": 100_000,
        "max_depth": 64,
        "max_hash_files": 500,
        "max_hash_total_bytes": 200_000_000,
        "max_candidates": 500,
        "max_duplicate_groups": 100,
        "max_large_files": 200,
        "deadline_seconds": 120,
        "include_internal": "auto",
    },
    "report": {"sample_paths": 20, "max_detail_items": 120, "top_protected": 15},
    "safety": {"leftover_age_hours": 1, "max_files_per_selection": 200000, "require_ai_review_for_caution": True,
               "generic_cache_risk": "safe"},
    "exclude_paths": ["/sdcard/.android-deep-clean", "/sdcard/Android/.android-deep-clean"],
    "extra_protected_paths": [],
}

DEFAULT_DEVICE: dict[str, Any] = {
    "serial": "MOCK-ADC-0001",
    "sdk": 34,
    "release": "14",
    "manufacturer": "MockCorp",
    "model": "MockPhone",
    "build_type": "userdebug",
    "abi": "arm64-v8a",
    "product": "mock_product",
    "fingerprint": "mock/MockPhone/mock:14/UP1A/1:userdebug/test-keys",
}

DEFAULT_PACKAGES: dict[str, Any] = {
    "installed": ["com.installed.app"],
    "uninstalled": [],
    "system": ["android"],
    "available": True,
}

DEFAULT_CAPS: dict[str, Any] = {
    "privilege": "shell",
    "commands": {},
    "df": {"total_bytes": 128_000_000_000, "used_bytes": 90_000_000_000, "available_bytes": 38_000_000_000},
}

PROBE_COMMANDS = ["find", "stat", "xargs", "sha256sum", "df", "pm", "realpath", "rm", "mv", "rmdir", "su"]


# --------------------------------------------------------------------------
# deterministic content helpers
# --------------------------------------------------------------------------
def generated_bytes(size: int, seed: Any = 0) -> bytes:
    """Deterministic pseudo-random bytes so different files rarely collide."""
    buf = bytearray()
    counter = 0
    while len(buf) < size:
        buf.extend(hashlib.sha256(f"{seed}:{counter}".encode("utf-8")).digest())
        counter += 1
    return bytes(buf[:size])


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# device tree builder
# --------------------------------------------------------------------------
class PhoneTree:
    """Builds a mock Android file tree and exposes path helpers."""

    MOUNT_ALIASES = {
        "/storage/emulated/0": "/sdcard",
        "/storage/self/primary": "/sdcard",
        "/mnt/sdcard": "/sdcard",
    }

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        (self.root / "sdcard").mkdir(parents=True, exist_ok=True)
        (self.root / "data").mkdir(parents=True, exist_ok=True)

    # -- mapping -----------------------------------------------------------
    def host(self, device_path: str) -> Path:
        path = str(device_path)
        for alias, canonical in self.MOUNT_ALIASES.items():
            if path == alias or path.startswith(alias + "/"):
                path = canonical + path[len(alias):]
                break
        if not path.startswith("/"):
            raise ValueError(f"device path must be absolute: {device_path!r}")
        if path == "/":
            return self.root
        if path == "/sdcard" or path.startswith("/sdcard/"):
            rel = path[len("/sdcard"):].lstrip("/")
            return self.root / "sdcard" / rel if rel else self.root / "sdcard"
        if path == "/data" or path.startswith("/data/"):
            rel = path[len("/data"):].lstrip("/")
            return self.root / "data" / rel if rel else self.root / "data"
        parts = [p for p in path.split("/") if p]
        return self.root.joinpath(*parts)

    # -- creation ----------------------------------------------------------
    def file(
        self,
        device_path: str,
        *,
        size: int | None = None,
        content: str | bytes | None = None,
        age_days: float = 5.0,
        mtime: float | None = None,
    ) -> Path:
        host = self.host(device_path)
        host.parent.mkdir(parents=True, exist_ok=True)
        if content is not None:
            data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        else:
            data = generated_bytes(int(size or 0), seed=device_path)
        host.write_bytes(data)
        stamp = mtime if mtime is not None else NOW - float(age_days) * 86400.0
        os.utime(host, (stamp, stamp))
        return host

    def dir(self, device_path: str, *, age_days: float = 5.0) -> Path:
        host = self.host(device_path)
        host.mkdir(parents=True, exist_ok=True)
        stamp = NOW - float(age_days) * 86400.0
        os.utime(host, (stamp, stamp))
        return host

    def set_age(self, device_path: str, age_days: float = 5.0) -> None:
        host = self.host(device_path)
        stamp = NOW - float(age_days) * 86400.0
        os.utime(host, (stamp, stamp))

    def set_mtime(self, device_path: str, mtime: float) -> None:
        os.utime(self.host(device_path), (mtime, mtime))

    def remove(self, device_path: str) -> None:
        host = self.host(device_path)
        if host.is_dir():
            import shutil

            shutil.rmtree(host)
        else:
            host.unlink()

    def exists(self, device_path: str) -> bool:
        return self.host(device_path).exists()

    # -- inspection --------------------------------------------------------
    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Hash/mtime/size manifest of the whole tree (read-only assertions)."""
        manifest: dict[str, dict[str, Any]] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames.sort()
            filenames.sort()
            current = Path(dirpath)
            rel_dir = os.path.relpath(current, self.root).replace("\\", "/")
            stat = current.lstat()
            manifest[rel_dir] = {"kind": "dir", "mtime_ns": stat.st_mtime_ns}
            for name in filenames:
                file_path = current / name
                stat = file_path.lstat()
                rel = f"{rel_dir}/{name}" if rel_dir != "." else name
                manifest[rel] = {
                    "kind": "file",
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": sha256_path(file_path),
                }
        return manifest

    def files(self) -> list[str]:
        return sorted(key for key, value in self.snapshot().items() if value["kind"] == "file")


def assert_tree_unchanged(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> None:
    """Fail with a readable diff when a supposedly read-only step changed files."""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = []
    for key in sorted(set(before) & set(after)):
        if before[key] != after[key]:
            changed.append((key, before[key], after[key]))
    if added or removed or changed:
        lines = ["fixture tree changed unexpectedly", f"added: {added[:10]}", f"removed: {removed[:10]}"]
        for key, old, new in changed[:10]:
            lines.append(f"changed: {key}: {old} -> {new}")
        pytest.fail("\n".join(lines))


# --------------------------------------------------------------------------
# CLI result wrapper
# --------------------------------------------------------------------------
class CliResult:
    def __init__(self, returncode: int, stdout: str, stderr: str, argv: Sequence[str]) -> None:
        self.returncode = int(returncode)
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.argv = [str(item) for item in argv]

    def json(self) -> Any:
        text = self.stdout.strip()
        if not text:
            raise AssertionError(f"expected JSON on stdout, got nothing\nargv={self.argv}\nstderr={self.stderr[-2000:]}")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"stdout is not JSON ({exc})\nargv={self.argv}\nstdout={self.stdout[-2000:]}\nstderr={self.stderr[-2000:]}"
            ) from exc

    def expect(self, code: int = 0) -> "CliResult":
        if self.returncode != code:
            pytest.fail(
                f"command failed: expected rc={code}, got {self.returncode}\n"
                f"argv={self.argv}\n--- stdout ---\n{self.stdout[-4000:]}\n--- stderr ---\n{self.stderr[-4000:]}"
            )
        return self

    def expect_error(self, *codes: int) -> "CliResult":
        if self.returncode not in codes:
            pytest.fail(
                f"command should fail with rc in {codes}, got {self.returncode}\n"
                f"argv={self.argv}\n--- stdout ---\n{self.stdout[-4000:]}\n--- stderr ---\n{self.stderr[-4000:]}"
            )
        return self

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"CliResult(rc={self.returncode}, argv={self.argv})"


# --------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------
class AdcEnv:
    """A complete mock-device test environment in one temporary directory."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = Path(tmp_path)
        self.phone = PhoneTree(self.tmp / "phone")
        self.state_dir = self.tmp / "state"
        self.config: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.packages: dict[str, Any] = copy.deepcopy(DEFAULT_PACKAGES)
        self.caps: dict[str, Any] = copy.deepcopy(DEFAULT_CAPS)
        self.device: dict[str, Any] = copy.deepcopy(DEFAULT_DEVICE)
        self.fixture_dir = self.tmp / "fixtures"
        self.fixture_dir.mkdir(parents=True, exist_ok=True)
        self.flush()

    # -- fixture files -----------------------------------------------------
    @property
    def config_path(self) -> Path:
        return self.fixture_dir / "config.json"

    @property
    def packages_path(self) -> Path:
        return self.fixture_dir / "packages.json"

    @property
    def caps_path(self) -> Path:
        return self.fixture_dir / "caps.json"

    @property
    def device_path(self) -> Path:
        return self.fixture_dir / "device.json"

    def flush(self) -> None:
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False, indent=2), encoding="utf-8")
        self.packages_path.write_text(json.dumps(self.packages, ensure_ascii=False, indent=2), encoding="utf-8")
        self.caps_path.write_text(json.dumps(self.caps, ensure_ascii=False, indent=2), encoding="utf-8")
        self.device_path.write_text(json.dumps(self.device, ensure_ascii=False, indent=2), encoding="utf-8")

    # -- capability helpers ------------------------------------------------
    def set_caps(self, **overrides: Any) -> None:
        self.caps.update(overrides)
        self.flush()

    def set_commands(self, **commands: Any) -> None:
        self.caps.setdefault("commands", {})
        self.caps["commands"].update(commands)
        self.flush()

    def set_packages(self, **overrides: Any) -> None:
        self.packages.update(overrides)
        self.flush()

    def set_config(self, **overrides: Any) -> None:
        self.config = deep_merge(self.config, overrides)
        self.flush()

    # -- CLI invocation ----------------------------------------------------
    def build_argv(
        self,
        command: str | Sequence[str],
        extra: Iterable[Any] = (),
        *,
        want_json: bool = True,
    ) -> list[str]:
        command_path = [command] if isinstance(command, str) else list(command)
        argv = [
            sys.executable,
            str(ADC_ENTRY),
            *[str(item) for item in command_path],
            "--state",
            str(self.state_dir),
            "--transport",
            "mock",
            "--mock-root",
            str(self.phone.root),
            "--mock-packages",
            str(self.packages_path),
            "--mock-caps",
            str(self.caps_path),
            "--mock-device",
            str(self.device_path),
            "--config",
            str(self.config_path),
        ]
        if want_json:
            argv.append("--json")
        argv.extend(str(item) for item in extra)
        return argv

    def run(self, command: str | Sequence[str], *extra: Any, want_json: bool = True) -> CliResult:
        self.flush()
        argv = self.build_argv(command, extra, want_json=want_json)
        env = dict(os.environ)
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        env.pop("ADC_STATE", None)
        proc = subprocess.run(
            argv,
            cwd=str(self.tmp),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=env,
        )
        return CliResult(proc.returncode, proc.stdout, proc.stderr, argv)

    def run_raw(self, argv: Iterable[Any]) -> CliResult:
        """Run the CLI with a fully custom argv (no automatic common options)."""
        full = [sys.executable, str(ADC_ENTRY), *[str(item) for item in argv]]
        env = dict(os.environ)
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        env.pop("ADC_STATE", None)
        proc = subprocess.run(
            full,
            cwd=str(self.tmp),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=env,
        )
        return CliResult(proc.returncode, proc.stdout, proc.stderr, full)

    def ok(self, command: str | Sequence[str], *extra: Any, want_json: bool = True) -> CliResult:
        return self.run(command, *extra, want_json=want_json).expect(0)

    def err(self, command: str | Sequence[str], *extra: Any, rc: int | tuple[int, ...] = (2, 3, 5, 6)) -> CliResult:
        codes = (rc,) if isinstance(rc, int) else tuple(rc)
        return self.run(command, *extra).expect_error(*codes)

    # -- state readers -----------------------------------------------------
    def state_json(self, relative: str) -> Any:
        path = self.state_dir / relative
        if not path.exists():
            pytest.fail(
                f"expected state file missing: {path}\n"
                f"state dir listing: {sorted(str(p) for p in (self.state_dir).rglob('*'))}"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def state_path(self, relative: str) -> Path:
        return self.state_dir / relative

    @property
    def analysis(self) -> dict[str, Any]:
        return self.state_json("analysis/analysis.json")

    @property
    def scan_meta(self) -> dict[str, Any]:
        return self.state_json("scan/snapshot-meta.json")

    @property
    def doctor(self) -> dict[str, Any]:
        return self.state_json("doctor/doctor.json")

    @property
    def plan(self) -> dict[str, Any]:
        return self.state_json("plan/plan.json")

    @property
    def selection(self) -> dict[str, Any]:
        return self.state_json("selection/selection.json")

    @property
    def execution(self) -> dict[str, Any]:
        return self.state_json("execution/execution.json")

    @property
    def verification(self) -> dict[str, Any]:
        return self.state_json("execution/verification.json")

    def analysis_items(self, category: str | None = None) -> list[dict[str, Any]]:
        items = list(self.analysis.get("items") or [])
        if category is not None:
            items = [item for item in items if item.get("category") == category]
        return items

    def item(self, category: str, *, predicate: Any = None) -> dict[str, Any] | None:
        for item in self.analysis_items(category):
            if predicate is None or predicate(item):
                return item
        return None

    def require_item(self, category: str, *, predicate: Any = None) -> dict[str, Any]:
        item = self.item(category, predicate=predicate)
        if item is None:
            pytest.fail(
                f"no analysis candidate in category {category!r}; "
                f"candidates={[(i.get('category'), i.get('primary_path')) for i in self.analysis_items()]}"
            )
        return item

    def candidate_files(self, candidate_id: str) -> list[dict[str, Any]]:
        db = self.state_dir / "analysis" / "analysis.db"
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM candidate_files WHERE candidate_id=? ORDER BY path", (candidate_id,)
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def all_candidate_paths(self) -> set[str]:
        db = self.state_dir / "analysis" / "analysis.db"
        conn = sqlite3.connect(str(db))
        try:
            rows = conn.execute("SELECT DISTINCT path FROM candidate_files").fetchall()
        finally:
            conn.close()
        return {row[0] for row in rows}

    def analysis_db_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.state_dir / "analysis" / "analysis.db"))
        conn.row_factory = sqlite3.Row
        return conn

    # -- flow helpers ------------------------------------------------------
    def prepare_analysis(self) -> dict[str, Any]:
        """doctor -> scan -> analyze; returns the analysis payload."""
        self.ok("doctor")
        self.ok("scan")
        self.ok("analyze")
        return self.analysis

    def write_review(
        self,
        decisions: list[dict[str, Any]] | None = None,
        *,
        verdict: str = "trash",
        reason: str = "测试复核：已检查候选路径与文件清单，确认可清理。",
        analysis_id: str | None = None,
        filename: str = "review.json",
        include_ids: Iterable[str] | None = None,
    ) -> Path | None:
        analysis = self.analysis
        if decisions is None:
            review_items = analysis.get("review_items") or []
            if include_ids is not None:
                wanted = set(include_ids)
                review_items = [item for item in review_items if item.get("id") in wanted]
            if not review_items:
                return None
            decisions = [
                {"id": item["id"], "verdict": verdict, "reason": reason} for item in review_items
            ]
        payload = {
            "analysis_id": analysis_id if analysis_id is not None else analysis.get("analysis_id"),
            "decisions": decisions,
        }
        path = self.fixture_dir / filename
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def plan_now(self, review: Path | str | None | bool = "auto") -> dict[str, Any]:
        extra: list[Any] = []
        if review == "auto":
            review_path = self.write_review()
            if review_path is not None:
                extra = ["--review", str(review_path)]
        elif review:
            extra = ["--review", str(review)]
        self.ok("plan", *extra)
        return self.plan

    def select(self, *extra: Any) -> CliResult:
        return self.ok("select", *extra)

    def select_ids(self, *ids: str, mode: str | None = None) -> dict[str, Any]:
        extra: list[Any] = ["--ids", ",".join(ids)]
        if mode:
            extra += ["--mode", mode]
        return self.select(*extra).json()

    def select_all_safe(self, *, mode: str | None = None) -> dict[str, Any]:
        extra: list[Any] = ["--all-safe"]
        if mode:
            extra += ["--mode", mode]
        return self.select(*extra).json()

    def execute(
        self,
        token: str | None = None,
        *,
        mode: str | None = None,
        allow_permanent: bool = False,
    ) -> CliResult:
        if token is None:
            token = str(self.selection.get("token") or "")
        extra: list[Any] = ["--token", token]
        if mode:
            extra += ["--mode", mode]
        if allow_permanent:
            extra.append("--allow-permanent-delete")
        return self.run("execute", *extra)

    def execute_ok(self, token: str | None = None, *, mode: str | None = None, allow_permanent: bool = False) -> CliResult:
        return self.execute(token, mode=mode, allow_permanent=allow_permanent).expect(0)

    def run_full_quarantine(self, *, review: bool = False) -> dict[str, Any]:
        """analyze -> plan(optional review) -> select all safe -> execute."""
        self.prepare_analysis()
        self.plan_now(review if review else None)
        self.select_all_safe()
        self.execute_ok()
        return self.execution

    # -- read-only assertions ---------------------------------------------
    def snapshot(self) -> dict[str, dict[str, Any]]:
        return self.phone.snapshot()

    def assert_unchanged(self, before: dict[str, dict[str, Any]]) -> None:
        assert_tree_unchanged(before, self.phone.snapshot())


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def find_quarantine_file(env: "AdcEnv", device_path: str) -> Path | None:
    """Map an original device path to its file inside the quarantine dir."""
    execution = env.execution
    root = str(execution.get("quarantine_root") or "")
    if not root:
        return None
    if device_path.startswith("/sdcard/"):
        rel = device_path[len("/sdcard/"):]
        return env.phone.host(root) / "files" / "sdcard" / rel
    return None


def journal_entries(env: "AdcEnv") -> list[dict[str, Any]]:
    path = Path(str(env.execution.get("journal_path") or ""))
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


def quarantine_file_paths(env: "AdcEnv") -> list[Path]:
    """All regular files currently stored in the quarantine area."""
    execution = env.execution
    roots = [str(r) for r in (execution.get("quarantine_roots") or []) if r]
    if execution.get("quarantine_root"):
        roots.append(str(execution["quarantine_root"]))
    found: list[Path] = []
    for root in sorted(set(roots)):
        host = env.phone.host(root)
        if host.is_dir():
            found.extend(sorted(p for p in host.rglob("*") if p.is_file()))
    return found
