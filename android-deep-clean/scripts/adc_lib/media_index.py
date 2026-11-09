"""Host-side MediaStore ingestion and cross-validation.

Reads only the auxiliary files written by ``scan`` (``scan/aux``), joins them
against the filesystem snapshot, and produces evidence.  It never creates
candidates, never changes risk, and never touches the device.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import CandidateFile
from .rules import MEDIA_EXTS
from .state import State
from .util import human_bytes, is_under, read_json

MEDIA_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_items (
    item_key TEXT PRIMARY KEY,
    path TEXT NOT NULL DEFAULT '',
    path_lower TEXT NOT NULL DEFAULT '',
    collections TEXT NOT NULL DEFAULT '[]',
    display_name TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    mtime INTEGER,
    date_taken INTEGER,
    mime_type TEXT NOT NULL DEFAULT '',
    width INTEGER,
    height INTEGER,
    duration_ms INTEGER,
    owner_package TEXT NOT NULL DEFAULT '',
    volume TEXT NOT NULL DEFAULT '',
    relative_path TEXT NOT NULL DEFAULT '',
    pending INTEGER NOT NULL DEFAULT 0,
    trashed INTEGER NOT NULL DEFAULT 0,
    scan_size INTEGER,
    in_scan INTEGER NOT NULL DEFAULT 0,
    cross_status TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_media_path ON media_items(path);
CREATE INDEX IF NOT EXISTS idx_media_path_lower ON media_items(path_lower);
CREATE INDEX IF NOT EXISTS idx_media_status ON media_items(cross_status);
CREATE TABLE IF NOT EXISTS source_status (
    name TEXT PRIMARY KEY,
    available INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT '',
    items INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT ''
);
"""

MEDIA_EVIDENCE_CATEGORIES = {"media_old", "screenshots", "duplicates", "trash"}
COLLECTION_LABELS = {"images": "图片", "video": "视频", "audio": "音频", "downloads": "下载"}
SAMPLE_LIMIT = 8


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _date_label(value: Any) -> str:
    number = _int(value, 0)
    if not number:
        return ""
    try:
        return datetime.fromtimestamp(number).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def _duration_label(milliseconds: Any) -> str:
    number = _int(milliseconds, 0)
    if not number:
        return ""
    seconds = number // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


def _in_shared_media_scope(path: str) -> bool:
    if not is_under(path, "/sdcard"):
        return False
    if is_under(path, "/sdcard/Android"):
        return False
    parts = path.split("/")[2:]
    return not any(part.startswith(".") for part in parts if part)


