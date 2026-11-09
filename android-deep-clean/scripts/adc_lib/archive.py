"""Cross-round directory archive.

The archive is a compact host-side SQLite registry.  It is deliberately
separate from the per-scan snapshot: snapshots hold every file and are rotated,
while the archive keeps only directory-level facts needed to answer "what
changed since last time" across many rounds.

Safety contract: this module never touches the device and its marks are
evidence only.  They do not authorize deletion or change any cleanup risk.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .models import PackageIndex
from .rules import package_from_path
from .util import (
    ValidationError,
    atomic_write_json,
    human_bytes,
    is_under,
    normalize_device_path,
    now_iso,
    stable_id,
)

ARCHIVE_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS environments (
    env_id TEXT PRIMARY KEY,
    serial TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    fingerprint TEXT NOT NULL DEFAULT '',
    sdk INTEGER NOT NULL DEFAULT 0,
    platform TEXT NOT NULL DEFAULT '',
    android_user TEXT NOT NULL DEFAULT '',
    first_seen_at TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS directories (
    env_id TEXT NOT NULL,
    volume TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL,
    parent TEXT NOT NULL DEFAULT '',
    depth INTEGER NOT NULL DEFAULT 0,
    direct_bytes INTEGER NOT NULL DEFAULT 0,
    subtree_bytes INTEGER NOT NULL DEFAULT 0,
    subtree_files INTEGER NOT NULL DEFAULT 0,
    newest_mtime INTEGER,
    first_seen_at TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT NOT NULL DEFAULT '',
    first_seen_scan TEXT NOT NULL DEFAULT '',
    last_seen_scan TEXT NOT NULL DEFAULT '',
    absent_rounds INTEGER NOT NULL DEFAULT 0,
    resurrect_count INTEGER NOT NULL DEFAULT 0,
    last_resurrected_scan TEXT NOT NULL DEFAULT '',
    app_pkg TEXT NOT NULL DEFAULT '',
    app_version TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (env_id, volume, path)
);
CREATE INDEX IF NOT EXISTS idx_directories_path ON directories(env_id, path);
CREATE INDEX IF NOT EXISTS idx_directories_size ON directories(env_id, subtree_bytes);
CREATE TABLE IF NOT EXISTS packages (
    env_id TEXT NOT NULL,
    package TEXT NOT NULL,
    installed INTEGER NOT NULL DEFAULT 0,
    system INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL DEFAULT '',
    last_seen_at TEXT NOT NULL DEFAULT '',
    last_seen_installed_at TEXT NOT NULL DEFAULT '',
    uninstalled_since_at TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT '',
    previous_version TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (env_id, package)
);
CREATE TABLE IF NOT EXISTS scans (
    scan_id TEXT PRIMARY KEY,
    env_id TEXT NOT NULL DEFAULT '',
    scanned_at TEXT NOT NULL DEFAULT '',
    files INTEGER NOT NULL DEFAULT 0,
    logical_bytes INTEGER NOT NULL DEFAULT 0,
    unique_bytes INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
    complete INTEGER NOT NULL DEFAULT 0,
    stats_json TEXT NOT NULL DEFAULT '{}'
);
"""

VALID_DECISIONS = {"keep", "watch", "neutral"}
APP_DIR_PARENTS = {
    "/sdcard/Android/data",
    "/sdcard/Android/obb",
    "/sdcard/Android/media",
}


def _volume_for_path(path: str) -> str:
    if path == "/sdcard" or is_under(path, "/sdcard"):
        return "sdcard"
    if path == "/data" or is_under(path, "/data"):
        return "internal"
    parts = [part for part in path.split("/") if part]
    return parts[0] if parts else "root"


def _environment(scan_meta: dict[str, Any]) -> dict[str, Any]:
    device = scan_meta.get("device") or {}
    identity = scan_meta.get("device_identity") or {}
    caps = scan_meta.get("capabilities") or {}
    serial = str(identity.get("serial") or device.get("serial") or "unknown")
    model = str(device.get("model") or identity.get("model") or "")
    fingerprint = str(device.get("fingerprint") or identity.get("fingerprint") or "")
    sdk = int(device.get("sdk") or identity.get("sdk") or 0)
    platform = str(scan_meta.get("platform") or caps.get("platform") or "android")
    user = str(caps.get("android_user") or "")
    transport = str(scan_meta.get("transport") or caps.get("transport") or "")
    env_id = stable_id("env", serial, model, fingerprint, sdk, platform, user)
    return {
        "env_id": env_id,
        "serial": serial,
        "model": model,
        "fingerprint": fingerprint,
        "sdk": sdk,
        "platform": platform,
        "android_user": user,
        "identity_uncertain": serial in {"", "unknown"} or transport == "hdc",
    }


