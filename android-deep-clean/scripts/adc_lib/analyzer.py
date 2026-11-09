"""Candidate generation and deep-analysis manifest.

Deterministic, evidence-based, read-only. Creates:
- analysis.db with candidate groups + exact expected file manifest
- analysis.json / analysis-summary.json
- review-template.json for the AI layer
- report-before.md (via reports.analysis_markdown)
"""

from __future__ import annotations

import json
import posixpath
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .app_storage_index import AppStorageIndex
from .archive import DirectoryArchive
from .html_report import write_analysis_html
from .media_index import MediaIndex
from .models import CandidateFile, PackageIndex
from .reconcile import build_storage_accounting
from .reports import analysis_markdown
from .space_story import build_space_story
from .rules import (
    APK_EXTS,
    ARCHIVE_EXTS,
    CATEGORY_META,
    LOG_EXTS,
    MEDIA_EXTS,
    TEMP_EXTS,
    THUMB_DIR_NAMES,
    TRASH_DIR_NAMES,
    app_info,
    category_risk,
    category_impact,
    category_title,
    describe_app,
    guard_path,
    is_chat_path,
    is_ota_file,
    is_screenshot,
)
from .state import State
from .transport import Transport
from .util import (
    age_label,
    atomic_write_json,
    canonical_json,
    compute_files_digest,
    display_path,
    human_bytes,
    now_iso,
    parse_age_days,
    read_json,
    sha256_file,
    sha256_text,
    stable_id,
)

