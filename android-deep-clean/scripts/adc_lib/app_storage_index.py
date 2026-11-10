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
    apk_base_bytes INTEGER,
    apk_split_bytes INTEGER,
    apk_file_count INTEGER NOT NULL DEFAULT 0,
    apk_status TEXT NOT NULL DEFAULT '',
    internal_data_bytes INTEGER,
    internal_cache_bytes INTEGER,
    internal_code_cache_bytes INTEGER,
    internal_status TEXT NOT NULL DEFAULT '',
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
            apk_base = apk.get("apk_base_bytes")
            apk_base = int(apk_base) if isinstance(apk_base, int) else None
            apk_split = apk.get("apk_split_bytes")
            apk_split = int(apk_split) if isinstance(apk_split, int) else None
            internal_data = apk.get("internal_data_bytes")
            internal_data = int(internal_data) if isinstance(internal_data, int) else None
            internal_cache = apk.get("internal_cache_bytes")
            internal_cache = int(internal_cache) if isinstance(internal_cache, int) else None
            internal_code_cache = apk.get("internal_code_cache_bytes")
            internal_code_cache = int(internal_code_cache) if isinstance(internal_code_cache, int) else None
            total = (apk_bytes or 0) + (internal_data or 0) + external_total
            if not installed and external_total == 0 and apk_bytes is None and internal_data is None:
                continue
            info = app_info(package)
            coverage_parts = []
            if apk_base is not None:
                coverage_parts.append("apk_base")
            if apk_split is not None and apk_split > 0:
                coverage_parts.append("apk_split")
            if internal_data is not None:
                coverage_parts.append("internal")
            if external_total:
                coverage_parts.append("external")
            self.rows.append({
                "package": package,
                "label": info.get("label", package),
                "kind": info.get("kind", "app"),
                "installed": installed,
                "system": system,
                "version": self.packages.versions.get(package, ""),
                "apk_path": str(apk.get("apk_path") or self.packages.apk_paths.get(package, "")),
                "apk_bytes": apk_bytes,
                "apk_base_bytes": apk_base,
                "apk_split_bytes": apk_split,
                "apk_file_count": _int(apk.get("apk_file_count"), 0),
                "apk_status": str(apk.get("status") or ""),
                "internal_data_bytes": internal_data,
                "internal_cache_bytes": internal_cache,
                "internal_code_cache_bytes": internal_code_cache,
                "internal_status": str(apk.get("internal_status") or ""),
                "external_data_bytes": data,
                "external_media_bytes": media,
                "external_obb_bytes": obb,
                "external_cache_bytes": cache,
                "external_total_bytes": external_total,
                "total_known_bytes": total,
                "residue": (not installed) and external_total > 0,
                "coverage": "+".join(coverage_parts) or "unknown",
            })
        if self.rows:
            db.executemany(
                """INSERT OR REPLACE INTO app_storage
                (package,label,kind,installed,system,version,apk_path,apk_bytes,
                 apk_base_bytes,apk_split_bytes,apk_file_count,apk_status,
                 internal_data_bytes,internal_cache_bytes,internal_code_cache_bytes,internal_status,
                 external_data_bytes,external_media_bytes,external_obb_bytes,external_cache_bytes,
                 total_known_bytes,residue,coverage)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        row["package"], row["label"], row["kind"], 1 if row["installed"] else 0,
                        1 if row["system"] else 0, row["version"], row["apk_path"], row["apk_bytes"],
                        row["apk_base_bytes"], row["apk_split_bytes"], row["apk_file_count"], row["apk_status"],
                        row["internal_data_bytes"], row["internal_cache_bytes"], row["internal_code_cache_bytes"],
                        row["internal_status"],
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
        apk_base_bytes = sum(row["apk_base_bytes"] or 0 for row in self.rows if row["apk_base_bytes"] is not None)
        apk_split_bytes = sum(row["apk_split_bytes"] or 0 for row in self.rows if row["apk_split_bytes"] is not None)
        apk_file_count = sum(int(row["apk_file_count"] or 0) for row in self.rows)
        base_unknown = sum(
            1 for row in self.rows
            if row["apk_bytes"] is not None and row["apk_base_bytes"] is None
        )
        # Only apps whose internal data was actually measured can contribute.
        # A cache we could not read stays unknown: summing it as 0 would turn a
        # permission failure into a fake "0 B" in every report.
        measured_rows = [row for row in self.rows if row["internal_data_bytes"] is not None]
        internal_data_bytes = sum(int(row["internal_data_bytes"] or 0) for row in measured_rows)
        cache_known_rows = [
            row for row in measured_rows if row["internal_cache_bytes"] is not None
        ]
        cache_unknown = len(measured_rows) - len(cache_known_rows)
        internal_cache_bytes = sum(int(row["internal_cache_bytes"] or 0) for row in cache_known_rows)
        data_bytes = sum(row["external_data_bytes"] for row in self.rows)
        media_bytes = sum(row["external_media_bytes"] for row in self.rows)
        obb_bytes = sum(row["external_obb_bytes"] for row in self.rows)
        cache_bytes = sum(row["external_cache_bytes"] for row in self.rows)
        external_total = data_bytes + media_bytes + obb_bytes
        total = apk_bytes + internal_data_bytes + external_total
        limitations = list(self.limitations)
        if not self.apk_available and self.manifest_status not in {"disabled", "unsupported"}:
            limitations.append("APK 大小不可用；外部 data/media/obb 仍来自文件扫描。")
        if self.manifest_status == "disabled":
            limitations.append("本轮未采集 APK 大小（--no-app-storage 或配置关闭）；外部目录仍来自文件扫描。")
        limitations.append("内部 data 包含 cache/code_cache；外部 data 包含外部 cache；分项是子集，不能重复相加。")
        if self._dir_stats_ok:
            self.status = self.manifest_status
            if self.status in {"not_collected", "unavailable"} and (external_total or internal_data_bytes):
                self.status = "partial"
            if self.status in {"disabled", "unsupported"}:
                self.available = False
            else:
                self.available = self.apk_available or bool(external_total) or bool(internal_data_bytes)
        else:
            self.status = "partial"
            self.available = self.apk_available
        reason = self.manifest_reason
        if not self.apk_available and external_total and not reason:
            reason = "仅外部目录可用；APK 大小不可用"
        self.reason = reason
        top_rows = sorted(
            (row for row in installed_rows if row["external_total_bytes"] > 0),
            key=lambda row: row["external_total_bytes"], reverse=True,
        )[: self.top_apps]
        residue = sorted(residue_rows, key=lambda row: row["external_total_bytes"], reverse=True)
        internal_statuses = {row["internal_status"] for row in self.rows}
        internal_any_partial = "partial" in internal_statuses
        internal_readable = bool(internal_statuses & {"collected", "partial"})
        if internal_any_partial:
            internal_state = "部分可读（只读 du，部分目录不可读，数值为下限）"
        elif internal_readable:
            internal_state = "部分可读（只读 du）"
        else:
            internal_state = "不可读（需要 StorageStatsManager 权限或 Root）"
        if not measured_rows:
            cache_state = "未采集"
            cache_human = "未知"
        elif cache_unknown == len(measured_rows):
            cache_state = "du 未返回 cache 大小"
            cache_human = "未知"
        elif cache_unknown:
            cache_state = f"{cache_unknown} 个应用的 cache 不可读，合计为下限"
            cache_human = human_bytes(internal_cache_bytes)
        else:
            cache_state = ""
            cache_human = human_bytes(internal_cache_bytes)
        split_status = str((self.manifest.get("apk") or {}).get("split_status") or "")
        split_state = {
            "collected": "已尝试基础 APK + split APK",
            "partial": "部分应用已查询 split APK（达到上限）",
            "disabled": "未查询 split APK",
            "unavailable": "split APK 不可用",
        }.get(split_status, "未采集")
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
                "apk_base_bytes": apk_base_bytes,
                "apk_base_human": human_bytes(apk_base_bytes),
                "apk_split_bytes": apk_split_bytes,
                "apk_split_human": human_bytes(apk_split_bytes),
                "apk_file_count": apk_file_count,
                "internal_data_bytes": internal_data_bytes,
                "internal_data_human": human_bytes(internal_data_bytes),
                "internal_cache_bytes": internal_cache_bytes,
                "internal_cache_human": cache_human,
                "internal_cache_unknown": cache_unknown,
                "internal_cache_measured": len(measured_rows),
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
                "internal_data": internal_state,
                "internal_cache": cache_state or "已读取",
                "split_apk": split_state,
                "apk_base": (
                    f"{base_unknown} 个应用的基础 APK 不可读或未识别，合计为下限"
                    if base_unknown else ""
                ),
            },
            "capabilities": self.manifest.get("capabilities") or {},
            "internal_status": str((self.manifest.get("internal") or {}).get("status") or ""),
            "apk_status": str((self.manifest.get("apk") or {}).get("status") or ""),
            "split_status": split_status,
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
            "apk_base_human": human_bytes(row["apk_base_bytes"] or 0),
            "apk_split_human": human_bytes(row["apk_split_bytes"] or 0),
            "apk_file_count": int(row["apk_file_count"] or 0),
            "internal_data_human": human_bytes(row["internal_data_bytes"] or 0),
            "internal_cache_human": (
                "-" if row["internal_data_bytes"] is None
                else ("未知" if row["internal_cache_bytes"] is None
                      else human_bytes(row["internal_cache_bytes"]))
            ),
            "internal_status": row["internal_status"],
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
            "apk_base_bytes": known.get("apk_base_bytes", 0),
            "apk_base_human": known.get("apk_base_human", "0 B"),
            "apk_split_bytes": known.get("apk_split_bytes", 0),
            "apk_split_human": known.get("apk_split_human", "0 B"),
            "apk_file_count": known.get("apk_file_count", 0),
            "internal_data_bytes": known.get("internal_data_bytes", 0),
            "internal_data_human": known.get("internal_data_human", "0 B"),
            "internal_cache_bytes": known.get("internal_cache_bytes", 0),
            "internal_cache_human": known.get("internal_cache_human", "0 B"),
            "external_total_bytes": known.get("external_total_bytes", 0),
            "external_total_human": known.get("external_total_human", "0 B"),
            "external_data_human": known.get("external_data_human", "0 B"),
            "external_media_human": known.get("external_media_human", "0 B"),
            "external_obb_human": known.get("external_obb_human", "0 B"),
            "external_cache_human": known.get("external_cache_human", "0 B"),
            "internal_data": (app.get("unknown") or {}).get("internal_data", "不可读"),
            "internal_cache": (app.get("unknown") or {}).get("internal_cache", "不可读"),
            "split_apk": (app.get("unknown") or {}).get("split_apk", "未采集"),
            "capabilities": app.get("capabilities") or {},
            "note": "APK 含基础与已查询的 split APK；内部 data 含 cache/code_cache，外部 data 含外部 cache；分项是子集，不能重复相加。",
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
            apk_text = f"基础 APK {human_bytes(row['apk_bytes'] or 0)}"
            if (row["apk_split_bytes"] or 0) > 0:
                apk_text = (
                    f"APK 合计 {human_bytes(row['apk_bytes'] or 0)}"
                    f"（基础 {human_bytes(row['apk_base_bytes'] or 0)} + "
                    f"split {human_bytes(row['apk_split_bytes'] or 0)}）"
                )
            if row["internal_data_bytes"] is not None:
                cache_text = (
                    "未知" if row["internal_cache_bytes"] is None
                    else human_bytes(row["internal_cache_bytes"])
                )
                internal_text = (
                    f"内部 data {human_bytes(row['internal_data_bytes'])}"
                    f"（cache {cache_text}）"
                )
            else:
                internal_text = "内部 data/cache 不可读"
            evidence.append({
                "code": "app_storage",
                "text": (
                    f"应用级存储：{row['label']} ({package})，{state}；"
                    f"{apk_text}，"
                    f"外部 data {human_bytes(row['external_data_bytes'])}"
                    f"（其中 cache {human_bytes(row['external_cache_bytes'])}）、"
                    f"media {human_bytes(row['external_media_bytes'])}、"
                    f"obb {human_bytes(row['external_obb_bytes'])}；"
                    f"{internal_text}。"
                ),
            })
        return evidence