def _scan_complete(scan_meta: dict[str, Any]) -> bool:
    coverage = scan_meta.get("coverage") or {}
    if coverage:
        if coverage.get("truncated") or coverage.get("depth_limited"):
            return False
        if str(coverage.get("status") or "") in {"partial", "suspect"}:
            return False
        if coverage.get("root_status"):
            if any(item.get("state") != "scanned" for item in coverage["root_status"]):
                return False
        elif coverage.get("roots_requested") != coverage.get("roots_scanned"):
            return False
        return True
    roots_requested = list(scan_meta.get("roots_requested") or [])
    roots_scanned = list(scan_meta.get("roots_scanned") or [])
    return not scan_meta.get("truncated") and roots_requested == roots_scanned


def _directory_rows(scan_db: Path) -> tuple[list[sqlite3.Row], str]:
    conn = sqlite3.connect(str(scan_db))
    conn.row_factory = sqlite3.Row
    try:
        tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "dir_stats" not in tables:
            return [], "快照缺少 dir_stats，请重新 scan"
        rows = conn.execute(
            """SELECT path,parent,depth,direct_bytes,subtree_bytes,subtree_files,newest_mtime
               FROM dir_stats ORDER BY subtree_bytes DESC, path"""
        ).fetchall()
        return rows, ""
    finally:
        conn.close()