ANALYSIS_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    risk TEXT NOT NULL,
    action TEXT NOT NULL,
    primary_path TEXT NOT NULL,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    dir_count INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.5,
    app_pkg TEXT NOT NULL DEFAULT '',
    app_kind TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    reasons_json TEXT NOT NULL DEFAULT '[]',
    keep_suggestion TEXT NOT NULL DEFAULT '',
    ai_review_required INTEGER NOT NULL DEFAULT 0,
    selectable INTEGER NOT NULL DEFAULT 1,
    sample_paths_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT '',
    files_digest TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS candidate_files (
    candidate_id TEXT NOT NULL,
    path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    mtime INTEGER,
    inode INTEGER,
    dev INTEGER,
    kind TEXT NOT NULL DEFAULT 'file',
    role TEXT NOT NULL DEFAULT 'delete',
    PRIMARY KEY (candidate_id, path)
);
CREATE INDEX IF NOT EXISTS idx_cf_candidate ON candidate_files(candidate_id);
CREATE TABLE IF NOT EXISTS claimed (
    path TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS protected_items (
    path TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    risk TEXT NOT NULL DEFAULT 'DANGEROUS'
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_RISK_ORDER = {"SAFE": 0, "CAUTION": 1, "DANGEROUS": 2}

# Archive members often carry 1980-era placeholder dates and clock skew can
# produce future times. Such mtimes must never drive age-based selection.
MTIME_ANOMALY_MIN = 946684800  # 2000-01-01T00:00:00Z
MTIME_ANOMALY_FUTURE_SECONDS = 86400


class Analyzer:
    def __init__(
        self,
        transport: Transport,
        config: dict[str, Any],
        state: State,
        scan_conn: sqlite3.Connection,
        scan_meta: dict[str, Any],
        packages: PackageIndex,
        *,
        hash_mode: str = "device",
    ) -> None:
        self.transport = transport
        self.config = config
        self.state = state
        self.scan_conn = scan_conn
        self.scan_meta = scan_meta
        self.packages = packages
        import time

        self.now = time.time()
        self.limitations: list[str] = list(scan_meta.get("limitations") or [])
        self.review_items: list[dict[str, Any]] = []
        self.candidates_written = 0
        self.limit_reached = False
        self.category_counts: dict[str, int] = {}
        self.sample_limit = int(config["report"]["sample_paths"])
        self.max_candidates = int(config["scan"]["max_candidates"])
        self.hash_mode = hash_mode if hash_mode in {"device", "pc"} else "device"
        self.platform = str(
            scan_meta.get("platform")
            or (scan_meta.get("capabilities") or {}).get("platform")
            or "android"
        )
        self.directory_diff: dict[str, Any] = read_json(
            self.state.dir("scan") / "directory-diff.json", default={}
        ) or {}
        self._diff_maps: dict[str, dict[str, dict[str, Any]]] = {}
        for key in ("new", "grew", "shrunk", "resurrected", "app_updated", "orphan_suspect"):
            items = self.directory_diff.get(key) if isinstance(self.directory_diff, dict) else []
            self._diff_maps[key] = {
                str(item.get("path")): item for item in (items or []) if item.get("path")
            }
        self._archive_rows: dict[str, dict[str, Any]] = {}
        archive_path = self.state.root / "history" / "archive.db"
        if archive_path.exists():
            try:
                archive = DirectoryArchive(archive_path)
                self._archive_rows = archive.rows()
                archive.close()
            except Exception:  # noqa: BLE001 - archive must never break analysis
                self._archive_rows = {}
        scan_db = state.require("scan")
        self.out_dir = state.dir("analysis")
        self.db_path = self.out_dir / "analysis.db"
        if self.db_path.exists():
            self.db_path.unlink()
        self.db = sqlite3.connect(str(self.db_path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(ANALYSIS_SCHEMA)
        self.db.execute("ATTACH DATABASE ? AS scan", (str(scan_db),))
        self._analysis_id = stable_id("analysis", scan_meta.get("scanned_at", ""), canonical_json(config)[:100])
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('analysis_id',?)", (self._analysis_id,))
        self.db.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES ('scan_meta',?)",
            (json.dumps(scan_meta, ensure_ascii=False),),
        )
        self.media_index = MediaIndex(self.state, self.scan_conn, self.scan_meta, self.config)
        self.media_index.load()
        self.media_index.ingest(self.db)
        self.app_storage = AppStorageIndex(
            self.state, self.scan_conn, self.scan_meta, self.config, self.packages
        )
        self.app_storage.load()
        self.app_storage.ingest(self.db)

    # ------------------------------------------------------------- db helpers
    def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self.db.execute(sql, tuple(params)))

    def _one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        return self.db.execute(sql, tuple(params)).fetchone()

    @staticmethod
    def _like_prefix(prefix: str) -> str:
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return escaped + "/%"

    def _entries_under(self, path: str) -> list[sqlite3.Row]:
        return self._rows(
            "SELECT path,parent,name,kind,size,mtime,inode,dev FROM scan.entries "
            "WHERE path = ? OR path LIKE ? ESCAPE '\\' ORDER BY path",
            (path, self._like_prefix(path)),
        )

    def _children(self, path: str) -> list[sqlite3.Row]:
        return self._rows(
            "SELECT path,parent,name,kind,size,mtime,inode,dev FROM scan.entries WHERE parent=? ORDER BY path",
            (path,),
        )

    def _dir_row(self, path: str) -> sqlite3.Row | None:
        return self._one(
            "SELECT path,parent,name,kind,size,mtime,inode,dev FROM scan.entries WHERE path=? AND kind='dir'",
            (path,),
        )

    def _limit(self) -> bool:
        if self.candidates_written >= self.max_candidates:
            self.limit_reached = True
            return True
        return False

    @staticmethod
    def _file_from_row(row: sqlite3.Row, role: str = "delete") -> CandidateFile:
        keys = row.keys()

        def value(name: str, default: Any = None) -> Any:
            return row[name] if name in keys else default

        return CandidateFile(
            path=row["path"],
            size=int(value("size", 0) or 0),
            mtime=value("mtime"),
            inode=value("inode"),
            dev=value("dev"),
            kind=str(value("kind", "file") or "file"),
            role=role,
        )

    def _history_evidence(self, path: str) -> list[dict[str, str]]:
        """Cross-round facts from the directory archive.

        These are evidence strings only.  They never change risk, selectability
        or the candidate's file manifest.
        """
        evidence: list[dict[str, str]] = []
        record = self._archive_rows.get(path)
        if record:
            first_seen = str(record.get("first_seen_at") or "")
            last_seen = str(record.get("last_seen_at") or "")
            parts = ["跨轮档案"]
            if first_seen:
                parts.append(f"首次记录 {first_seen}")
            if last_seen:
                parts.append(f"最近记录 {last_seen}")
            if int(record.get("absent_rounds") or 0) > 0:
                parts.append(f"连续 {int(record['absent_rounds'])} 轮未再出现")
            if int(record.get("resurrect_count") or 0) > 0:
                parts.append(f"曾消失后重新出现 {int(record['resurrect_count'])} 次")
            if record.get("app_version"):
                parts.append(f"应用版本 {record['app_version']}")
            if record.get("decision"):
                note = f"（{record['note']}）" if record.get("note") else ""
                parts.append(f"档案标记 {record['decision']}{note}")
            evidence.append({"code": "history", "text": "；".join(parts)})

        grew = self._diff_maps.get("grew", {}).get(path)
        if grew:
            evidence.append({
                "code": "history_grew",
                "text": f"跨轮变化：本轮较上轮增长 {human_bytes(int(grew.get('delta_bytes') or 0))}"
                        f"（{grew.get('delta_percent', 0)}%）",
            })
        shrunk = self._diff_maps.get("shrunk", {}).get(path)
        if shrunk:
            evidence.append({
                "code": "history_shrunk",
                "text": f"跨轮变化：本轮较上轮缩小 {human_bytes(abs(int(shrunk.get('delta_bytes') or 0)))}",
            })
        if path in self._diff_maps.get("new", {}):
            evidence.append({"code": "history_new", "text": "跨轮变化：上轮扫描未记录该目录"})
        resurrected = self._diff_maps.get("resurrected", {}).get(path)
        if resurrected:
            evidence.append({
                "code": "history_resurrected",
                "text": f"跨轮变化：该目录曾在扫描中消失，本轮重新出现"
                        f"（累计 {int(resurrected.get('resurrect_count') or 1)} 次）",
            })
        updated = self._diff_maps.get("app_updated", {}).get(path)
        if updated:
            evidence.append({
                "code": "history_app_updated",
                "text": f"应用版本从 {updated.get('previous_version') or '-'} 变为 "
                        f"{updated.get('version') or '-'}；增长可能来自应用更新",
            })
        orphan = self._diff_maps.get("orphan_suspect", {}).get(path)
        if orphan:
            evidence.append({
                "code": "history_orphan",
                "text": f"跨轮残留线索：{orphan.get('reason') or '应用可能已卸载'}，"
                        f"置信度 {orphan.get('confidence') or '-'}",
            })
        return evidence

    # ------------------------------------------------------------ candidate add
    def _add_candidate(
        self,
        *,
        category: str,
        action: str,
        primary_path: str,
        files: list[CandidateFile],
        risk: str | None = None,
        title: str | None = None,
        evidence: list[dict[str, str]] | None = None,
        reasons: list[str] | None = None,
        confidence: float = 0.7,
        keep_suggestion: str = "",
        app_pkg: str = "",
        app_kind: str = "",
        notes: str = "",
    ) -> str | None:
        if self._limit() or not files or category not in CATEGORY_META or category == "protected":
            return None
        effective_risk = risk or category_risk(category)
        # Generic app/browser caches may contain offline downloads or unsaved
        # user material. They are CAUTION by default and need explicit review.
        if category in {"app_cache", "browser_cache"}:
            policy = str((self.config.get("safety") or {}).get("generic_cache_risk", "caution")).lower()
            if policy != "safe":
                effective_risk = _max_risk(effective_risk, "CAUTION")
        accepted: list[CandidateFile] = []
        for item in files:
            guard = guard_path(item.path, category, kind=item.kind, packages=self.packages, config=self.config, platform=self.platform)
            if not guard.get("allowed"):
                continue
            effective_risk = _max_risk(effective_risk, str(guard.get("risk_floor") or "SAFE"))
            if effective_risk == "DANGEROUS":
                return None
            accepted.append(item)
        if not accepted:
            return None
        base_id = stable_id(category, primary_path)
        candidate_id = base_id
        suffix = 2
        while self._one("SELECT 1 FROM candidates WHERE id=?", (candidate_id,)):
            candidate_id = f"{base_id}-{suffix}"
            suffix += 1
        final_files: list[CandidateFile] = []
        for item in accepted:
            if item.kind == "file":
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO claimed(path,candidate_id) VALUES (?,?)",
                    (item.path, candidate_id),
                )
                if cur.rowcount == 0:
                    continue
            final_files.append(item)
        delete_files = [item for item in final_files if item.kind == "file"]
        if action != "rmdir" and not delete_files:
            self.db.execute("DELETE FROM claimed WHERE candidate_id=?", (candidate_id,))
            return None
        total_bytes = sum(max(0, item.size) for item in delete_files)
        dir_count = sum(1 for item in final_files if item.kind == "dir")
        # Evidence is authored by the generator from the pre-filter file list.
        # Rewrite count/size so the report never claims more files or bytes than
        # the candidate actually stores (protected files are dropped above).
        checked_evidence: list[dict[str, str]] = []
        for entry in evidence or []:
            entry = dict(entry)
            if entry.get("code") == "count":
                entry["text"] = f"文件 {len(delete_files)} 个，目录 {dir_count} 个"
            elif entry.get("code") == "size":
                entry["text"] = f"逻辑大小 {human_bytes(total_bytes)}"
            checked_evidence.append(entry)
        checked_evidence.extend(self.media_index.evidence(final_files, category))
        checked_evidence.extend(self.app_storage.evidence(final_files, category))
        checked_evidence.extend(self._history_evidence(primary_path))
        sample_paths = [item.path for item in delete_files[: self.sample_limit]] or [
            item.path for item in final_files if item.kind == "dir"
        ][: self.sample_limit] or [primary_path]
        display_title = title or f"{category_title(category)}：{display_path(primary_path)}"
        review_required = effective_risk == "CAUTION" and bool(
            self.config["safety"].get("require_ai_review_for_caution", True)
        )
        self.db.execute(
            """INSERT OR REPLACE INTO candidates
            (id,category,title,risk,action,primary_path,total_bytes,file_count,dir_count,confidence,
             app_pkg,app_kind,evidence_json,reasons_json,keep_suggestion,ai_review_required,selectable,
             sample_paths_json,notes,files_digest)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                candidate_id, category, display_title, effective_risk, action, primary_path,
                total_bytes, len(delete_files), dir_count,
                float(confidence), app_pkg, app_kind,
                json.dumps(checked_evidence, ensure_ascii=False),
                json.dumps(reasons or [], ensure_ascii=False),
                keep_suggestion, 1 if review_required else 0,
                0 if effective_risk == "DANGEROUS" else 1,
                json.dumps(sample_paths, ensure_ascii=False), notes,
                compute_files_digest(final_files),
            ),
        )
        self.db.executemany(
            "INSERT OR REPLACE INTO candidate_files(candidate_id,path,size,mtime,inode,dev,kind,role) VALUES (?,?,?,?,?,?,?,?)",
            [
                (candidate_id, item.path, item.size, item.mtime, item.inode, item.dev, item.kind, item.role)
                for item in final_files
            ],
        )
        self.candidates_written += 1
        self.category_counts[category] = self.category_counts.get(category, 0) + 1
        if review_required:
            self.review_items.append(
                {
                    "id": candidate_id,
                    "category": category,
                    "title": display_title,
                    "risk": effective_risk,
                    "total_bytes": total_bytes,
                    "human_size": human_bytes(total_bytes),
                    "file_count": len(delete_files),
                    "sample_paths": sample_paths,
                    "evidence": checked_evidence,
                }
            )
        return candidate_id

    def _group_files_candidate(self, *, category: str, action: str = "delete_files", parent: str,
                               files: list[CandidateFile], risk: str | None = None,
                               title: str | None = None, evidence: list[dict[str, str]] | None = None,
                               reasons: list[str] | None = None, confidence: float = 0.7,
                               keep_suggestion: str = "", app_pkg: str = "", app_kind: str = "") -> str | None:
        if not files:
            return None
        return self._add_candidate(
            category=category, action=action, primary_path=parent, files=files, risk=risk,
            title=title, evidence=evidence, reasons=reasons, confidence=confidence,
            keep_suggestion=keep_suggestion, app_pkg=app_pkg, app_kind=app_kind,
        )

    # ---------------------------------------------------------------- helpers
    def _mtime_anomalous(self, mtime: int | None) -> bool:
        if not mtime:
            return False
        return int(mtime) < MTIME_ANOMALY_MIN or int(mtime) > self.now + MTIME_ANOMALY_FUTURE_SECONDS

    def _age_days(self, mtime: int | None) -> float | None:
        if self._mtime_anomalous(mtime):
            return None
        return parse_age_days(mtime, now=self.now)

    def _app_meta(self, path: str) -> tuple[str, str, str]:
        from .rules import package_from_path

        package = package_from_path(path)
        if not package:
            return "", "", ""
        info = app_info(package)
        return package, info.get("label", package), info.get("kind", "app")

    def _base_evidence(self, path: str, files: list[CandidateFile]) -> list[dict[str, str]]:
        ages = [age for age in (self._age_days(item.mtime) for item in files if item.mtime) if age is not None]
        evidence = [
            {"code": "path", "text": f"路径：{display_path(path)}"},
            {"code": "count", "text": f"文件 {sum(1 for i in files if i.kind == 'file')} 个，目录 {sum(1 for i in files if i.kind == 'dir')} 个"},
            {"code": "size", "text": f"逻辑大小 {human_bytes(sum(i.size for i in files if i.kind == 'file'))}"},
        ]
        if ages:
            evidence.append({"code": "age", "text": f"最旧文件约 {age_label(max(ages))} 未修改"})
        anomalous = [item for item in files if self._mtime_anomalous(item.mtime)]
        if anomalous:
            evidence.append({
                "code": "mtime_anomaly",
                "text": f"{len(anomalous)} 个文件时间戳异常（早于 2000 年或晚于当前时间），年龄不可作为判断依据",
            })
        return evidence

    # ------------------------------------------------------------- generators
    def gen_residue(self) -> None:
        if not self.packages.available:
            self.limitations.append("包管理器不可用，跳过卸载残留检测。")
            return
        candidates: list[tuple[str, str]] = []
        for package in sorted(self.packages.uninstalled):
            for base in ("data", "obb", "media"):
                candidates.append((package, f"/sdcard/Android/{base}/{package}"))
        for package, path in candidates:
            if self._limit():
                return
            row = self._dir_row(path)
            if not row:
                continue
            rows = self._entries_under(path)
            files = [self._file_from_row(row, role="target_dir")] + [
                self._file_from_row(item) for item in rows if item["path"] != path
            ]
            info = app_info(package)
            evidence = self._base_evidence(path, files) + [
                {"code": "package", "text": f"包名 {package} 不在已安装列表"},
                {"code": "app", "text": f"应用类型：{info.get('kind')}，显示名：{info.get('label')}"},
            ]
            self._add_candidate(
                category="residue", action="clean_dir_children", primary_path=path, files=files,
                risk="CAUTION", title=f"卸载残留：{describe_app(package)}", evidence=evidence,
                reasons=["包管理器显示该应用未安装，但外部存储仍保留其数据目录。",
                         "可能包含用户下载、游戏存档或媒体，先复核内容再决定。"],
                confidence=0.8, keep_suggestion="如果计划重装应用，或目录内有需要保留的存档/下载，请保留或先导出。",
                app_pkg=package, app_kind=info.get("kind", "app"),
            )
        known = self.packages.all_known
        rows = self._rows(
            "SELECT path,parent,name,kind,size,mtime,inode,dev FROM scan.entries "
            "WHERE kind='dir' AND parent='/sdcard/Android/data'"
        )
        for row in rows:
            if self._limit():
                return
            package = row["name"]
            if package in known:
                continue
            path = row["path"]
            inner = self._entries_under(path)
            files = [self._file_from_row(row, role="target_dir")] + [
                self._file_from_row(item) for item in inner if item["path"] != path
            ]
            self._add_candidate(
                category="residue", action="clean_dir_children", primary_path=path, files=files,
                risk="CAUTION", title=f"未知残留目录：{package}",
                evidence=self._base_evidence(path, files) + [
                    {"code": "package", "text": f"目录名 {package} 不对应任何已知包名"},
                ],
                reasons=["目录名不是已知包名，可能是旧版本/改包名/异常卸载留下的外部数据。"],
                confidence=0.6, keep_suggestion="先确认目录内容与来源；可能是某个仍在使用的应用的旧包名。",
                app_pkg=package,
            )

    def gen_caches(self) -> None:
        rows = self._rows(
            "SELECT path,parent,name,kind,size,mtime,inode,dev FROM scan.entries "
            "WHERE kind='dir' AND lower(name) IN ('cache','code_cache') "
            "ORDER BY length(path) ASC LIMIT 400"
        )
        for row in rows:
            if self._limit():
                return
            path = row["path"]
            guard = guard_path(path, "app_cache", kind="dir", packages=self.packages, config=self.config, platform=self.platform)
            if not guard.get("allowed"):
                continue
            package, label, kind = self._app_meta(path)
            category = "app_cache"
            if is_chat_path(path):
                category = "chat_cache"
            elif kind == "browser":
                category = "browser_cache"
            inner = self._entries_under(path)
            files = [self._file_from_row(row, role="target_dir")] + [
                self._file_from_row(item) for item in inner if item["path"] != path
            ]
            total = sum(item.size for item in files if item.kind == "file")
            if total <= 0:
                continue
            risk = "CAUTION" if category == "chat_cache" else "SAFE"
            if total >= int(self.config["thresholds"]["large_cache_caution_bytes"]):
                risk = "CAUTION"
            evidence = self._base_evidence(path, files) + [
                {"code": "cache", "text": f"目录名 {posixpath.basename(path)}，属于缓存目录，应用可重新生成"},
            ]
            if package:
                evidence.append({"code": "app", "text": f"应用：{label} ({package})，kind={kind}"})
                evidence.append({
                    "code": "installed",
                    "text": "应用状态：已安装" if self.packages.is_installed(package) else "应用状态：未知或未安装",
                })
            self._add_candidate(
                category=category, action="clean_dir_children", primary_path=path, files=files,
                risk=risk, title=f"{label or '应用'}缓存：{display_path(path)}", evidence=evidence,
                reasons=["缓存目录通常由应用自动重建，清理后不会删除账号、聊天记录或数据库。",
                         "如果目录内包含离线下载/离线地图等大文件，清理后需要重新下载。"],
                confidence=0.9 if category == "app_cache" else 0.75,
                keep_suggestion="如该应用正在下载离线内容，可在下载完成后再清理。",
                app_pkg=package, app_kind=kind,
            )

    def gen_thumbnails(self) -> None:
        placeholders = ",".join("?" for _ in THUMB_DIR_NAMES)
        rows = self._rows(
            f"SELECT path,parent,name,kind,size,mtime,inode,dev FROM scan.entries "
            f"WHERE kind='dir' AND lower(name) IN ({placeholders}) ORDER BY length(path) ASC LIMIT 120",
            tuple(sorted(THUMB_DIR_NAMES)),
        )
        for row in rows:
            if self._limit():
                return
            path = row["path"]
            guard = guard_path(path, "thumbnails", kind="dir", packages=self.packages, config=self.config, platform=self.platform)
            if not guard.get("allowed"):
                continue
            inner = self._entries_under(path)
            files = [self._file_from_row(row, role="target_dir")] + [
                self._file_from_row(item) for item in inner if item["path"] != path
            ]
            total = sum(item.size for item in files if item.kind == "file")
            if total <= 0:
                continue
            risk = str(guard.get("risk_floor") or "SAFE")
            if risk == "DANGEROUS":
                continue
            self._add_candidate(
                category="thumbnails", action="clean_dir_children", primary_path=path, files=files,
                risk=risk, title=f"缩略图缓存：{display_path(path)}",
                evidence=self._base_evidence(path, files) + [
                    {"code": "thumbnail", "text": "目录名为缩略图缓存，删除后系统/应用会重新生成"},
                ],
                reasons=["缩略图缓存可重新生成，通常不包含原始照片或视频。"],
                confidence=0.85, keep_suggestion="如设备性能较弱、希望保留缩略图加速，可保留。",
            )

    def gen_temp_logs(self) -> None:
        temp_threshold = int(self.config["thresholds"]["temp_old_days"])
        log_threshold = int(self.config["thresholds"]["log_old_days"])
        conditions = []
        params: list[Any] = []
        if TEMP_EXTS:
            conditions.append("lower(ext) IN (" + ",".join("?" for _ in TEMP_EXTS) + ")")
            params.extend(sorted(TEMP_EXTS))
        if LOG_EXTS:
            conditions.append("lower(ext) IN (" + ",".join("?" for _ in LOG_EXTS) + ")")
            params.extend(sorted(LOG_EXTS))
        conditions.append("lower(name) LIKE '%.log%'")
        conditions.append("lower(name) LIKE '%.dmp'")
        rows = self._rows(
            "SELECT path,parent,name,kind,size,mtime,inode,dev,ext FROM scan.entries "
            "WHERE kind='file' AND (" + " OR ".join(conditions) + ") ORDER BY mtime ASC LIMIT 5000",
            params,
        )
        groups: dict[str, list[CandidateFile]] = {}
        for row in rows:
            age = self._age_days(row["mtime"])
            if age is None:
                continue
            is_log = ".log" in row["name"].lower() or (row["ext"] or "") in LOG_EXTS
            if age < (log_threshold if is_log else temp_threshold):
                continue
            if self._one("SELECT 1 FROM claimed WHERE path=?", (row["path"],)):
                continue
            guard = guard_path(row["path"], "temp_logs", kind="file", packages=self.packages, config=self.config, platform=self.platform)
            if not guard.get("allowed"):
                continue
            groups.setdefault(row["parent"], []).append(self._file_from_row(row))
        for parent, files in sorted(groups.items()):
            if self._limit():
                return
            risk = "SAFE"
            for item in files:
                guard = guard_path(item.path, "temp_logs", kind="file", packages=self.packages, config=self.config, platform=self.platform)
                risk = _max_risk(risk, str(guard.get("risk_floor") or "SAFE"))
            self._group_files_candidate(
                category="temp_logs", parent=parent, files=files, risk=risk,
                title=f"临时文件/日志：{display_path(parent)}",
                evidence=self._base_evidence(parent, files) + [
                    {"code": "ext", "text": "扩展名/文件名符合临时文件或日志特征，且超过保留期"},
                ],
                reasons=["临时文件、崩溃日志和未完成下载通常不具备长期保留价值。"],
                confidence=0.8, keep_suggestion="如日志正在用于排障，请先导出或保留。",
            )

    def gen_trash(self) -> None:
        placeholders = ",".join("?" for _ in TRASH_DIR_NAMES)
        rows = self._rows(
            f"SELECT path,parent,name,kind,size,mtime,inode,dev FROM scan.entries "
            f"WHERE kind='dir' AND lower(name) IN ({placeholders}) ORDER BY length(path) ASC LIMIT 80",
            tuple(sorted(TRASH_DIR_NAMES)),
        )
        threshold = int(self.config["thresholds"]["trash_old_days"])
        for row in rows:
            path = row["path"]
            guard = guard_path(path, "trash", kind="dir", packages=self.packages, config=self.config, platform=self.platform)
            if not guard.get("allowed"):
                continue
            inner = self._entries_under(path)
            files = [self._file_from_row(row, role="target_dir")] + [
                self._file_from_row(item) for item in inner if item["path"] != path
            ]
            file_items = [item for item in files if item.kind == "file"]
            if not file_items:
                continue
            ages = [age for age in (self._age_days(item.mtime) for item in file_items if item.mtime) if age is not None]
            if ages and max(ages) < threshold:
                continue
            total = sum(item.size for item in file_items)
            if total <= 0:
                continue
            self._add_candidate(
                category="trash", action="clean_dir_children", primary_path=path, files=files,
                risk="CAUTION", title=f"回收站：{display_path(path)}",
                evidence=self._base_evidence(path, files) + [
                    {"code": "trash", "text": "目录名为回收站/垃圾桶，且文件已超过保留期"},
                ],
                reasons=["回收站中的内容可能仍可恢复；清空后不可恢复。"],
                confidence=0.7, keep_suggestion="如果最近误删过文件，请先在文件管理器中恢复。",
            )
        # Android 11+ MediaProvider / 文件管理器使用 .trashed-<timestamp> 命名
        cutoff = self.now - threshold * 86400
        rows = self._rows(
            "SELECT path,parent,name,size,mtime,inode,dev FROM scan.entries e WHERE kind='file' "
            "AND lower(name) LIKE '.trashed-%' AND mtime IS NOT NULL AND mtime >= ? AND mtime <= ? AND mtime < ? "
            "AND NOT EXISTS (SELECT 1 FROM claimed c WHERE c.path=e.path) ORDER BY mtime ASC LIMIT 500",
            (MTIME_ANOMALY_MIN, self.now + MTIME_ANOMALY_FUTURE_SECONDS, cutoff),
        )
        for parent, items in sorted(self._group_by_parent(rows).items()):
            if self._limit():
                return
            files = [self._file_from_row(row) for row in items]
            self._group_files_candidate(
                category="trash", action="delete_files", parent=parent, files=files, risk="CAUTION",
                title=f"系统回收站文件：{display_path(parent)}",
                evidence=self._base_evidence(parent, files) + [
                    {"code": "trash", "text": "文件名符合 .trashed-* 系统回收站命名，且超过保留期"},
                ],
                reasons=["系统回收站中的文件可能仍可恢复；清理后不可恢复。"],
                confidence=0.7, keep_suggestion="若最近误删过文件，请先在相册/文件管理器中恢复。",
            )

    def _hash_paths(self, paths: list[str], *, expected_size: int) -> dict[str, str | None]:
        """Hash duplicate candidates on the device or on the PC.

        PC mode pulls only the size-collision candidates over USB and computes
        SHA-256 locally; it never asks the phone to hash user files and is
        useful when the device toybox lacks sha256sum.
        """
        if self.hash_mode != "pc":
            return self.transport.hash_files(paths)
        tmp = self.out_dir / "hash-tmp"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        result: dict[str, str | None] = {}
        try:
            for index, path in enumerate(paths):
                temp = tmp / f"{index:04d}-{sha256_text(path)[:12]}.bin"
                try:
                    written = self.transport.pull(path, str(temp))
                    if int(written) != int(expected_size):
                        result[path] = None
                        continue
                    result[path] = sha256_file(temp)
                except Exception:  # noqa: BLE001
                    result[path] = None
                finally:
                    try:
                        if temp.exists():
                            temp.unlink()
                    except OSError:
                        pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return result

    def gen_duplicates(self) -> None:
        caps = self.scan_meta.get("capabilities") or {}
        if self.hash_mode == "device" and not caps.get("hashing", True):
            self.limitations.append("设备缺少 hash 工具，跳过重复文件检测（可改用 --hash-mode pc）。")
            return
        min_size = int(self.config["thresholds"]["min_duplicate_bytes"])
        max_groups = int(self.config["scan"]["max_duplicate_groups"])
        max_hash_files = int(self.config["scan"]["max_hash_files"])
        max_hash_bytes = int(self.config["scan"]["max_hash_total_bytes"])
        size_rows = self._rows(
            """SELECT size, COUNT(*) AS c FROM scan.entries e
               WHERE kind='file' AND size >= ?
                 AND NOT EXISTS (SELECT 1 FROM claimed c WHERE c.path=e.path)
               GROUP BY size HAVING c > 1 ORDER BY size DESC LIMIT ?""",
            (min_size, max_groups),
        )
        checked_files = 0
        checked_bytes = 0
        if len(size_rows) >= max_groups:
            self.limitations.append(f"同尺寸重复分组达到上限 {max_groups} 组，可能仍有未检查的分组。")
        truncated_group_noted = False
        for size_row in size_rows:
            if self._limit():
                return
            size = int(size_row["size"])
            if checked_files >= max_hash_files or checked_bytes + size * 2 > max_hash_bytes:
                self.limitations.append("达到重复文件哈希预算，部分大文件未做完全重复校验。")
                break
            rows = self._rows(
                """SELECT path,size,mtime,ext,inode,dev FROM scan.entries e
                   WHERE kind='file' AND size=?
                     AND NOT EXISTS (SELECT 1 FROM claimed c WHERE c.path=e.path)
                   ORDER BY path LIMIT 200""",
                (size,),
            )
            if len(rows) >= 200 and not truncated_group_noted:
                self.limitations.append("同一大小的文件超过 200 个，仅前 200 个参与完全重复校验。")
                truncated_group_noted = True
            if len(rows) < 2:
                continue
            paths = []
            for row in rows:
                guard = guard_path(row["path"], "duplicates", kind="file", packages=self.packages, config=self.config, platform=self.platform)
                if guard.get("allowed"):
                    paths.append(row["path"])
            if len(paths) < 2:
                continue
            hashes = self._hash_paths(paths, expected_size=size)
            checked_files += len(paths)
            checked_bytes += size * len(paths)
            groups: dict[str, list[str]] = {}
            for path in paths:
                digest = hashes.get(path)
                if digest:
                    groups.setdefault(digest, []).append(path)
            for digest, group_paths in groups.items():
                if len(group_paths) < 2:
                    continue
                keep = self._choose_duplicate_keep(group_paths, rows)
                delete_paths = [p for p in group_paths if p != keep] or group_paths[1:]
                file_rows = [row for row in rows if row["path"] in set(delete_paths)]
                files = [self._file_from_row(row) for row in file_rows]
                if not files:
                    continue
                self._add_candidate(
                    category="duplicates", action="delete_files", primary_path=delete_paths[0], files=files,
                    risk="CAUTION", title=f"重复文件：{human_bytes(size)} × {len(group_paths)} 份",
                    evidence=[
                        {"code": "count", "text": f"文件 {len(files)} 个"},
                        {"code": "size", "text": f"逻辑大小 {human_bytes(size * len(files))}"},
                        {"code": "hash", "text": f"SHA-256 完全相同：{digest[:16]}..."},
                        {"code": "keep", "text": f"建议保留：{display_path(keep)}"},
                    ],
                    reasons=["这些文件字节级完全相同，至少保留一份即可。",
                             "建议保留的一份是根据路径/时间挑选的，不代表它一定是最重要的版本；请复核。"],
                    confidence=0.9, keep_suggestion=f"建议保留：{display_path(keep)}",
                )

    def _choose_duplicate_keep(self, paths: list[str], rows: list[sqlite3.Row]) -> str:
        row_map = {row["path"]: row for row in rows}

        def score(path: str) -> tuple[int, float, int]:
            p = path.lower()
            value = 0
            if "/dcim/camera/" in p:
                value += 100
            elif "/dcim/" in p:
                value += 90
            elif "/pictures/" in p:
                value += 80
            elif "/movies/" in p:
                value += 70
            elif "/download/" in p:
                value += 30
            if "/backup" in p or "copy" in p or "副本" in p:
                value -= 20
            mtime = row_map.get(path)["mtime"] if path in row_map else None
            return value, -float(mtime or 0), -len(path)

        return max(paths, key=score)

    def _group_by_parent(self, rows: Iterable[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
        groups: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            groups.setdefault(row["parent"], []).append(row)
        return groups

    def gen_apks(self) -> None:
        threshold = int(self.config["thresholds"]["apk_old_days"])
        placeholders = ",".join("?" for _ in APK_EXTS)
        rows = self._rows(
            f"SELECT path,parent,name,size,mtime,inode,dev FROM scan.entries e WHERE kind='file' AND lower(ext) IN ({placeholders}) "
            f"AND mtime IS NOT NULL AND mtime >= ? AND mtime <= ? AND mtime < ? AND NOT EXISTS (SELECT 1 FROM claimed c WHERE c.path=e.path) "
            f"ORDER BY mtime ASC LIMIT 1000",
            tuple(sorted(APK_EXTS))
            + (MTIME_ANOMALY_MIN, self.now + MTIME_ANOMALY_FUTURE_SECONDS, self.now - threshold * 86400),
        )
        for parent, items in sorted(self._group_by_parent(rows).items()):
            if self._limit():
                return
            files = [self._file_from_row(row) for row in items]
            ages = [age for age in (self._age_days(row["mtime"]) for row in items if row["mtime"]) if age is not None]
            self._group_files_candidate(
                category="apk", parent=parent, files=files, risk="CAUTION",
                title=f"旧安装包：{display_path(parent)}",
                evidence=self._base_evidence(parent, files) + [
                    {"code": "age", "text": f"最早安装包约 {age_label(max(ages)) if ages else '未知'} 未修改"},
                    {"code": "ext", "text": "APK/APKS/XAPK/APKM 安装包；如需保留重装包请先确认"},
                ],
                reasons=["长期未修改的安装包通常已不再需要；但可能是离线重装/备用包。"],
                confidence=0.75, keep_suggestion="如果这些版本用于降级/离线安装，请保留。",
            )

    def gen_archives(self) -> None:
        threshold = int(self.config["thresholds"]["archive_old_days"])
        min_size = int(self.config["thresholds"]["min_archive_bytes"])
        archive_placeholders = ",".join("?" for _ in ARCHIVE_EXTS)
        apk_placeholders = ",".join("?" for _ in APK_EXTS)
        rows = self._rows(
            f"SELECT path,parent,name,size,mtime,inode,dev FROM scan.entries e WHERE kind='file' AND lower(ext) IN ({archive_placeholders}) "
            f"AND size >= ? AND mtime IS NOT NULL AND mtime >= ? AND mtime <= ? AND mtime < ? "
            f"AND lower(ext) NOT IN ({apk_placeholders}) "
            f"AND NOT EXISTS (SELECT 1 FROM claimed c WHERE c.path=e.path) ORDER BY mtime ASC LIMIT 1000",
            tuple(sorted(ARCHIVE_EXTS))
            + (min_size, MTIME_ANOMALY_MIN, self.now + MTIME_ANOMALY_FUTURE_SECONDS, self.now - threshold * 86400)
            + tuple(sorted(APK_EXTS)),
        )
        for parent, items in sorted(self._group_by_parent(rows).items()):
            if self._limit():
                return
            files = [self._file_from_row(row) for row in items]
            ages = [self._age_days(row["mtime"]) or 0 for row in items]
            self._group_files_candidate(
                category="archives", parent=parent, files=files, risk="CAUTION",
                title=f"旧压缩包/备份：{display_path(parent)}",
                evidence=self._base_evidence(parent, files) + [
                    {"code": "age", "text": f"最早文件约 {age_label(max(ages))} 未修改"},
                ],
                reasons=["旧压缩包/备份可能包含唯一副本，删除前请确认没有正在使用的备份。"],
                confidence=0.7, keep_suggestion="如有唯一备份或归档用途，请保留。",
            )

    def gen_screenshots(self) -> None:
        threshold = int(self.config["thresholds"]["screenshot_old_days"])
        rows = self._rows(
            "SELECT path,parent,name,size,mtime,ext,inode,dev FROM scan.entries e WHERE kind='file' "
            "AND mtime IS NOT NULL AND mtime >= ? AND mtime <= ? AND mtime < ? AND ("
            "lower(name) LIKE '%screenshot%' OR lower(name) LIKE '%screen_shot%' OR lower(name) LIKE '%screen-shot%' "
            "OR name LIKE '%截屏%' OR name LIKE '%屏幕截图%') "
            "AND NOT EXISTS (SELECT 1 FROM claimed c WHERE c.path=e.path) ORDER BY mtime ASC LIMIT 1000",
            (MTIME_ANOMALY_MIN, self.now + MTIME_ANOMALY_FUTURE_SECONDS, self.now - threshold * 86400),
        )
        filtered = [row for row in rows if is_screenshot(row["name"])]
        for parent, items in sorted(self._group_by_parent(filtered).items()):
            if self._limit():
                return
            files = [self._file_from_row(row) for row in items]
            self._group_files_candidate(
                category="screenshots", parent=parent, files=files, risk="CAUTION",
                title=f"旧截图：{display_path(parent)}",
                evidence=self._base_evidence(parent, files) + [
                    {"code": "age", "text": "截图超过保留期；是否包含重要信息只能由用户/AI 查看内容判断"},
                ],
                reasons=["截图可能是临时信息，也可能包含账号、票据、工作资料，必须逐项复核。"],
                confidence=0.55, keep_suggestion="先用预览/图片查看确认内容；含个人信息的截图请保留或自行处理。",
            )

    def gen_ota(self) -> None:
        rows = self._rows(
            "SELECT path,parent,name,size,mtime,inode,dev FROM scan.entries e WHERE kind='file' AND mtime IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM claimed c WHERE c.path=e.path) ORDER BY mtime ASC LIMIT 20000",
        )
        groups: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            if not is_ota_file(row["name"]):
                continue
            age = self._age_days(row["mtime"])
            if age is None or age < int(self.config["thresholds"]["archive_old_days"]):
                continue
            groups.setdefault(row["parent"], []).append(row)
        for parent, items in sorted(groups.items()):
            if self._limit():
                return
            files = [self._file_from_row(row) for row in items]
            self._group_files_candidate(
                category="ota", parent=parent, files=files, risk="CAUTION",
                title=f"旧系统更新包：{display_path(parent)}",
                evidence=self._base_evidence(parent, files) + [
                    {"code": "pattern", "text": "文件名符合 update/OTA/firmware 特征，且超过保留期"},
                ],
                reasons=["系统更新包通常可重新下载；但某些机型可能需要本地包做降级/救砖。"],
                confidence=0.7, keep_suggestion="如计划刷机/降级，请保留对应官方包。",
            )

    def gen_downloads(self) -> None:
        threshold = int(self.config["thresholds"]["download_old_days"])
        min_size = int(self.config["thresholds"]["min_download_bytes"])
        rows = self._rows(
            "SELECT path,parent,name,size,mtime,inode,dev FROM scan.entries e WHERE kind='file' "
            "AND path LIKE '/sdcard/Download/%' AND size >= ? AND mtime IS NOT NULL "
            "AND mtime >= ? AND mtime <= ? AND mtime < ? "
            "AND NOT EXISTS (SELECT 1 FROM claimed c WHERE c.path=e.path) ORDER BY mtime ASC LIMIT 2000",
            (min_size, MTIME_ANOMALY_MIN, self.now + MTIME_ANOMALY_FUTURE_SECONDS, self.now - threshold * 86400),
        )
        for parent, items in sorted(self._group_by_parent(rows).items()):
            if self._limit():
                return
            files = [self._file_from_row(row) for row in items]
            self._group_files_candidate(
                category="download_old", parent=parent, files=files, risk="CAUTION",
                title=f"旧下载文件：{display_path(parent)}",
                evidence=self._base_evidence(parent, files) + [
                    {"code": "age", "text": "Download 目录中超过保留期的文件"},
                ],
                reasons=["下载文件可能是用户仍需的资料；删除前请确认。"],
                confidence=0.55, keep_suggestion="不确定用途的下载请保留。",
            )

    def gen_large_old(self) -> None:
        threshold = int(self.config["thresholds"]["old_file_days"])
        min_size = int(self.config["thresholds"]["large_file_bytes"])
        excluded = tuple(sorted(MEDIA_EXTS | ARCHIVE_EXTS))
        placeholders = ",".join("?" for _ in excluded)
        rows = self._rows(
            f"SELECT path,parent,name,size,mtime,ext,inode,dev FROM scan.entries e WHERE kind='file' AND size >= ? "
            f"AND mtime IS NOT NULL AND mtime >= ? AND mtime <= ? AND mtime < ? AND lower(ext) NOT IN ({placeholders}) "
            f"AND NOT EXISTS (SELECT 1 FROM claimed c WHERE c.path=e.path) ORDER BY size DESC LIMIT ?",
            (min_size, MTIME_ANOMALY_MIN, self.now + MTIME_ANOMALY_FUTURE_SECONDS, self.now - threshold * 86400)
            + excluded + (int(self.config["scan"]["max_large_files"]),),
        )
        for row in rows:
            if self._limit():
                return
            age = self._age_days(row["mtime"])
            files = [self._file_from_row(row)]
            self._add_candidate(
                category="large_old", action="delete_files", primary_path=row["path"], files=files,
                risk="CAUTION", title=f"大文件且长期未修改：{display_path(row['path'])}",
                evidence=self._base_evidence(row["parent"], files) + [
                    {"code": "age", "text": f"约 {age_label(age)} 未修改"},
                    {"code": "size", "text": f"{human_bytes(row['size'])}"},
                ],
                reasons=["体积大且长期未修改，可能是遗忘的下载/备份/镜像；也可能是仍需的资料。"],
                confidence=0.55, keep_suggestion="请先确认文件用途或打开预览，再决定。",
            )

    def gen_media_old(self) -> None:
        threshold = int(self.config["thresholds"]["media_old_days"])
        min_size = int(self.config["thresholds"]["min_media_bytes"])
        placeholders = ",".join("?" for _ in MEDIA_EXTS)
        rows = self._rows(
            f"SELECT path,parent,name,size,mtime,ext,inode,dev FROM scan.entries e WHERE kind='file' "
            f"AND lower(ext) IN ({placeholders}) AND size >= ? AND mtime IS NOT NULL "
            f"AND mtime >= ? AND mtime <= ? AND mtime < ? "
            f"AND NOT EXISTS (SELECT 1 FROM claimed c WHERE c.path=e.path) ORDER BY size DESC LIMIT 200",
            tuple(sorted(MEDIA_EXTS))
            + (min_size, MTIME_ANOMALY_MIN, self.now + MTIME_ANOMALY_FUTURE_SECONDS, self.now - threshold * 86400),
        )
        for parent, items in sorted(self._group_by_parent(rows).items()):
            if self._limit():
                return
            files = [self._file_from_row(row) for row in items]
            self._group_files_candidate(
                category="media_old", parent=parent, files=files, risk="CAUTION",
                title=f"旧照片/视频：{display_path(parent)}",
                evidence=self._base_evidence(parent, files) + [
                    {"code": "media", "text": "个人媒体文件，删除不可恢复；默认只列出，不自动全选"},
                ],
                reasons=["照片/视频属于个人数据，是否保留必须由用户确认。"],
                confidence=0.4, keep_suggestion="强烈建议先备份；不确定用途的文件请保留。",
            )

    def gen_empty_dirs(self) -> None:
        threshold = int(self.config["thresholds"]["empty_dir_old_days"])
        rows = self._rows(
            """SELECT d.path,d.parent,d.name,d.kind,d.size,d.mtime,d.inode,d.dev FROM scan.entries d
               LEFT JOIN scan.entries c ON c.parent=d.path
               WHERE d.kind='dir' AND c.id IS NULL AND d.path LIKE '/sdcard/%'
                 AND d.mtime IS NOT NULL AND d.mtime >= ? AND d.mtime <= ? AND d.mtime < ?
               ORDER BY d.path LIMIT 400""",
            (MTIME_ANOMALY_MIN, self.now + MTIME_ANOMALY_FUTURE_SECONDS, self.now - threshold * 86400),
        )
        groups: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            guard = guard_path(row["path"], "empty_dirs", kind="dir", packages=self.packages, config=self.config, platform=self.platform)
            if not guard.get("allowed"):
                continue
            groups.setdefault(row["parent"], []).append(row)
        for parent, items in sorted(groups.items()):
            if self._limit():
                return
            files = [self._file_from_row(row, role="rmdir") for row in items]
            self._group_files_candidate(
                category="empty_dirs", action="rmdir", parent=parent, files=files, risk="CAUTION",
                title=f"空目录：{display_path(parent)}",
                evidence=[
                    {"code": "empty", "text": f"{len(items)} 个空目录，超过 {threshold} 天未修改"},
                    {"code": "rmdir", "text": "只使用 rmdir；目录一旦非空就会失败，不会误删内容"},
                ],
                reasons=["空目录可能仍被应用用作占位路径；默认只建议清理长期无变化的空目录。"],
                confidence=0.7, keep_suggestion="如应用依赖固定目录结构，请保留。",
            )

    def gen_protected(self) -> None:
        rows = self._rows(
            "SELECT path,size,name,ext FROM scan.entries WHERE kind='file' AND ("
            "lower(ext) IN ('.db','.sqlite','.sqlite3','.realm','.keystore','.jks','.p12','.key') "
            "OR lower(name) LIKE '%.db' OR lower(name) LIKE '%.sqlite') ORDER BY size DESC LIMIT ?",
            (int(self.config["report"]["top_protected"]),),
        )
        for row in rows:
            self.db.execute(
                "INSERT OR IGNORE INTO protected_items(path,reason,size,risk) VALUES (?,?,?,?)",
                (row["path"], "数据库/密钥/账号数据，默认禁止删除", int(row["size"] or 0), "DANGEROUS"),
            )
        rows = self._rows(
            "SELECT path FROM scan.entries WHERE kind='dir' AND parent='/sdcard/Android/obb' ORDER BY path LIMIT 30"
        )
        for row in rows:
            package = posixpath.basename(row["path"])
            if self.packages.is_installed(package):
                total = self._one(
                    "SELECT COALESCE(SUM(size),0) AS s FROM scan.entries WHERE path LIKE ? ESCAPE '\\' AND kind='file'",
                    (self._like_prefix(row["path"]),),
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO protected_items(path,reason,size,risk) VALUES (?,?,?,?)",
                    (row["path"], f"已安装应用 {package} 的 OBB 数据，默认保护", int(total["s"] if total else 0), "DANGEROUS"),
                )

    # ---------------------------------------------------------------- outputs
    def _analysis_hash(self) -> str:
        rows = self._rows("SELECT id,category,risk,action,total_bytes,file_count,selectable FROM candidates ORDER BY id")
        return sha256_text(self._analysis_id + canonical_json([dict(row) for row in rows]))

    def _summary(self) -> dict[str, Any]:
        categories: dict[str, dict[str, Any]] = {}
        for row in self._rows(
            "SELECT category,risk,COUNT(*) AS groups,COALESCE(SUM(file_count),0) AS files,"
            "COALESCE(SUM(total_bytes),0) AS bytes FROM candidates GROUP BY category,risk"
        ):
            entry = categories.setdefault(
                row["category"],
                {"category": row["category"], "title": category_title(row["category"]),
                 "groups": 0, "files": 0, "bytes": 0, "risk": "SAFE", "risk_counts": {}},
            )
            entry["groups"] += int(row["groups"])
            entry["files"] += int(row["files"])
            entry["bytes"] += int(row["bytes"])
            entry["risk_counts"][row["risk"]] = int(row["groups"])
            entry["risk"] = _max_risk(entry["risk"], row["risk"])
        risk_totals = {}
        for row in self._rows(
            "SELECT risk,COUNT(*) AS groups,COALESCE(SUM(file_count),0) AS files,COALESCE(SUM(total_bytes),0) AS bytes FROM candidates GROUP BY risk"
        ):
            risk_totals[row["risk"]] = {"groups": int(row["groups"]), "files": int(row["files"]), "bytes": int(row["bytes"])}
        total_row = self._one("SELECT COUNT(*) AS c, COALESCE(SUM(file_count),0) AS f, COALESCE(SUM(total_bytes),0) AS b FROM candidates")
        return {
            "analysis_id": self._analysis_id,
            "generated_at": now_iso(),
            "device": self.scan_meta.get("device") or {},
            "device_identity": self.scan_meta.get("device_identity") or {},
            "android": self.scan_meta.get("android", ""),
            "stats": self.scan_meta.get("stats") or {},
            "candidate_groups": int(total_row["c"] if total_row else 0),
            "candidate_files": int(total_row["f"] if total_row else 0),
            "candidate_bytes": int(total_row["b"] if total_row else 0),
            "hash_mode": self.hash_mode,
            "risk_totals": risk_totals,
            "categories": sorted(categories.values(), key=lambda c: (-c["bytes"], c["category"])),
            "limitations": self.limitations,
            "review_required": len(self.review_items),
            "protected_items": [dict(row) for row in self._rows("SELECT * FROM protected_items ORDER BY size DESC")],
            "truncated": bool(self.scan_meta.get("truncated")),
        }

    def _items(self) -> list[dict[str, Any]]:
        items = []
        for row in self._rows("SELECT * FROM candidates ORDER BY category, risk, total_bytes DESC, id"):
            reasons = json.loads(row["reasons_json"] or "[]")
            items.append(
                {
                    "id": row["id"], "category": row["category"],
                    "category_title": category_title(row["category"]), "title": row["title"],
                    "risk": row["risk"], "action": row["action"], "primary_path": row["primary_path"],
                    "sample_paths": json.loads(row["sample_paths_json"] or "[]"),
                    "total_bytes": row["total_bytes"], "human_size": human_bytes(row["total_bytes"]),
                    "file_count": row["file_count"], "dir_count": row["dir_count"],
                    "files_digest": row["files_digest"],
                    "confidence": row["confidence"], "app_pkg": row["app_pkg"], "app_kind": row["app_kind"],
                    "evidence": json.loads(row["evidence_json"] or "[]"),
                    "reasons": reasons,
                    "explain": {
                        "what": CATEGORY_META.get(row["category"], {}).get("description", ""),
                        "why": reasons[0] if reasons else "规则命中该分类，详见证据。",
                        "impact": category_impact(row["category"], row["action"]),
                    },
                    "keep_suggestion": row["keep_suggestion"],
                    "ai_review_required": bool(row["ai_review_required"]),
                    "selectable": bool(row["selectable"]), "notes": row["notes"],
                }
            )
        return items

    def run(self) -> dict[str, Any]:
        if self.platform == "harmonyos_next":
            generators = (
                self.gen_temp_logs, self.gen_duplicates, self.gen_apks, self.gen_archives,
                self.gen_large_old, self.gen_empty_dirs, self.gen_protected,
            )
        else:
            generators = (
                self.gen_residue, self.gen_thumbnails, self.gen_caches, self.gen_temp_logs,
                self.gen_trash, self.gen_duplicates, self.gen_apks, self.gen_archives,
                self.gen_screenshots, self.gen_ota, self.gen_downloads, self.gen_large_old,
                self.gen_media_old, self.gen_empty_dirs, self.gen_protected,
            )
        for generator in generators:
            try:
                generator()
            except Exception as exc:  # noqa: BLE001
                self.limitations.append(f"分析规则 {getattr(generator, '__name__', '?')} 失败：{exc}")
        if self.limit_reached:
            self.limitations.append(f"候选组达到上限 {self.max_candidates}，部分内容未列出。")
        summary = self._summary()
        summary["media_index"] = self.media_index.summary()
        summary["app_storage"] = self.app_storage.summary()
        summary["storage"] = build_storage_accounting(self.scan_meta, self.config)
        self.media_index.attach_storage(summary["storage"])
        self.app_storage.attach_storage(summary["storage"])
        summary["coverage"] = self.scan_meta.get("coverage") or {}
        summary["space_story"] = build_space_story(
            self.scan_meta, summary["app_storage"], summary["media_index"], self.config
        )
        summary["directory_changes"] = self.directory_diff
        items = self._items()
        review_template = {
            "schema": 1,
            "analysis_id": self._analysis_id,
            "instructions": (
                "仅对 decisions 中每组给出 verdict（trash/keep/uncertain）。"
                "verdict=trash 必须填写 reason，且不得降低风险等级。"
                "DANGEROUS 永远不可选；不确定请填 uncertain 或 keep。"
            ),
            "decisions": [
                {"id": item["id"], "title": item["title"], "risk": item["risk"],
                 "verdict": "", "reason": "", "evidence_checked": []}
                for item in self.review_items
            ],
        }
        analysis = {
            "schema": 1,
            "analysis_id": self._analysis_id,
            "generated_at": now_iso(),
            "scan_meta_path": str(self.state.file("scan", "snapshot-meta.json")),
            "analysis_db_path": str(self.db_path),
            "analysis_hash": self._analysis_hash(),
            "device_identity": self.scan_meta.get("device_identity") or {},
            "platform": self.platform,
            "hash_mode": self.hash_mode,
            "summary": summary,
            "storage": summary["storage"],
            "coverage": summary["coverage"],
            "media_index": summary["media_index"],
            "app_storage": summary["app_storage"],
            "space_story": summary["space_story"],
            "directory_changes": summary["directory_changes"],
            "items": items,
            "limitations": self.limitations,
            "review_items": self.review_items,
            "review_template_path": str(self.out_dir / "review-template.json"),
            "next_steps": [
                "AI 阅读 analysis-summary.json，必要时用 inspect 查看具体文件。",
                "填写 review-template.json 并保存为 review.json。",
                "运行 plan 生成计划，再由用户选择与确认。",
            ],
        }
        atomic_write_json(self.out_dir / "analysis.json", analysis)
        atomic_write_json(self.out_dir / "analysis-summary.json", {
            "schema": 1,
            "analysis_id": self._analysis_id,
            "generated_at": analysis["generated_at"],
            "analysis_hash": analysis["analysis_hash"],
            "summary": summary,
            "review_items": self.review_items,
            "items": [
                {key: item[key] for key in (
                    "id", "category", "title", "risk", "total_bytes", "human_size",
                    "file_count", "sample_paths", "ai_review_required", "selectable",
                    "evidence", "explain", "keep_suggestion",
                )}
                for item in items
            ],
            "limitations": self.limitations,
            "next_steps": analysis["next_steps"],
        })
        atomic_write_json(self.out_dir / "review-template.json", review_template)
        (self.out_dir / "report-before.md").write_text(analysis_markdown(analysis, self.config), encoding="utf-8")
        html_path = write_analysis_html(analysis, self.out_dir / "report-before.html", self.config)
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('analysis_hash',?)", (analysis["analysis_hash"],))
        self.db.commit()
        self.db.close()
        self.state.set_file("analysis", self.db_path)
        self.state.set_file("analysis_json", self.out_dir / "analysis.json")
        self.state.set_file("analysis_summary", self.out_dir / "analysis-summary.json")
        self.state.set_file("report_before", self.out_dir / "report-before.md")
        self.state.set_file("report_html", html_path)
        return analysis


def _max_risk(a: str, b: str) -> str:
    return a if _RISK_ORDER.get(a, 99) >= _RISK_ORDER.get(b, 99) else b
