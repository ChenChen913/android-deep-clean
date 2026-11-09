"""Host-side app-level storage aggregation (partial, evidence only).

Known:
- base APK size collected by ``app_storage.py``;
- external data/media/obb sizes from the scanned filesystem snapshot.

Unknown:
- app-internal data/cache (shell lacks StorageStatsManager permissions);
- split APKs.

The module never creates candidates or changes risk.  It only reports,
cross-checks and adds evidence to existing app-related candidates.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from .models import CandidateFile, PackageIndex
from .rules import app_info, package_from_path
from .state import State
from .util import human_bytes, read_json

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_storage (
    package TEXT PRIMARY KEY,
    label TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    installed INTEGER NOT NULL DEFAULT 0,
    system INTEGER NOT NULL DEFAULT 0,
    version TEXT NOT NULL DEFAULT '',
    apk_path TEXT NOT NULL DEFAULT '',
    apk_bytes INTEGER,
    external_data_bytes INTEGER NOT NULL DEFAULT 0,
    external_media_bytes INTEGER NOT NULL DEFAULT 0,
    external_obb_bytes INTEGER NOT NULL DEFAULT 0,
    external_cache_bytes INTEGER NOT NULL DEFAULT 0,
    total_known_bytes INTEGER NOT NULL DEFAULT 0,
    residue INTEGER NOT NULL DEFAULT 0,
    coverage TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_app_storage_total ON app_storage(total_known_bytes);
CREATE TABLE IF NOT EXISTS source_status (
    name TEXT PRIMARY KEY,
    available INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT '',
    items INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT ''
);
"""

