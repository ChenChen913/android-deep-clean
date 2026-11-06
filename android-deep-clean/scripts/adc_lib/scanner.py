"""Read-only device scanner.

A scan writes a SQLite snapshot plus a small JSON manifest.  Nothing on the
device is modified.  The snapshot is the single source of truth for later
analysis so that planning can be reproduced and audited.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterable

from .device import Doctor, doctor_markdown
from .models import Entry, PackageIndex
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
    apk_path TEXT NOT NULL DEFAULT ''
);
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
        self._conn: sqlite3.Connection | None = None

    # -- root resolution --------------------------------------------------
    def resolve_roots(self, explicit: str | None, include_internal: bool | str) -> list[str]:
        caps = self.doctor.get("capabilities") or {}
        root_ok = caps.get("privilege") in {"root", "su"}
        if explicit:
            roots: list[str] = []
            for item in [x.strip() for x in explicit.split(",") if x.strip()]:
                if item in {"sdcard", "/sdcard"}:
                    roots.append("/sdcard")
                elif item in {"internal-cache", "internal"}:
                    roots.extend(self._internal_cache_roots(root_ok))
                else:
                    roots.append(normalize_device_path(item))
        else:
            roots = ["/sdcard"]
            if caps.get("sdcard_read") is False:
                roots = []
            if include_internal and root_ok:
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
        if not caps.get("internal_data_read"):
            return []
        try:
            return self.transport.find_dirs("/data/data", max_depth=2, names={"cache", "code_cache"})
        except TransportError as exc:
            self.skip.append({"path": "/data/data", "reason": f"无法枚举 app cache：{exc}", "root": "/data/data"})
            return []

    # -- scanning ---------------------------------------------------------
    def scan(
        self,
        *,
        explicit_roots: str | None = None,
        max_depth: int | None = None,
        max_files: int | None = None,
        include_internal: bool | str = True,
        progress: bool = True,
    ) -> dict[str, Any]:
        scan_dir = self.state.dir("scan")
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
        self._save_packages(conn, packages)
        total_entries = 0
        total_files = 0
        total_bytes = 0
        started = time.monotonic()
        excludes = list(self.config.get("exclude_paths") or [])
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
            "android": self.doctor.get("android", ""),
            "capabilities": (self.doctor.get("capabilities") or {}),
            "limitations": (self.doctor.get("limitations") or []),
            "packages": {
                "available": packages.available,
                "installed": len(packages.installed),
                "uninstalled": len(packages.uninstalled),
                "system": len(packages.system),
                "error": packages.error,
            },
            "stats": {"entries": total_entries, "files": total_files, "bytes": total_bytes},
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
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO packages(package,installed,uninstalled,system,apk_path) VALUES (?,?,?,?,?)",
            rows,
        )
        conn.commit()


def scan_markdown(meta: dict[str, Any]) -> str:
    stats = meta.get("stats") or {}
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
        "",
        "## 未能访问的路径 / 限制",
        "",
    ]
    skipped = meta.get("skipped") or []
    if skipped:
        lines += [f"- {item.get('path')}：{item.get('reason')}" for item in skipped[:50]]
    else:
        lines.append("- 无")
    lines += ["", "> 扫描阶段全程只读；本文件不代表已经删除任何内容。", ""]
    return "\n".join(lines)


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
    rows = conn.execute("SELECT package,installed,uninstalled,system,apk_path FROM packages").fetchall()
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
    return index