class DirectoryArchive:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(ARCHIVE_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "DirectoryArchive":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    # -- meta -------------------------------------------------------------
    def _meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", (key, value))

    def latest_env_id(self) -> str:
        env_id = self._meta("last_env_id")
        if env_id:
            return env_id
        row = self.conn.execute(
            "SELECT env_id FROM environments ORDER BY last_seen_at DESC LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else ""

    # -- update -----------------------------------------------------------
    def update(
        self,
        scan_db: str | Path,
        scan_meta: dict[str, Any],
        packages: PackageIndex | None,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        archive_config = config.get("archive") or {}
        min_bytes = int(archive_config.get("min_dir_bytes", 10 * 1024 * 1024))
        grow_bytes = int(archive_config.get("grow_bytes", 50 * 1024 * 1024))
        grow_percent = int(archive_config.get("grow_percent", 20))
        max_entries = int(archive_config.get("max_entries", 50000))
        max_diff_items = int(archive_config.get("max_diff_items", 200))

        env = _environment(scan_meta)
        env_id = env["env_id"]
        scanned_at = str(scan_meta.get("scanned_at") or now_iso())
        scan_id = stable_id("scan", scanned_at, env_id)
        previous_env = self._meta("last_env_id")
        environment_changed = bool(previous_env and previous_env != env_id)
        scan_complete = _scan_complete(scan_meta)
        rows, row_error = _directory_rows(Path(scan_db))
        limitations: list[str] = []
        if row_error:
            limitations.append(row_error)
            scan_complete = False
        if not scan_complete:
            limitations.append("本轮扫描不完整：消失类变化不会计入，避免把未扫描区域误判为已删除。")
        if env["identity_uncertain"]:
            limitations.append("设备序列号不可用，目录档案的环境识别可能不稳定。")
        if environment_changed:
            limitations.append("设备环境发生变化（系统/用户/指纹），本轮按新基线处理。")

        roots = set(scan_meta.get("roots_requested") or [])
        filtered: list[sqlite3.Row] = []
        for row in rows:
            path = str(row["path"])
            size = int(row["subtree_bytes"] or 0)
            parent = str(row["parent"] or "")
            if size >= min_bytes or path in roots or parent in APP_DIR_PARENTS:
                filtered.append(row)
        entries_truncated = len(filtered) > max_entries
        if entries_truncated:
            filtered = filtered[:max_entries]
            limitations.append(f"目录档案达到上限 {max_entries}，较小的目录未纳入跨轮跟踪；本轮不做消失判断。")
        archive_complete = scan_complete and not entries_truncated

        old_rows: dict[str, sqlite3.Row] = {}
        if not environment_changed:
            for row in self.conn.execute(
                "SELECT * FROM directories WHERE env_id=?", (env_id,)
            ).fetchall():
                old_rows[str(row["path"])] = row
        baseline = not old_rows or environment_changed
        current_paths: set[str] = set()

        new_items: list[dict[str, Any]] = []
        grew_items: list[dict[str, Any]] = []
        shrunk_items: list[dict[str, Any]] = []
        resurrected_items: list[dict[str, Any]] = []
        app_updated_items: list[dict[str, Any]] = []
        unreviewed_items: list[dict[str, Any]] = []
        orphan_items: list[dict[str, Any]] = []

        now = now_iso()
        self.conn.execute(
            """INSERT OR REPLACE INTO environments
            (env_id,serial,model,fingerprint,sdk,platform,android_user,first_seen_at,last_seen_at)
            VALUES (?,?,?,?,?,?,?,COALESCE((SELECT first_seen_at FROM environments WHERE env_id=?),?),?)""",
            (env_id, env["serial"], env["model"], env["fingerprint"], env["sdk"], env["platform"],
             env["android_user"], env_id, now, now),
        )

        for row in filtered:
            path = normalize_device_path(str(row["path"]))
            parent = str(row["parent"] or "")
            size = int(row["subtree_bytes"] or 0)
            files = int(row["subtree_files"] or 0)
            newest = row["newest_mtime"]
            pkg = package_from_path(path)
            version = packages.versions.get(pkg, "") if packages and pkg else ""
            old = old_rows.get(path)
            is_root = path in roots
            current_paths.add(path)
            item = {
                "path": path,
                "bytes": size,
                "human_size": human_bytes(size),
                "files": files,
                "newest_mtime": newest,
                "app_pkg": pkg,
                "app_version": version,
                "decision": str(old["decision"]) if old else "",
                "note": str(old["note"]) if old else "",
            }
            if old is None:
                first_seen_at = now
                first_seen_scan = scan_id
                last_seen_at = now
                absent_rounds = 0
                resurrect_count = 0
                last_resurrected = ""
                if not baseline and not is_root:
                    new_items.append(dict(item, first_seen_at=first_seen_at))
            else:
                first_seen_at = str(old["first_seen_at"] or now)
                first_seen_scan = str(old["first_seen_scan"] or "")
                last_seen_at = now
                absent_rounds = 0
                resurrect_count = int(old["resurrect_count"] or 0)
                last_resurrected = str(old["last_resurrected_scan"] or "")
                if int(old["absent_rounds"] or 0) > 0:
                    resurrect_count += 1
                    last_resurrected = scan_id
                    if not is_root:
                        resurrected_items.append(dict(
                            item,
                            previous_bytes=int(old["subtree_bytes"] or 0),
                            absent_rounds_before=int(old["absent_rounds"] or 0),
                            resurrect_count=resurrect_count,
                        ))
                delta = size - int(old["subtree_bytes"] or 0)
                old_size = int(old["subtree_bytes"] or 0)
                percent = (float(delta) / old_size * 100.0) if old_size else (100.0 if delta > 0 else 0.0)
                if not is_root and delta >= grow_bytes and (percent >= grow_percent or old_size == 0):
                    grew_items.append(dict(item, delta_bytes=delta, delta_percent=round(percent, 1)))
                if not is_root and delta <= -grow_bytes:
                    shrunk_items.append(dict(item, delta_bytes=delta, delta_percent=round(percent, 1)))
                old_version = str(old["app_version"] or "")
                if pkg and version and old_version and version != old_version:
                    app_updated_items.append(dict(
                        item, previous_version=old_version, version=version,
                        delta_bytes=delta,
                    ))
            decision = str(old["decision"]) if old else ""
            note = str(old["note"]) if old else ""
            if not decision and size >= min_bytes and not is_root:
                unreviewed_items.append(item)
            if pkg and parent in APP_DIR_PARENTS and packages is not None and packages.available:
                installed = packages.is_installed(pkg)
                known = packages.all_known
                if not installed:
                    reason = "包管理器显示该应用未安装" if pkg in known else "目录名不对应任何已知包名"
                    orphan_items.append(dict(item, reason=reason,
                                            confidence="high" if pkg in known else "medium"))

            self.conn.execute(
                """INSERT OR REPLACE INTO directories
                (env_id,volume,path,parent,depth,direct_bytes,subtree_bytes,subtree_files,newest_mtime,
                 first_seen_at,last_seen_at,first_seen_scan,last_seen_scan,absent_rounds,resurrect_count,
                 last_resurrected_scan,app_pkg,app_version,decision,note,reviewed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    env_id, _volume_for_path(path), path, parent, int(row["depth"] or 0),
                    int(row["direct_bytes"] or 0), size, files, newest,
                    first_seen_at, last_seen_at, first_seen_scan, scan_id,
                    absent_rounds, resurrect_count, last_resurrected,
                    pkg, version, decision, note,
                    str(old["reviewed_at"]) if old else "",
                ),
            )

        vanished_items: list[dict[str, Any]] = []
        for path, old in old_rows.items():
            if path in current_paths:
                continue
            prior_absent = int(old["absent_rounds"] or 0)
            new_absent = prior_absent + 1 if archive_complete else prior_absent
            self.conn.execute(
                "UPDATE directories SET absent_rounds=? WHERE env_id=? AND volume=? AND path=?",
                (new_absent, env_id, str(old["volume"]), path),
            )
            if archive_complete and prior_absent == 0 and path not in roots:
                vanished_items.append({
                    "path": path,
                    "bytes": int(old["subtree_bytes"] or 0),
                    "human_size": human_bytes(int(old["subtree_bytes"] or 0)),
                    "files": int(old["subtree_files"] or 0),
                    "app_pkg": str(old["app_pkg"] or ""),
                    "reason": "本轮完整扫描中未再出现，可能已删除、移走或改名",
                })

        self._update_packages(env_id, packages, scan_id, now)

        baseline_reason = "first_scan"
        if environment_changed:
            baseline_reason = "environment_changed"
        elif old_rows:
            baseline_reason = ""
        diff = {
            "schema": 1,
            "archive_path": str(self.path),
            "scan_id": scan_id,
            "env_id": env_id,
            "environment": {key: value for key, value in env.items() if key != "env_id"},
            "scan_complete": scan_complete,
            "archive_complete": archive_complete,
            "baseline": bool(baseline),
            "baseline_reason": baseline_reason,
            "environment_changed": environment_changed,
            "entries_truncated": entries_truncated,
            "summary": {
                "tracked_directories": len(filtered),
                "new": len(new_items),
                "grew": len(grew_items),
                "shrunk": len(shrunk_items),
                "vanished": len(vanished_items),
                "resurrected": len(resurrected_items),
                "app_updated": len(app_updated_items),
                "orphan_suspect": len(orphan_items),
                "unreviewed": len(unreviewed_items),
            },
            "new": sorted(new_items, key=lambda item: item["bytes"], reverse=True)[:max_diff_items],
            "grew": sorted(grew_items, key=lambda item: item["delta_bytes"], reverse=True)[:max_diff_items],
            "shrunk": sorted(shrunk_items, key=lambda item: item["delta_bytes"])[:max_diff_items],
            "vanished": sorted(vanished_items, key=lambda item: item["bytes"], reverse=True)[:max_diff_items],
            "resurrected": sorted(resurrected_items, key=lambda item: item["bytes"], reverse=True)[:max_diff_items],
            "app_updated": sorted(app_updated_items, key=lambda item: abs(item.get("delta_bytes", 0)), reverse=True)[:max_diff_items],
            "orphan_suspect": sorted(orphan_items, key=lambda item: item["bytes"], reverse=True)[:max_diff_items],
            "unreviewed": sorted(unreviewed_items, key=lambda item: item["bytes"], reverse=True)[:max_diff_items],
            "limitations": limitations,
        }
        stats = scan_meta.get("stats") or {}
        coverage = scan_meta.get("coverage") or {}
        self.conn.execute(
            """INSERT OR REPLACE INTO scans
            (scan_id,env_id,scanned_at,files,logical_bytes,unique_bytes,truncated,complete,stats_json)
            VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                scan_id, env_id, scanned_at, int(stats.get("files", 0)),
                int(stats.get("bytes", 0)), int(coverage.get("unique_bytes") or stats.get("bytes") or 0),
                1 if scan_meta.get("truncated") else 0, 1 if scan_complete else 0,
                json.dumps(stats, ensure_ascii=False),
            ),
        )
        self._set_meta("last_env_id", env_id)
        self._set_meta("last_scan_id", scan_id)
        self._set_meta("updated_at", now)
        self.conn.commit()

        diff_path = Path(scan_db).parent / "directory-diff.json"
        atomic_write_json(diff_path, diff)
        diff["diff_path"] = str(diff_path)
        return diff

    def _update_packages(
        self, env_id: str, packages: PackageIndex | None, scan_id: str, now: str
    ) -> None:
        if packages is None or not packages.available:
            return
        existing = {
            str(row["package"]): row
            for row in self.conn.execute("SELECT * FROM packages WHERE env_id=?", (env_id,)).fetchall()
        }
        known = packages.all_known
        for package in sorted(known):
            installed = packages.is_installed(package)
            version = packages.versions.get(package, "")
            old = existing.get(package)
            first_seen = str(old["first_seen_at"]) if old else now
            last_installed = str(old["last_seen_installed_at"]) if old else ""
            uninstalled_since = str(old["uninstalled_since_at"]) if old else ""
            previous_version = str(old["version"]) if old and old["version"] else ""
            if installed:
                last_installed = now
                uninstalled_since = ""
            elif old and int(old["installed"] or 0) and not uninstalled_since:
                uninstalled_since = now
            self.conn.execute(
                """INSERT OR REPLACE INTO packages
                (env_id,package,installed,system,first_seen_at,last_seen_at,last_seen_installed_at,
                 uninstalled_since_at,version,previous_version)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    env_id, package, 1 if installed else 0,
                    1 if package in packages.system else 0,
                    first_seen, now, last_installed, uninstalled_since,
                    version, previous_version,
                ),
            )

    # -- reads ------------------------------------------------------------
    def rows(self, env_id: str | None = None) -> dict[str, dict[str, Any]]:
        env_id = env_id or self.latest_env_id()
        if not env_id:
            return {}
        rows = self.conn.execute("SELECT * FROM directories WHERE env_id=?", (env_id,)).fetchall()
        return {str(row["path"]): dict(row) for row in rows}

    def stats(self) -> dict[str, Any]:
        env_id = self.latest_env_id()
        if not env_id:
            return {
                "schema": 1,
                "archive_path": str(self.path),
                "env_id": "",
                "directories": 0,
                "reviewed": 0,
                "unreviewed": 0,
                "tracked_bytes": 0,
                "tracked_human": human_bytes(0),
                "updated_at": self._meta("updated_at"),
            }
        rows = self.conn.execute(
            "SELECT COUNT(*) AS c, "
            "SUM(CASE WHEN decision != '' THEN 1 ELSE 0 END) AS reviewed FROM directories WHERE env_id=?",
            (env_id,),
        ).fetchone()
        directories = int(rows["c"] or 0)
        reviewed = int(rows["reviewed"] or 0)
        top = self.conn.execute(
            """SELECT COALESCE(SUM(d.subtree_bytes),0) AS s FROM directories d
               WHERE d.env_id=? AND NOT EXISTS (
                   SELECT 1 FROM directories p
                   WHERE p.env_id=d.env_id AND p.volume=d.volume AND p.path=d.parent
               )""",
            (env_id,),
        ).fetchone()
        tracked = int(top["s"] or 0)
        return {
            "schema": 1,
            "archive_path": str(self.path),
            "env_id": env_id,
            "directories": directories,
            "reviewed": reviewed,
            "unreviewed": max(0, directories - reviewed),
            "tracked_bytes": tracked,
            "tracked_human": human_bytes(tracked),
            "updated_at": self._meta("updated_at"),
        }

    def mark(self, path: str, decision: str, note: str = "") -> dict[str, Any]:
        decision = str(decision or "").strip().lower()
        if decision not in VALID_DECISIONS:
            raise ValidationError("archive decision 只能是 keep / watch / neutral")
        env_id = self.latest_env_id()
        if not env_id:
            raise ValidationError("目录档案为空，请先运行 scan")
        path = normalize_device_path(path)
        row = self.conn.execute(
            "SELECT * FROM directories WHERE env_id=? AND path=?", (env_id, path)
        ).fetchone()
        if row is None:
            raise ValidationError(f"目录档案中没有该路径：{path}")
        stored = "" if decision == "neutral" else decision
        reviewed_at = now_iso() if stored else ""
        self.conn.execute(
            "UPDATE directories SET decision=?,note=?,reviewed_at=? WHERE env_id=? AND volume=? AND path=?",
            (stored, str(note or ""), reviewed_at, env_id, str(row["volume"]), path),
        )
        self.conn.commit()
        return {
            "path": path,
            "decision": stored or "neutral",
            "note": str(note or ""),
            "reviewed_at": reviewed_at,
            "env_id": env_id,
            "archive_path": str(self.path),
        }

    def latest_diff(self, state_root: str | Path) -> dict[str, Any]:
        path = Path(state_root) / "scan" / "directory-diff.json"
        if not path.exists():
            raise ValidationError("尚未生成本轮目录差分，请先运行 scan")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError(f"目录差分文件无法读取：{exc}") from exc
        return data if isinstance(data, dict) else {}