class MediaIndex:
    def __init__(
        self,
        state: State,
        scan_conn: sqlite3.Connection,
        scan_meta: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        self.state = state
        self.scan_conn = scan_conn
        self.scan_meta = scan_meta
        self.config = config
        self.aux_dir: Path = state.dir("scan") / "aux"
        self.manifest: dict[str, Any] = {}
        self.records: list[dict[str, Any]] = []
        self.by_path: dict[str, dict[str, Any]] = {}
        self.status = "not_collected"
        self.reason = "本轮扫描未采集 MediaStore 索引"
        self.available = False
        self.limitations: list[str] = []
        self.collections: list[dict[str, Any]] = []
        self.crosscheck: dict[str, Any] = {}
        self._summary: dict[str, Any] | None = None

    # ---------------------------------------------------------------- loading
    def load(self) -> "MediaIndex":
        manifest_path = self.aux_dir / "sources.json"
        manifest = read_json(manifest_path, default=None) if manifest_path.exists() else None
        if not isinstance(manifest, dict):
            fallback = (self.scan_meta.get("aux") or {}).get("media_store")
            manifest = fallback if isinstance(fallback, dict) else {}
        self.manifest = manifest
        if not manifest:
            return self
        self.status = str(manifest.get("status") or "unavailable")
        self.reason = str(manifest.get("reason") or "")
        self.available = bool(manifest.get("available")) or self.status in {"collected", "partial"}
        self.collections = list(manifest.get("collections") or [])
        self.limitations = [str(item) for item in (manifest.get("limitations") or [])]
        items_path = self.aux_dir / "media-index.jsonl"
        malformed = 0
        if self.status in {"collected", "partial"}:
            if items_path.exists():
                with open(items_path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            malformed += 1
                            continue
                        if isinstance(record, dict):
                            self.records.append(record)
            else:
                self.status = "partial"
                self.available = True
                self.reason = "媒体索引清单存在但条目文件缺失"
                self.limitations.append(self.reason)
        if malformed:
            self.limitations.append(f"{malformed} 行媒体索引无法解析，已跳过。")
            if not self.records:
                self.status = "partial"
        for record in self.records:
            path = str(record.get("path") or "")
            if path:
                self.by_path.setdefault(path, record)
        return self

    # ---------------------------------------------------------------- ingest
    def ingest(self, db: sqlite3.Connection) -> None:
        db.executescript(MEDIA_SCHEMA)
        rows = []
        for record in self.records:
            collections = _json_list(record.get("collections")) or [str(record.get("collection") or "")]
            rows.append((
                str(record.get("item_key") or record.get("path") or ""),
                str(record.get("path") or ""),
                str(record.get("path") or "").lower(),
                json.dumps(collections, ensure_ascii=False),
                str(record.get("display_name") or ""),
                max(0, _int(record.get("size"), 0)),
                _int(record.get("mtime"), 0) or None,
                _int(record.get("date_taken"), 0) or None,
                str(record.get("mime_type") or ""),
                _int(record.get("width"), 0) or None,
                _int(record.get("height"), 0) or None,
                _int(record.get("duration_ms"), 0) or None,
                str(record.get("owner_package") or ""),
                str(record.get("volume") or ""),
                str(record.get("relative_path") or ""),
                1 if record.get("pending") else 0,
                1 if record.get("trashed") else 0,
            ))
        if rows:
            db.executemany(
                """INSERT OR REPLACE INTO media_items
                (item_key,path,path_lower,collections,display_name,size,mtime,date_taken,mime_type,width,height,
                 duration_ms,owner_package,volume,relative_path,pending,trashed)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        try:
            # Shared storage is usually case-insensitive (FUSE/FAT); MediaStore
            # can return a different case than `find`.  Compare lowercased paths
            # so a case difference is not misreported as an index-only file.
            db.execute(
                "CREATE TEMP TABLE scan_paths_lower AS "
                "SELECT lower(path) AS lp, size FROM scan.entries WHERE kind='file'"
            )
            db.execute("CREATE INDEX idx_scan_paths_lower ON scan_paths_lower(lp)")
            db.execute(
                "UPDATE media_items SET scan_size=(SELECT size FROM scan_paths_lower "
                "WHERE lp=media_items.path_lower LIMIT 1)"
            )
            db.execute(
                """UPDATE media_items SET
                   in_scan=CASE WHEN scan_size IS NULL THEN 0 ELSE 1 END,
                   cross_status=CASE
                     WHEN path='' THEN 'path_unknown'
                     WHEN scan_size IS NULL THEN 'index_only'
                     WHEN scan_size=size THEN 'matched'
                     ELSE 'size_mismatch'
                   END"""
            )
            crosscheck = self._compute_crosscheck(db)
        except sqlite3.Error as exc:
            self.limitations.append(f"无法与文件扫描交叉核对：{exc}")
            crosscheck = self._empty_crosscheck()
        self.crosscheck = crosscheck
        self._write_source_status(db)
        self._summary = self._build_summary()

    def _write_source_status(self, db: sqlite3.Connection) -> None:
        coverage = self.scan_meta.get("coverage") or {}
        stats = self.scan_meta.get("stats") or {}
        media = self.manifest
        try:
            db.execute(
                "INSERT OR REPLACE INTO source_status(name,available,status,items,bytes,reason) VALUES (?,?,?,?,?,?)",
                (
                    "file_scan", 1, str(coverage.get("status") or "unknown"),
                    _int(stats.get("files"), 0),
                    _int(coverage.get("unique_bytes") or stats.get("bytes"), 0),
                    "",
                ),
            )
            db.execute(
                "INSERT OR REPLACE INTO source_status(name,available,status,items,bytes,reason) VALUES (?,?,?,?,?,?)",
                (
                    "media_store", 1 if self.available else 0, self.status,
                    _int(media.get("items"), 0), _int(media.get("bytes"), 0), self.reason,
                ),
            )
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------- cross-check
    def _roots(self) -> list[str]:
        coverage = self.scan_meta.get("coverage") or {}
        roots = coverage.get("roots_scanned") or self.scan_meta.get("roots_scanned") or []
        return [str(root) for root in roots if root] or ["/sdcard"]

    def _in_scan_scope(self, path: str) -> bool:
        return any(is_under(path, root) for root in self._roots())

    @staticmethod
    def _empty_crosscheck() -> dict[str, Any]:
        return {
            "found_in_scan": 0,
            "matched": 0,
            "size_mismatch": 0,
            "case_mismatch": 0,
            "index_only": 0,
            "index_only_in_scope": 0,
            "index_only_outside_scope": 0,
            "path_unknown": 0,
            "found_bytes": 0,
            "index_in_scope": 0,
            "match_ratio": None,
            "index_scope_ratio": None,
            "scan_only": 0,
            "scan_only_bytes": 0,
            "samples": {"index_only": [], "index_only_outside": [], "size_mismatch": [], "scan_only": []},
        }

    def _compute_crosscheck(self, db: sqlite3.Connection) -> dict[str, Any]:
        counts: dict[str, tuple[int, int]] = {}
        for row in db.execute("SELECT cross_status,COUNT(*),COALESCE(SUM(size),0) FROM media_items GROUP BY cross_status"):
            counts[str(row[0])] = (int(row[1]), int(row[2]))
        matched = counts.get("matched", (0, 0))
        mismatch = counts.get("size_mismatch", (0, 0))
        index_only = counts.get("index_only", (0, 0))
        unknown = counts.get("path_unknown", (0, 0))
        found = matched[0] + mismatch[0]
        found_bytes = matched[1] + mismatch[1]
        totals = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(CASE WHEN path='' THEN 1 ELSE 0 END),0) FROM media_items"
        ).fetchone()
        total_items = int(totals[0]) if totals else 0
        path_known = total_items - int(totals[1]) if totals else 0
        try:
            case_mismatch = int(db.execute(
                "SELECT COUNT(*) FROM media_items m WHERE m.in_scan=1 "
                "AND NOT EXISTS (SELECT 1 FROM scan.entries e WHERE e.path=m.path)"
            ).fetchone()[0])
        except sqlite3.Error:
            case_mismatch = 0
        samples: dict[str, list[dict[str, Any]]] = {
            "index_only": [], "index_only_outside": [], "size_mismatch": [], "scan_only": [],
        }
        in_scope = 0
        outside = 0
        for row in db.execute(
            "SELECT path,size,collections FROM media_items WHERE cross_status='index_only' ORDER BY size DESC"
        ):
            path = str(row[0] or "")
            if not path:
                continue
            collections = _json_list(row[2])
            if self._in_scan_scope(path):
                in_scope += 1
                if len(samples["index_only"]) < SAMPLE_LIMIT:
                    samples["index_only"].append(self._sample(
                        path, _int(row[1], 0), collections,
                        "文件扫描快照没有该路径：可能位于不可读区域、索引延迟或文件已移动；不能认定文件已删除。",
                    ))
            else:
                outside += 1
                if len(samples["index_only_outside"]) < SAMPLE_LIMIT:
                    samples["index_only_outside"].append(self._sample(
                        path, _int(row[1], 0), collections,
                        "该路径不在本轮扫描根目录内，无法核对。",
                    ))
        for row in db.execute(
            "SELECT path,size,scan_size,collections FROM media_items "
            "WHERE cross_status='size_mismatch' ORDER BY size DESC LIMIT 200"
        ):
            if len(samples["size_mismatch"]) >= SAMPLE_LIMIT:
                break
            index_size = _int(row[1], 0)
            scan_size = _int(row[2], 0)
            samples["size_mismatch"].append(self._sample(
                str(row[0] or ""), index_size, _json_list(row[3]),
                f"媒体索引 {human_bytes(index_size)}，文件扫描 {human_bytes(scan_size)}；索引可能过期，以文件系统为准。",
            ))
        scan_only_count, scan_only_bytes = self._scan_only(db, samples)
        # The match ratio must not be diluted by index rows that sit outside the
        # scanned roots: a Download-only scan would otherwise look broken.
        index_in_scope = found + in_scope
        match_ratio = round(found / index_in_scope, 4) if index_in_scope else None
        scope_ratio = round(index_in_scope / path_known, 4) if path_known else None
        return {
            "found_in_scan": found,
            "matched": matched[0],
            "size_mismatch": mismatch[0],
            "case_mismatch": case_mismatch,
            "index_only": index_only[0],
            "index_only_in_scope": in_scope,
            "index_only_outside_scope": outside,
            "path_unknown": unknown[0],
            "found_bytes": found_bytes,
            "index_in_scope": index_in_scope,
            "match_ratio": match_ratio,
            "index_scope_ratio": scope_ratio,
            "scan_only": scan_only_count,
            "scan_only_bytes": scan_only_bytes,
            "samples": samples,
        }

    def _scan_only(self, db: sqlite3.Connection, samples: dict[str, list[dict[str, Any]]]) -> tuple[int, int]:
        placeholders = ",".join("?" for _ in MEDIA_EXTS)
        base = (
            "FROM scan.entries e LEFT JOIN media_items m ON m.path_lower=lower(e.path) "
            "WHERE e.kind='file' AND m.item_key IS NULL "
            "AND e.path LIKE '/sdcard/%' AND e.path NOT LIKE '/sdcard/Android/%' "
            "AND e.path NOT LIKE '/sdcard/%/.%' "
            f"AND lower(e.ext) IN ({placeholders})"
        )
        params = tuple(sorted(MEDIA_EXTS))
        try:
            row = db.execute(f"SELECT COUNT(*),COALESCE(SUM(e.size),0) {base}", params).fetchone()
            count, total = int(row[0]), int(row[1])
            for item in db.execute(
                f"SELECT e.path,e.size {base} ORDER BY e.size DESC LIMIT {SAMPLE_LIMIT}", params
            ):
                path = str(item[0])
                if _in_shared_media_scope(path):
                    samples["scan_only"].append(self._sample(
                        path, _int(item[1], 0), [],
                        "共享目录中存在但媒体索引没有记录：可能是隐藏目录、未触发媒体扫描或应用私有媒体，不代表异常。",
                    ))
            return count, total
        except sqlite3.Error:
            return 0, 0

    @staticmethod
    def _sample(path: str, size: int, collections: list[str], reason: str) -> dict[str, Any]:
        return {
            "path": path,
            "size": size,
            "human_size": human_bytes(size),
            "collections": collections,
            "reason": reason,
        }

    # ---------------------------------------------------------------- summary
    def summary(self) -> dict[str, Any]:
        if self._summary is not None:
            return self._summary
        self._summary = self._build_summary()
        return self._summary

    def _build_summary(self) -> dict[str, Any]:
        items = len(self.records)
        total_bytes = sum(max(0, _int(record.get("size"), 0)) for record in self.records)
        path_known = sum(1 for record in self.records if record.get("path"))
        by_collection: Counter[str] = Counter()
        bytes_by_collection: Counter[str] = Counter()
        for record in self.records:
            collections = _json_list(record.get("collections")) or [str(record.get("collection") or "")]
            for name in collections:
                by_collection[name] += 1
                bytes_by_collection[name] += max(0, _int(record.get("size"), 0))
        limitations = list(self.limitations)
        if path_known < items:
            limitations.append(f"{items - path_known} 条媒体索引没有可用路径，未参与文件扫描核对。")
        if _int((self.crosscheck or {}).get("case_mismatch"), 0):
            limitations.append(
                f"{_int(self.crosscheck.get('case_mismatch'), 0)} 个索引路径与文件扫描仅大小写不同，"
                "已按共享存储通常不区分大小写核对。"
            )
        if not self.available and self.status not in {"disabled", "unsupported"} and self.reason:
            limitations.append(self.reason)
        base = {
            "schema": 1,
            "status": self.status,
            "available": self.available,
            "reason": self.reason,
            "index": {
                "items": items,
                "bytes": total_bytes,
                "bytes_human": human_bytes(total_bytes),
                "path_known": path_known,
                "path_unknown": items - path_known,
                "trashed": sum(1 for record in self.records if record.get("trashed")),
                "pending": sum(1 for record in self.records if record.get("pending")),
                "by_collection": [
                    {"name": name, "label": COLLECTION_LABELS.get(name, name),
                     "items": count, "bytes": bytes_by_collection[name]}
                    for name, count in sorted(by_collection.items())
                ],
            },
            "crosscheck": self.crosscheck or self._empty_crosscheck(),
            "collections": self.collections,
            "limitations": list(dict.fromkeys(limitations)),
            "manifest_path": str(self.aux_dir / "sources.json"),
        }
        return base

    def attach_storage(self, storage: dict[str, Any]) -> None:
        media = self.summary()
        crosscheck = media.get("crosscheck") or self._empty_crosscheck()
        block = storage.setdefault("media_index", {})
        block.update({
            "status": media.get("status"),
            "available": media.get("available"),
            "reason": media.get("reason"),
            "items": (media.get("index") or {}).get("items", 0),
            "bytes": (media.get("index") or {}).get("bytes", 0),
            "bytes_human": (media.get("index") or {}).get("bytes_human", "0 B"),
            "path_known": (media.get("index") or {}).get("path_known", 0),
            "trashed": (media.get("index") or {}).get("trashed", 0),
            "pending": (media.get("index") or {}).get("pending", 0),
            "found_in_scan": crosscheck.get("found_in_scan", 0),
            "index_only": crosscheck.get("index_only", 0),
            "scan_only": crosscheck.get("scan_only", 0),
            "size_mismatch": crosscheck.get("size_mismatch", 0),
            "match_ratio": crosscheck.get("match_ratio"),
            "note": "媒体索引与文件扫描口径不同，不能相加；它是证据，不是删除授权。",
        })
        for source in storage.get("sources") or []:
            if source.get("name") == "media_store":
                source.update({
                    "available": bool(media.get("available")),
                    "status": media.get("status"),
                    "items": (media.get("index") or {}).get("items", 0),
                    "bytes": (media.get("index") or {}).get("bytes", 0),
                    "path_known": (media.get("index") or {}).get("path_known", 0),
                    "trashed": (media.get("index") or {}).get("trashed", 0),
                    "pending": (media.get("index") or {}).get("pending", 0),
                    "reason": media.get("reason") or "",
                })

    # ---------------------------------------------------------------- evidence
    def evidence(self, files: Iterable[CandidateFile], category: str) -> list[dict[str, str]]:
        if category not in MEDIA_EVIDENCE_CATEGORIES or not self.by_path:
            return []
        file_items = [item for item in files if item.kind == "file"]
        matched = [(item, self.by_path[item.path]) for item in file_items if item.path in self.by_path]
        if not matched:
            return []
        evidence: list[dict[str, str]] = []
        collection_counts: Counter[str] = Counter()
        for _item, record in matched:
            for name in _json_list(record.get("collections")) or [str(record.get("collection") or "")]:
                collection_counts[name] += 1
        parts = [f"{COLLECTION_LABELS.get(name, name)} {count} 个"
                 for name, count in sorted(collection_counts.items())]
        detail: list[str] = []
        example = next((record for _item, record in matched if record.get("mime_type")), matched[0][1])
        if example.get("mime_type"):
            detail.append(str(example["mime_type"]))
        if example.get("width") and example.get("height"):
            detail.append(f"{int(example['width'])}×{int(example['height'])}")
        duration = _duration_label(example.get("duration_ms"))
        if duration:
            detail.append(f"时长 {duration}")
        date = _date_label(example.get("date_taken"))
        if date:
            detail.append(f"拍摄 {date}")
        text = f"媒体索引：{len(matched)}/{len(file_items)} 个文件在系统媒体库中"
        if parts:
            text += f"（{'，'.join(parts)}）"
        if detail:
            text += f"；示例 {'，'.join(detail)}"
        evidence.append({"code": "media_index", "text": text})
        trashed = sum(1 for _item, record in matched if record.get("trashed"))
        pending = sum(1 for _item, record in matched if record.get("pending"))
        if trashed or pending:
            states = []
            if trashed:
                states.append(f"回收站标记 {trashed} 个（is_trashed）")
            if pending:
                states.append(f"pending {pending} 个（is_pending）")
            evidence.append({
                "code": "media_index_state",
                "text": "媒体索引状态：" + "，".join(states) + "；文件系统状态仍以扫描为准。",
            })
        mismatch = sum(1 for item, record in matched if max(0, _int(record.get("size"), 0)) != item.size)
        if mismatch:
            evidence.append({
                "code": "media_index_mismatch",
                "text": f"媒体索引与文件扫描大小不一致：{mismatch} 个；索引可能过期，以文件系统为准。",
            })
        unindexed = len(file_items) - len(matched)
        if unindexed:
            evidence.append({
                "code": "media_index_unindexed",
                "text": f"{unindexed} 个候选文件不在媒体索引中；可能是未索引或隐藏目录，不代表文件异常或可删除。",
            })
        return evidence
