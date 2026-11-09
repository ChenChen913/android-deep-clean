"""Read-only device scanner.

A scan writes a SQLite snapshot plus a small JSON manifest.  Nothing on the
device is modified.  The snapshot is the single source of truth for later
analysis so that planning can be reproduced and audited.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from .app_storage import AppStorageCollector
from .device import Doctor, doctor_markdown
from .media_store import MediaStoreCollector
from .models import Entry, PackageIndex
from .platform import (
    HARMONY_NEXT,
    HARMONY_PHOTO_ROOT,
    HARMONY_PUBLIC_ROOT,
    HARMONY_QUARANTINE_ROOT,
    profile_for,
)
from .rules import package_from_path
from .state import State
from .transport import Transport, TransportError
from .util import (
    ValidationError,
    human_bytes,
    is_under,
    normalize_device_path,
    now_iso,
    read_json,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    parent TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    mtime INTEGER,
    atime INTEGER,
    ctime INTEGER,
    inode INTEGER,
    dev INTEGER,
    nlink INTEGER,
    mode INTEGER,
    uid INTEGER,
    depth INTEGER NOT NULL DEFAULT 0,
    root TEXT NOT NULL DEFAULT '',
    ext TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_entries_parent ON entries(parent);
CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind);
CREATE INDEX IF NOT EXISTS idx_entries_size ON entries(size);
CREATE INDEX IF NOT EXISTS idx_entries_mtime ON entries(mtime);
CREATE INDEX IF NOT EXISTS idx_entries_name ON entries(name);
CREATE TABLE IF NOT EXISTS skipped (
    path TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    root TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS packages (
    package TEXT PRIMARY KEY,
    installed INTEGER NOT NULL DEFAULT 0,
    uninstalled INTEGER NOT NULL DEFAULT 0,
    system INTEGER NOT NULL DEFAULT 0,
    apk_path TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS dir_stats (
    path TEXT PRIMARY KEY,
    parent TEXT NOT NULL DEFAULT '',
    depth INTEGER NOT NULL DEFAULT 0,
    direct_bytes INTEGER NOT NULL DEFAULT 0,
    direct_files INTEGER NOT NULL DEFAULT 0,
    subtree_bytes INTEGER NOT NULL DEFAULT 0,
    subtree_files INTEGER NOT NULL DEFAULT 0,
    newest_mtime INTEGER,
    oldest_mtime INTEGER
);
CREATE INDEX IF NOT EXISTS idx_dir_stats_parent ON dir_stats(parent);
CREATE INDEX IF NOT EXISTS idx_dir_stats_size ON dir_stats(subtree_bytes);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def state_db_path(state: State) -> Path:
    return state.dir("scan") / "snapshot.db"


def snapshot_meta_path(state: State) -> Path:
    return state.dir("scan") / "snapshot-meta.json"


class Scanner:
    def __init__(self, transport: Transport, config: dict[str, Any], state: State, doctor: dict[str, Any] | None = None) -> None:
        self.transport = transport
        self.config = config
        self.state = state
        self.doctor = doctor or {}
        self.skip: list[dict[str, str]] = []
        self.truncated = False
        self.roots: list[str] = []
        self.packages: PackageIndex | None = None
        self.root_status: list[dict[str, Any]] = []
        self._conn: sqlite3.Connection | None = None

    # -- root resolution --------------------------------------------------
    def resolve_roots(self, explicit: str | None, include_internal: bool | str) -> list[str]:
        caps = self.doctor.get("capabilities") or {}
        platform = str(caps.get("platform") or "android")
        profile = profile_for(platform)
        root_ok = caps.get("privilege") in {"root", "su"}
        if explicit:
            roots: list[str] = []
            for item in [x.strip() for x in explicit.split(",") if x.strip()]:
                if item in {"sdcard", "/sdcard"} and platform != HARMONY_NEXT:
                    roots.append("/sdcard")
                elif item in {"internal-cache", "internal"} and platform != HARMONY_NEXT:
                    roots.extend(self._internal_cache_roots(root_ok))
                elif item in {"public", "harmony-public"} and platform == HARMONY_NEXT:
                    roots.extend(profile.default_roots)
                else:
                    roots.append(normalize_device_path(item))
        else:
            if platform == HARMONY_NEXT:
                roots = [HARMONY_PUBLIC_ROOT] + list(caps.get("harmony_photo_roots") or [HARMONY_PHOTO_ROOT])
            else:
                roots = list(profile.default_roots)
            if platform != HARMONY_NEXT and caps.get("sdcard_read") is False:
                roots = []
            if platform != HARMONY_NEXT and include_internal and root_ok:
                roots.extend(self._internal_cache_roots(True))
        # remove nested roots (keep the shallow one because it already covers children)
        cleaned: list[str] = []
        for root in sorted(set(roots), key=lambda p: (p.count("/"), p)):
            if any(is_under(root, existing) for existing in cleaned):
                continue
            cleaned.append(root)
        return cleaned

    def _internal_cache_roots(self, root_ok: bool) -> list[str]:
        if not root_ok:
            return []
        caps = self.doctor.get("capabilities") or {}
        if caps.get("platform") == HARMONY_NEXT or not caps.get("internal_data_read"):
            return []
        try:
            return self.transport.find_dirs("/data/data", max_depth=2, names={"cache", "code_cache"})
        except TransportError as exc:
            self.skip.append({"path": "/data/data", "reason": f"无法枚举 app cache：{exc}", "root": "/data/data"})
            return []

    # -- scanning ---------------------------------------------------------
    def _archive_previous_scan(self) -> str:
        scan_dir = self.state.dir("scan")
        db_path = scan_dir / "snapshot.db"
        if not db_path.exists():
            return ""
        history = self.state.root / "history"
        history.mkdir(parents=True, exist_ok=True)
        target = history / ("scan-" + time.strftime("%Y%m%d-%H%M%S"))
        suffix = 2
        while target.exists():
            target = history / ("scan-" + time.strftime("%Y%m%d-%H%M%S") + f"-{suffix}")
            suffix += 1
        target.mkdir(parents=True, exist_ok=True)
        for name in ("snapshot.db", "snapshot-meta.json", "scan-summary.md"):
            source = scan_dir / name
            if source.exists():
                shutil.copy2(source, target / name)
        # Keep a bounded history so repeated scans cannot fill the host disk.
        archives = sorted(history.glob("scan-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in archives[10:]:
            shutil.rmtree(old, ignore_errors=True)
        return str(target)

    def scan(
        self,
        *,
        explicit_roots: str | None = None,
        max_depth: int | None = None,
        max_files: int | None = None,
        include_internal: bool | str = True,
        progress: bool = True,
        collect_media_store: bool = True,
        collect_app_storage: bool = True,
    ) -> dict[str, Any]:
        scan_dir = self.state.dir("scan")
        previous_scan = self._archive_previous_scan()
        db_path = scan_dir / "snapshot.db"
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(db_path) + suffix)
            if path.exists():
                path.unlink()
        conn = sqlite3.connect(str(db_path))
        conn.executescript(SCHEMA)
        self._conn = conn
        max_depth = max_depth if max_depth is not None else int(self.config["scan"]["max_depth"])
        max_files = max_files if max_files is not None else int(self.config["scan"]["max_files"])
        deadline = time.monotonic() + int(self.config["scan"]["deadline_seconds"])
        self.roots = self.resolve_roots(explicit_roots, include_internal)
        packages = self.transport.list_packages()
        self.packages = packages
        self._save_packages(conn, packages)
        total_entries = 0
        total_files = 0
        total_bytes = 0
        started = time.monotonic()
        excludes = list(self.config.get("exclude_paths") or [])
        if str((self.doctor.get("capabilities") or {}).get("platform") or "") == HARMONY_NEXT:
            excludes.append(HARMONY_QUARANTINE_ROOT)
        seen_roots: list[str] = []

        for root in self.roots:
            if time.monotonic() > deadline:
                self.truncated = True
                self.skip.append({"path": root, "reason": "总体扫描超时", "root": root})
                break
            access = self.transport.access(root)
            if not access.readable:
                self.skip.append({"path": root, "reason": access.error or "不可读", "root": root})
                continue
            seen_roots.append(root)
            remaining = max(0, max_files - total_entries) if max_files else 0
            batch: list[Entry] = []
            try:
                for entry in self.transport.iter_tree(
                    root,
                    max_depth=max_depth,
                    max_files=remaining,
                    excludes=excludes,
                    skip_sink=self.skip,
                    deadline=deadline,
                ):
                    if entry.kind in {"file", "dir", "symlink", "other"}:
                        batch.append(entry)
                    if len(batch) >= 2000:
                        inserted = self._insert_batch(conn, batch)
                        total_entries += inserted
                        total_files += sum(1 for e in batch if e.kind == "file")
                        total_bytes += sum(e.size for e in batch if e.kind == "file")
                        batch = []
                        if progress and total_entries % 10000 < 2000:
                            print(f"[scan] {total_entries} 条目 / {human_bytes(total_bytes)} ...", file=sys.stderr, flush=True)
                    if max_files and total_entries >= max_files:
                        self.truncated = True
                        break
                if batch:
                    inserted = self._insert_batch(conn, batch)
                    total_entries += inserted
                    total_files += sum(1 for e in batch if e.kind == "file")
                    total_bytes += sum(e.size for e in batch if e.kind == "file")
            except TransportError as exc:
                self.skip.append({"path": root, "reason": str(exc), "root": root})
            except KeyboardInterrupt:
                raise
            if max_files and total_entries >= max_files:
                self.truncated = True
                self.skip.append({"path": root, "reason": f"达到 max_files={max_files} 限制", "root": root})
                break

        # authoritative totals from DB (dedupe of overlapping roots)
        row = conn.execute("SELECT COUNT(*), COALESCE(SUM(CASE WHEN kind='file' THEN 1 ELSE 0 END),0), COALESCE(SUM(CASE WHEN kind='file' THEN size ELSE 0 END),0) FROM entries").fetchone()
        total_entries, total_files, total_bytes = int(row[0]), int(row[1]), int(row[2])
        if any("max_files" in str(item.get("reason", "")) or "截断" in str(item.get("reason", "")) or "超时" in str(item.get("reason", "")) for item in self.skip):
            self.truncated = True
        for item in self.skip:
            try:
                conn.execute("INSERT OR IGNORE INTO skipped(path, reason, root) VALUES (?,?,?)", (item.get("path", ""), item.get("reason", ""), item.get("root", "")))
            except sqlite3.Error:
                pass
        dir_stats = _write_dir_stats(conn)
        unique_bytes = _unique_file_bytes(conn)
        depth_limited = False
        if max_depth and max_depth > 0:
            depth_limited = conn.execute(
                "SELECT 1 FROM entries WHERE kind='dir' AND depth >= ? LIMIT 1", (max_depth,)
            ).fetchone() is not None
        storage = self.doctor.get("storage") or []
        coverage = _build_coverage(
            stats={"entries": total_entries, "files": total_files, "bytes": total_bytes},
            roots=self.roots,
            scanned_roots=seen_roots,
            skipped=self.skip,
            truncated=self.truncated,
            depth_limited=depth_limited,
            max_depth=max_depth,
            max_files=max_files,
            dir_stats=dir_stats,
            unique_bytes=unique_bytes,
            storage=storage,
        )
        try:
            media_manifest = MediaStoreCollector(
                self.transport, self.config, self.state, self.doctor
            ).collect(enabled=collect_media_store)
        except Exception as exc:  # noqa: BLE001 - auxiliary source must never break scan
            aux_dir = self.state.dir("scan") / "aux"
            aux_dir.mkdir(parents=True, exist_ok=True)
            items_path = aux_dir / "media-index.jsonl"
            items_path.write_text("", encoding="utf-8")
            media_manifest = {
                "schema": 1,
                "status": "unavailable",
                "available": False,
                "reason": f"媒体索引采集异常：{exc}",
                "platform": str((self.doctor.get("capabilities") or {}).get("platform") or "android"),
                "collections": [],
                "items": 0,
                "bytes": 0,
                "path_known": 0,
                "path_unknown": 0,
                "trashed": 0,
                "pending": 0,
                "truncated": False,
                "limitations": [f"媒体索引采集异常：{exc}"],
                "items_path": str(items_path),
                "manifest_path": str(aux_dir / "sources.json"),
            }
            from .util import atomic_write_json

            atomic_write_json(aux_dir / "sources.json", media_manifest)
        try:
            app_manifest = AppStorageCollector(
                self.transport, self.config, self.state, self.doctor, self.packages
            ).collect(enabled=collect_app_storage)
        except Exception as exc:  # noqa: BLE001 - auxiliary source must never break scan
            aux_dir = self.state.dir("scan") / "aux"
            aux_dir.mkdir(parents=True, exist_ok=True)
            app_manifest = {
                "schema": 1,
                "status": "unavailable",
                "available": False,
                "reason": f"应用级存储采集异常：{exc}",
                "platform": str((self.doctor.get("capabilities") or {}).get("platform") or "android"),
                "packages": [],
                "items": 0,
                "bytes": 0,
                "limitations": [f"应用级存储采集异常：{exc}"],
                "manifest_path": str(aux_dir / "app-storage.json"),
            }
            from .util import atomic_write_json

            atomic_write_json(aux_dir / "app-storage.json", app_manifest)
        meta = {
            "schema": 1,
            "scanned_at": now_iso(),
            "started_at": started,
            "duration_seconds": round(time.monotonic() - started, 1),
            "roots_requested": self.roots,
            "roots_scanned": seen_roots,
            "max_depth": max_depth,
            "max_files": max_files,
            "truncated": self.truncated,
            "transport": self.transport.name,
            "privilege": self.transport.privilege,
            "device": (self.doctor.get("device") or {}),
            "device_identity": (self.doctor.get("device_identity") or {}),
            "previous_scan": previous_scan,
            "android": self.doctor.get("android", ""),
            "platform": (
                self.doctor.get("platform")
                or (self.doctor.get("capabilities") or {}).get("platform")
                or "android"
            ),
            "capabilities": (self.doctor.get("capabilities") or {}),
            "limitations": (self.doctor.get("limitations") or []),
            "storage": storage,
            "packages": {
                "available": packages.available,
                "installed": len(packages.installed),
                "uninstalled": len(packages.uninstalled),
                "system": len(packages.system),
                "version_available": packages.version_available,
                "error": packages.error,
            },
            "stats": {"entries": total_entries, "files": total_files, "bytes": total_bytes},
            "coverage": coverage,
            "aux": {"schema": 1, "media_store": media_manifest, "app_storage": app_manifest},
            "skipped": self.skip[:2000],
            "doctor_path": str(self.state.file("doctor", "doctor.json")),
        }
        conn.commit()
        conn.close()
        self._conn = None
        from .util import atomic_write_json

        atomic_write_json(snapshot_meta_path(self.state), meta)
        self.state.set_file("scan", db_path)
        self.state.set_file("scan_meta", snapshot_meta_path(self.state))
        summary = scan_markdown(meta)
        (scan_dir / "scan-summary.md").write_text(summary, encoding="utf-8")
        return meta

    def _insert_batch(self, conn: sqlite3.Connection, batch: list[Entry]) -> int:
        before = conn.total_changes
        conn.executemany(
            """INSERT OR IGNORE INTO entries
            (path,parent,name,kind,size,mtime,atime,ctime,inode,dev,nlink,mode,uid,depth,root,ext)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (e.path, e.parent, e.name, e.kind, e.size, e.mtime, e.atime, e.ctime,
                 e.inode, e.dev, e.nlink, e.mode, e.uid, e.depth, e.root, e.ext)
                for e in batch
            ],
        )
        conn.commit()
        return conn.total_changes - before

    @staticmethod
    def _save_packages(conn: sqlite3.Connection, packages: PackageIndex) -> None:
        rows = []
        for package in packages.all_known:
            rows.append((
                package,
                1 if package in packages.installed else 0,
                1 if package in packages.uninstalled else 0,
                1 if package in packages.system else 0,
                packages.apk_paths.get(package, ""),
                packages.versions.get(package, ""),
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO packages(package,installed,uninstalled,system,apk_path,version) VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()


def _write_dir_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Aggregate direct and full-subtree sizes for every scanned directory.

    Kept in the snapshot so the archive and reports do not have to re-sum
    hundreds of thousands of rows on every run.
    """
    dirs: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT path,parent,depth,mtime FROM entries WHERE kind='dir'"):
        dirs[row[0]] = {
            "parent": row[1] or "",
            "depth": int(row[2] or 0),
            "direct_bytes": 0,
            "direct_files": 0,
            "subtree_bytes": 0,
            "subtree_files": 0,
            "newest_mtime": None,
            "oldest_mtime": None,
            "own_mtime": row[3],
        }
    if not dirs:
        return {"count": 0, "bytes": 0}

    for row in conn.execute("SELECT parent,size,mtime FROM entries WHERE kind='file'"):
        item = dirs.get(row[0])
        if item is None:
            continue
        size = int(row[1] or 0)
        item["direct_bytes"] += size
        item["direct_files"] += 1
        mtime = row[2]
        if mtime is not None:
            item["newest_mtime"] = max(item["newest_mtime"] or mtime, mtime)
            item["oldest_mtime"] = min(item["oldest_mtime"] or mtime, mtime)

    for item in dirs.values():
        if item["newest_mtime"] is None and item["own_mtime"] is not None:
            item["newest_mtime"] = item["own_mtime"]
            item["oldest_mtime"] = item["own_mtime"]
        item["subtree_bytes"] = item["direct_bytes"]
        item["subtree_files"] = item["direct_files"]

    for path in sorted(dirs, key=lambda p: dirs[p]["depth"], reverse=True):
        item = dirs[path]
        parent = item["parent"]
        if parent in dirs and parent != path:
            guardian = dirs[parent]
            guardian["subtree_bytes"] += item["subtree_bytes"]
            guardian["subtree_files"] += item["subtree_files"]
            if item["newest_mtime"] is not None:
                guardian["newest_mtime"] = max(guardian["newest_mtime"] or item["newest_mtime"], item["newest_mtime"])
            if item["oldest_mtime"] is not None:
                guardian["oldest_mtime"] = min(guardian["oldest_mtime"] or item["oldest_mtime"], item["oldest_mtime"])

    rows = [
        (
            path,
            item["parent"],
            item["depth"],
            item["direct_bytes"],
            item["direct_files"],
            item["subtree_bytes"],
            item["subtree_files"],
            item["newest_mtime"],
            item["oldest_mtime"],
        )
        for path, item in dirs.items()
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO dir_stats
        (path,parent,depth,direct_bytes,direct_files,subtree_bytes,subtree_files,newest_mtime,oldest_mtime)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    conn.commit()
    return {
        "count": len(rows),
        "bytes": sum(item["subtree_bytes"] for item in dirs.values() if item["parent"] not in dirs),
        "largest": sorted(
            ({"path": path, "bytes": item["subtree_bytes"], "files": item["subtree_files"]}
             for path, item in dirs.items()),
            key=lambda item: item["bytes"],
            reverse=True,
        )[:10],
    }


def _unique_file_bytes(conn: sqlite3.Connection) -> int:
    """Logical bytes with hard-linked files counted once by (dev, inode)."""
    seen: set[tuple[int, int]] = set()
    total = 0
    for row in conn.execute("SELECT size,nlink,dev,inode FROM entries WHERE kind='file'"):
        size = int(row[0] or 0)
        nlink = row[1]
        dev, inode = row[2], row[3]
        if nlink and int(nlink) > 1 and dev is not None and inode is not None:
            key = (int(dev), int(inode))
            if key in seen:
                continue
            seen.add(key)
        total += size
    return total


def _root_status(roots: list[str], scanned_roots: list[str], skipped: list[dict[str, str]]) -> list[dict[str, Any]]:
    by_root: dict[str, str] = {}
    for item in skipped:
        path = str(item.get("path") or "")
        by_root.setdefault(path, str(item.get("reason") or ""))
    status: list[dict[str, Any]] = []
    for root in roots:
        if root in scanned_roots:
            reason = by_root.get(root, "")
            status.append({
                "path": root,
                "state": "partial" if reason else "scanned",
                "reason": reason,
            })
        else:
            status.append({
                "path": root,
                "state": "skipped",
                "reason": by_root.get(root, "未访问"),
            })
    return status


def _storage_used(storage: list[dict[str, Any]]) -> tuple[int, str]:
    preferred = [item for item in storage if str(item.get("path")) == "/data"]
    candidates = preferred or [item for item in storage if str(item.get("path")) == "/sdcard"] or storage
    for item in candidates:
        try:
            used = int(item.get("used_bytes") or 0)
        except (TypeError, ValueError):
            used = 0
        if used:
            return used, str(item.get("path") or "")
    return 0, ""


def _build_coverage(
    *,
    stats: dict[str, int],
    roots: list[str],
    scanned_roots: list[str],
    skipped: list[dict[str, str]],
    truncated: bool,
    depth_limited: bool,
    max_depth: int,
    max_files: int,
    dir_stats: dict[str, Any],
    unique_bytes: int,
    storage: list[dict[str, Any]],
) -> dict[str, Any]:
    reasons: list[str] = []
    root_status = _root_status(roots, scanned_roots, skipped)
    if truncated:
        reasons.append("扫描被截断，结果不完整")
    if depth_limited:
        reasons.append(f"存在达到 max_depth={max_depth} 的目录，更深层内容未扫描")
    missing_roots = [item["path"] for item in root_status if item["state"] != "scanned"]
    if missing_roots:
        reasons.append("部分扫描根目录未完整扫描：" + ", ".join(missing_roots[:10]))
    if any(item["state"] == "partial" for item in root_status):
        reasons.append("部分扫描根目录在扫描过程中出现错误或被截断")
    skipped_paths = sorted({str(item.get("path") or "") for item in skipped if item.get("path")})
    skipped_subpaths = [path for path in skipped_paths if path not in roots]
    if skipped_subpaths:
        reasons.append(f"扫描过程中跳过 {len(skipped_subpaths)} 个路径（权限不足/无法读取），结果不完整")

    used_bytes, storage_path = _storage_used(storage)
    suspect = int(stats.get("files", 0)) == 0 and used_bytes > 0
    if suspect:
        reasons.append("未扫描到任何文件，但设备已用空间大于 0，结果可疑")
    status = "suspect" if suspect else ("partial" if reasons else "good")
    logical = int(stats.get("bytes", 0))
    return {
        "schema": 1,
        "status": status,
        "reasons": reasons,
        "roots_requested": roots,
        "roots_scanned": scanned_roots,
        "root_status": root_status,
        "skipped_paths": skipped_paths,
        "truncated": bool(truncated),
        "depth_limited": bool(depth_limited),
        "max_depth": max_depth,
        "max_files": max_files,
        "files": int(stats.get("files", 0)),
        "logical_bytes": logical,
        "unique_bytes": int(unique_bytes),
        "hardlink_saved_bytes": max(0, logical - int(unique_bytes)),
        "dir_count": int(dir_stats.get("count", 0)),
        "dir_root_bytes": int(dir_stats.get("bytes", 0)),
        "largest_dirs": dir_stats.get("largest") or [],
        "storage_path": storage_path,
        "used_bytes": used_bytes,
        "scan_ratio": round(float(unique_bytes) / used_bytes, 4) if used_bytes else None,
    }


def scan_markdown(meta: dict[str, Any], directory_diff: dict[str, Any] | None = None) -> str:
    stats = meta.get("stats") or {}
    coverage = meta.get("coverage") or {}
    lines = [
        "# 存储扫描摘要",
        "",
        f"- 扫描时间：{meta.get('scanned_at')}",
        f"- 设备：{(meta.get('device') or {}).get('manufacturer', '')} {(meta.get('device') or {}).get('model', '')}".strip(),
        f"- 系统：{meta.get('android', '')}",
        f"- 权限：{meta.get('privilege')}",
        f"- 扫描根目录：{', '.join(meta.get('roots_scanned') or []) or '无'}",
        f"- 条目：{stats.get('entries', 0)}（文件 {stats.get('files', 0)}，总逻辑大小 {human_bytes(stats.get('bytes', 0))}）",
        f"- 截断：{'是，结果不完整' if meta.get('truncated') else '否'}",
        f"- 覆盖率状态：{_coverage_label(str(coverage.get('status') or 'unknown'))}",
        f"- 去重后大小：{human_bytes(coverage.get('unique_bytes', stats.get('bytes', 0)))}"
        + (f"（硬链接少计 {human_bytes(coverage.get('hardlink_saved_bytes', 0))}）"
           if coverage.get("hardlink_saved_bytes") else ""),
        f"- 目录统计：{coverage.get('dir_count', 0)} 个目录，根目录合计 {human_bytes(coverage.get('dir_root_bytes', 0))}",
        "",
        "## 扫描覆盖",
        "",
        "| 根目录 | 状态 | 说明 |",
        "|---|---|---|",
    ]
    for item in coverage.get("root_status") or []:
        lines.append(
            f"| {item.get('path')} | {_coverage_label(str(item.get('state') or 'unknown'))} | "
            f"{item.get('reason') or '-'} |"
        )
    if not (coverage.get("root_status") or []):
        lines.append("| - | - | 无 |")
    if coverage.get("reasons"):
        lines += ["", "覆盖限制：", ""]
        lines += [f"- {item}" for item in coverage["reasons"]]
    media = (meta.get("aux") or {}).get("media_store") or {}
    if media:
        lines += ["", "## 媒体索引（辅助数据源）", ""]
        lines.append(f"- 状态：{_media_status_label(str(media.get('status') or 'unknown'))}")
        if media.get("reason"):
            lines.append(f"- 原因：{media.get('reason')}")
        lines.append(
            f"- 条目：{media.get('items', 0)}，声明大小 {human_bytes(media.get('bytes', 0))}，"
            f"路径可解析 {media.get('path_known', 0)}"
        )
        if media.get("trashed") or media.get("pending"):
            lines.append(f"- 回收站标记 {media.get('trashed', 0)}，pending {media.get('pending', 0)}")
        lines.append("- 说明：媒体索引只用于交叉验证，不参与文件扫描统计，也不授权删除。")
        for item in media.get("limitations") or []:
            lines.append(f"- 限制：{item}")
    app = (meta.get("aux") or {}).get("app_storage") or {}
    if app:
        lines += ["", "## 应用级存储（部分口径）", ""]
        lines.append(f"- 状态：{_media_status_label(str(app.get('status') or 'unknown'))}")
        if app.get("reason"):
            lines.append(f"- 原因：{app.get('reason')}")
        lines.append(
            f"- 基础 APK：{app.get('items', 0)} 个应用 / {human_bytes(app.get('bytes', 0))}；"
            "split APK 未计入"
        )
        lines.append("- 说明：内部 data/cache 不可读；外部 data/media/obb 由文件扫描统计。")
        for item in app.get("limitations") or []:
            lines.append(f"- 限制：{item}")
    lines += [
        "",
        "## 未能访问的路径 / 限制",
        "",
    ]
    skipped = meta.get("skipped") or []
    if skipped:
        lines += [f"- {item.get('path')}：{item.get('reason')}" for item in skipped[:50]]
    else:
        lines.append("- 无")
    if directory_diff:
        lines += ["", "## 跨轮目录档案", ""]
        if directory_diff.get("baseline"):
            lines.append("- 本轮已建立目录基线；后续扫描开始显示新增/增长/复活/消失。")
        else:
            summary = directory_diff.get("summary") or {}
            lines.append(
                f"- 新增 {summary.get('new', 0)}；增长 {summary.get('grew', 0)}；"
                f"缩小 {summary.get('shrunk', 0)}；本轮未再出现 {summary.get('vanished', 0)}；"
                f"删后复活 {summary.get('resurrected', 0)}；疑似残留 {summary.get('orphan_suspect', 0)}"
            )
        if directory_diff.get("environment_changed"):
            lines.append("- 设备环境发生变化（系统/用户/指纹），本轮按新基线处理，未标记大量新增或消失。")
        if directory_diff.get("scan_complete") is False:
            lines.append("- 本轮扫描不完整，消失类变化只记录为待确认，不作为删除依据。")
    lines += ["", "> 扫描阶段全程只读；本文件不代表已经删除任何内容。", ""]
    return "\n".join(lines)


def _coverage_label(value: str) -> str:
    return {
        "good": "完整",
        "partial": "部分覆盖",
        "suspect": "结果可疑",
        "scanned": "已扫描",
        "skipped": "未扫描",
        "unknown": "未知",
    }.get(value, value)


def _media_status_label(value: str) -> str:
    return {
        "collected": "已采集",
        "partial": "部分可用",
        "unavailable": "不可用",
        "unsupported": "不支持",
        "disabled": "已关闭",
        "not_collected": "未采集",
        "unknown": "未知",
    }.get(value, value)


def load_snapshot(state: State) -> tuple[sqlite3.Connection, dict[str, Any]]:
    db = state.require("scan", "扫描结果")
    meta_path = snapshot_meta_path(state)
    meta = read_json(meta_path, default=None)
    if not isinstance(meta, dict):
        raise ValidationError("扫描元数据缺失，请重新运行 scan")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn, meta


def load_packages(conn: sqlite3.Connection) -> PackageIndex:
    index = PackageIndex(available=False)
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(packages)").fetchall()}
    version_sql = ",version" if "version" in columns else ""
    rows = conn.execute(
        "SELECT package,installed,uninstalled,system,apk_path" + version_sql + " FROM packages"
    ).fetchall()
    if rows:
        index.available = True
    for row in rows:
        package = row["package"]
        if row["installed"]:
            index.installed.add(package)
        if row["uninstalled"]:
            index.uninstalled.add(package)
        if row["system"]:
            index.system.add(package)
        if row["apk_path"]:
            index.apk_paths[package] = row["apk_path"]
        if "version" in columns and row["version"]:
            index.versions[package] = str(row["version"])
            index.version_available = True
    return index