APP_EVIDENCE_CATEGORIES = {"app_cache", "browser_cache", "chat_cache", "residue"}
EXTERNAL_PARENTS = ("/sdcard/Android/data", "/sdcard/Android/media", "/sdcard/Android/obb")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class AppStorageIndex:
    def __init__(
        self,
        state: State,
        scan_conn: sqlite3.Connection,
        scan_meta: dict[str, Any],
        config: dict[str, Any],
        packages: PackageIndex,
    ) -> None:
        self.state = state
        self.scan_conn = scan_conn
        self.scan_meta = scan_meta
        self.config = config
        self.packages = packages
        self.aux_path = state.dir("scan") / "aux" / "app-storage.json"
        self.manifest: dict[str, Any] = {}
        self.manifest_status = "not_collected"
        self.manifest_reason = "本轮扫描未采集应用 APK 大小"
        self.apk_available = False
        self.status = "not_collected"
        self.reason = self.manifest_reason
        self.available = False
        self.limitations: list[str] = []
        self.rows: list[dict[str, Any]] = []
        self.by_package: dict[str, dict[str, Any]] = {}
        self._summary: dict[str, Any] | None = None
        self._dir_stats_ok = True
        self.top_apps = max(1, _int((config.get("app_storage") or {}).get("top_apps"), 15))

    # ---------------------------------------------------------------- loading
    def load(self) -> "AppStorageIndex":
        manifest = read_json(self.aux_path, default=None) if self.aux_path.exists() else None
        if not isinstance(manifest, dict):
            fallback = (self.scan_meta.get("aux") or {}).get("app_storage")
            manifest = fallback if isinstance(fallback, dict) else {}
        self.manifest = manifest
        if manifest:
            self.manifest_status = str(manifest.get("status") or "unavailable")
            self.manifest_reason = str(manifest.get("reason") or "")
            self.apk_available = self.manifest_status in {"collected", "partial"}
            self.limitations = [str(item) for item in (manifest.get("limitations") or [])]
        return self

    # ---------------------------------------------------------------- ingest
    def ingest(self, db: sqlite3.Connection) -> None:
        db.executescript(APP_SCHEMA)
        try:
            db.execute("SELECT subtree_bytes FROM dir_stats LIMIT 1").fetchone()
        except sqlite3.Error:
            self._dir_stats_ok = False
            self.limitations.append("旧快照没有 dir_stats，无法读取外部应用目录大小。")
        apk_records = {
            str(item.get("package")): item
            for item in (self.manifest.get("packages") or [])
            if item.get("package")
        }
        external_packages = self._external_packages()
        names = sorted(set(self.packages.installed) | set(apk_records) | set(external_packages))
        for package in names:
            apk = apk_records.get(package) or {}
            data = self._dir_bytes(f"/sdcard/Android/data/{package}")
            cache = (
                self._dir_bytes(f"/sdcard/Android/data/{package}/cache")
                + self._dir_bytes(f"/sdcard/Android/data/{package}/code_cache")
            )
            media = self._dir_bytes(f"/sdcard/Android/media/{package}")
            obb = self._dir_bytes(f"/sdcard/Android/obb/{package}")
            external_total = data + media + obb
            installed = package in self.packages.installed
            system = package in self.packages.system
            apk_bytes = apk.get("apk_bytes")
            apk_bytes = int(apk_bytes) if isinstance(apk_bytes, int) else None
            total = (apk_bytes or 0) + external_total
            if not installed and external_total == 0 and apk_bytes is None:
                continue
            info = app_info(package)
            self.rows.append({
                "package": package,
                "label": info.get("label", package),
                "kind": info.get("kind", "app"),
                "installed": installed,
                "system": system,
                "version": self.packages.versions.get(package, ""),
                "apk_path": str(apk.get("apk_path") or self.packages.apk_paths.get(package, "")),
                "apk_bytes": apk_bytes,
                "external_data_bytes": data,
                "external_media_bytes": media,
                "external_obb_bytes": obb,
                "external_cache_bytes": cache,
                "external_total_bytes": external_total,
                "total_known_bytes": total,
                "residue": (not installed) and external_total > 0,
                "coverage": "apk_base+external" if apk_bytes is not None else "external_only",
            })
        if self.rows:
            db.executemany(
                """INSERT OR REPLACE INTO app_storage
                (package,label,kind,installed,system,version,apk_path,apk_bytes,
                 external_data_bytes,external_media_bytes,external_obb_bytes,external_cache_bytes,
                 total_known_bytes,residue,coverage)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        row["package"], row["label"], row["kind"], 1 if row["installed"] else 0,
                        1 if row["system"] else 0, row["version"], row["apk_path"], row["apk_bytes"],
                        row["external_data_bytes"], row["external_media_bytes"], row["external_obb_bytes"],
                        row["external_cache_bytes"], row["total_known_bytes"], 1 if row["residue"] else 0,
                        row["coverage"],
                    )
                    for row in self.rows
                ],
            )
        self.by_package = {row["package"]: row for row in self.rows}
        self._summary = self._build_summary()
        self._write_source_status(db)
        db.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES ('app_storage',?)",
            (json.dumps(self._summary, ensure_ascii=False),),
        )

    def _external_packages(self) -> set[str]:
        if not self._dir_stats_ok:
            return set()
        found: set[str] = set()
        placeholders = ",".join("?" for _ in EXTERNAL_PARENTS)
        try:
            for row in self.scan_conn.execute(
                f"SELECT path FROM dir_stats WHERE parent IN ({placeholders})", EXTERNAL_PARENTS
            ):
                path = str(row[0] or "")
                name = path.rsplit("/", 1)[-1]
                if name:
                    found.add(name)
        except sqlite3.Error:
            self._dir_stats_ok = False
            return set()
        return found

    def _dir_bytes(self, path: str) -> int:
        if not self._dir_stats_ok:
            return 0
        try:
            row = self.scan_conn.execute(
                "SELECT subtree_bytes FROM dir_stats WHERE path=?", (path,)
            ).fetchone()
        except sqlite3.Error:
            return 0
        return _int(row[0], 0) if row else 0

    def _write_source_status(self, db: sqlite3.Connection) -> None:
        try:
            db.execute(
                "INSERT OR REPLACE INTO source_status(name,available,status,items,bytes,reason) VALUES (?,?,?,?,?,?)",
                (
                    "app_storage", 1 if self.available else 0, self.status,
                    sum(1 for row in self.rows if row["total_known_bytes"] > 0),
                    sum(row["total_known_bytes"] for row in self.rows), self.reason,
                ),
            )
        except sqlite3.Error:
            pass

    # ---------------------------------------------------------------- summary
    def summary(self) -> dict[str, Any]:
        if self._summary is None:
            self._summary = self._build_summary()
        return self._summary

    def _build_summary(self) -> dict[str, Any]:
        installed_rows = [row for row in self.rows if row["installed"]]
        external_rows = [row for row in self.rows if row["external_total_bytes"] > 0]
        residue_rows = [row for row in self.rows if row["residue"]]
        apk_bytes = sum(row["apk_bytes"] or 0 for row in self.rows if row["apk_bytes"] is not None)
        data_bytes = sum(row["external_data_bytes"] for row in self.rows)
        media_bytes = sum(row["external_media_bytes"] for row in self.rows)
        obb_bytes = sum(row["external_obb_bytes"] for row in self.rows)
        cache_bytes = sum(row["external_cache_bytes"] for row in self.rows)
        external_total = data_bytes + media_bytes + obb_bytes
        total = apk_bytes + external_total
        limitations = list(self.limitations)
        if not self.apk_available and self.manifest_status not in {"disabled", "unsupported"}:
            limitations.append("APK 基础大小不可用；外部 data/media/obb 仍来自文件扫描。")
        if self.manifest_status == "disabled":
            limitations.append("本轮未采集 APK 基础大小（--no-app-storage 或配置关闭）；外部目录仍来自文件扫描。")
        limitations.append("内部 data/cache 与 split APK 未计入；本区块的合计不能当作应用总占用，也不能与设备已用空间相加。")
        if self._dir_stats_ok:
            self.status = self.manifest_status
            if self.status in {"not_collected", "unavailable"} and external_total:
                self.status = "partial"
            if self.status in {"disabled", "unsupported"}:
                self.available = False
            else:
                self.available = self.apk_available or bool(external_total)
        else:
            self.status = "partial"
            self.available = self.apk_available
        reason = self.manifest_reason
        if not self.apk_available and external_total and not reason:
            reason = "仅外部目录可用；APK 基础大小不可用"
        self.reason = reason
        top_rows = sorted(
            (row for row in installed_rows if row["external_total_bytes"] > 0),
            key=lambda row: row["external_total_bytes"], reverse=True,
        )[: self.top_apps]
        residue = sorted(residue_rows, key=lambda row: row["external_total_bytes"], reverse=True)
        return {
            "schema": 1,
            "status": self.status,
            "available": self.available,
            "reason": self.reason,
            "packages": {
                "total": len(self.rows),
                "installed": len(installed_rows),
                "system": sum(1 for row in self.rows if row["system"]),
                "with_external": len(external_rows),
                "residue": len(residue_rows),
            },
            "known": {
                "apk_bytes": apk_bytes,
                "apk_human": human_bytes(apk_bytes),
                "external_data_bytes": data_bytes,
                "external_data_human": human_bytes(data_bytes),
                "external_media_bytes": media_bytes,
                "external_media_human": human_bytes(media_bytes),
                "external_obb_bytes": obb_bytes,
                "external_obb_human": human_bytes(obb_bytes),
                "external_cache_bytes": cache_bytes,
                "external_cache_human": human_bytes(cache_bytes),
                "external_total_bytes": external_total,
                "external_total_human": human_bytes(external_total),
                "total_bytes": total,
                "total_human": human_bytes(total),
            },
            "unknown": {
                "internal_data": "不可读（需要 StorageStatsManager 权限或 Root）",
                "internal_cache": "不可读（需要 StorageStatsManager 权限或 Root）",
                "split_apk": "未采集",
            },
            "top_external": [self._public_row(row) for row in top_rows],
            "residue": [self._public_row(row) for row in residue],
            "limitations": list(dict.fromkeys(limitations)),
            "manifest_path": str(self.aux_path),
        }

    @staticmethod
    def _public_row(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "package": row["package"],
            "label": row["label"],
            "kind": row["kind"],
            "installed": row["installed"],
            "system": row["system"],
            "apk_human": human_bytes(row["apk_bytes"] or 0),
            "data_human": human_bytes(row["external_data_bytes"]),
            "media_human": human_bytes(row["external_media_bytes"]),
            "obb_human": human_bytes(row["external_obb_bytes"]),
            "cache_human": human_bytes(row["external_cache_bytes"]),
            "external_total_human": human_bytes(row["external_total_bytes"]),
            "total_human": human_bytes(row["total_known_bytes"]),
            "external_total_bytes": row["external_total_bytes"],
        }

    def attach_storage(self, storage: dict[str, Any]) -> None:
        app = self.summary()
        known = app.get("known") or {}
        block = storage.setdefault("app_storage", {})
        block.update({
            "status": app.get("status"),
            "available": app.get("available"),
            "reason": app.get("reason"),
            "packages": (app.get("packages") or {}).get("total", 0),
            "installed": (app.get("packages") or {}).get("installed", 0),
            "apk_bytes": known.get("apk_bytes", 0),
            "apk_human": known.get("apk_human", "0 B"),
            "external_total_bytes": known.get("external_total_bytes", 0),
            "external_total_human": known.get("external_total_human", "0 B"),
            "external_data_human": known.get("external_data_human", "0 B"),
            "external_media_human": known.get("external_media_human", "0 B"),
            "external_obb_human": known.get("external_obb_human", "0 B"),
            "external_cache_human": known.get("external_cache_human", "0 B"),
            "internal_data": "不可读",
            "internal_cache": "不可读",
            "note": "仅基础 APK + 外部目录；内部 data/cache 与 split APK 未计入，不能与设备已用空间相加。",
        })
        for source in storage.get("sources") or []:
            if source.get("name") == "app_storage":
                source.update({
                    "available": bool(app.get("available")),
                    "status": app.get("status"),
                    "items": (app.get("packages") or {}).get("installed", 0),
                    "bytes": known.get("total_bytes", 0),
                    "reason": app.get("reason") or "",
                })

    # ---------------------------------------------------------------- evidence
    def evidence(self, files: Iterable[CandidateFile], category: str) -> list[dict[str, str]]:
        if category not in APP_EVIDENCE_CATEGORIES:
            return []
        packages: list[str] = []
        for item in files:
            package = package_from_path(item.path)
            if package and package not in packages:
                packages.append(package)
        evidence: list[dict[str, str]] = []
        for package in packages[:2]:
            row = self.by_package.get(package)
            if not row or row["total_known_bytes"] <= 0:
                continue
            state = "包未安装，但外部目录仍有数据" if row["residue"] else (
                "系统应用" if row["system"] else "用户应用"
            )
            evidence.append({
                "code": "app_storage",
                "text": (
                    f"应用级存储：{row['label']} ({package})，{state}；"
                    f"基础 APK {human_bytes(row['apk_bytes'] or 0)}，"
                    f"外部 data {human_bytes(row['external_data_bytes'])}"
                    f"（其中 cache {human_bytes(row['external_cache_bytes'])}）、"
                    f"media {human_bytes(row['external_media_bytes'])}、"
                    f"obb {human_bytes(row['external_obb_bytes'])}；"
                    "内部 data/cache 不可读。"
                ),
            })
        return evidence
