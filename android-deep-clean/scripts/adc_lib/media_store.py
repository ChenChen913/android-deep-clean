"""MediaStore auxiliary source (read-only).

The collector asks Android's ``content`` command for the system media index.
It runs as the same shell identity as the rest of the scan (ADB shell UID 2000
or a Shizuku shell); it never requests root and never writes to the device.

Rows are stored on the host only.  They are evidence for cross-checking the
filesystem scan -- not a deletion source and not a replacement for scanning.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

from .platform import HARMONY_NEXT
from .rules import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS
from .state import State
from .transport import Transport, TransportError
from .util import atomic_write_json, display_path, now_iso, shell_quote

ROW_RE = re.compile(r"^Row:\s*(\d+)\s+(.*)$")
KV_SPLIT_RE = re.compile(r",\s+(?=[A-Za-z_][A-Za-z0-9_]*=)")

COLLECTION_LABELS = {
    "images": "图片",
    "video": "视频",
    "audio": "音频",
    "downloads": "下载",
}
COLLECTION_DIMS: dict[str, tuple[str, ...]] = {
    "images": ("width", "height"),
    "video": ("width", "height", "duration"),
    "audio": ("duration",),
    "downloads": (),
}
COLLECTION_MIN_SDK = {"downloads": 29}

CORE_COLUMNS = ("_id", "_display_name", "_size", "date_modified", "mime_type")
OPTIONAL_COLUMNS = ("_data", "datetaken", "relative_path", "volume_name",
                    "owner_package_name", "is_pending", "is_trashed")

_FATAL_ERROR_MARKERS = (
    "permission denial",
    "permission denied",
    "securityexception",
    "not allowed",
    "inaccessible",
    "timed out",
    "timeout",
    "超时",
    "content: not found",
    "unknown command",
    "unknown option",
    "unknown or unsupported url",
)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none"}:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return None


def _as_flag(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def _epoch_seconds(value: Any) -> int | None:
    number = _as_int(value)
    if number is None or number <= 0:
        return None
    if number > 10_000_000_000:
        number //= 1000
    return number


def _normalize_media_path(value: Any) -> str:
    raw = _text(value)
    if not raw.startswith("/"):
        return ""
    try:
        path = display_path(raw)
    except Exception:  # noqa: BLE001 - bad media row must not break analysis
        return ""
    for alias in ("/data/media/0", "/storage/emulated/0", "/storage/self/primary", "/mnt/sdcard"):
        if path == alias:
            return "/sdcard"
        if path.startswith(alias + "/"):
            return "/sdcard" + path[len(alias):]
    return path


def parse_content_rows(text: str) -> list[dict[str, Any]]:
    """Parse ``content query`` output into rows.

    The shell command prints ``Row: N key=value, key=value`` without quoting
    values.  We split only on ``, `` followed by a new ``key=`` so display
    names containing commas stay intact.  A trailing incomplete row is dropped.
    """
    rows: list[dict[str, Any]] = []
    for raw_line in (text or "").splitlines():
        match = ROW_RE.match(raw_line.strip())
        if not match:
            continue
        payload = match.group(2).rstrip()
        if payload.endswith(",") or re.search(r",\s+[A-Za-z_][A-Za-z0-9_]*$", payload):
            continue
        values: dict[str, Any] = {}
        incomplete = False
        for part in KV_SPLIT_RE.split(payload):
            key, separator, value = part.partition("=")
            if not separator:
                incomplete = True
                break
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            values[key] = None if value.lower() in {"null", "none"} else value
        if incomplete:
            continue
        if values:
            rows.append(values)
    return rows


def derive_media_path(row: dict[str, Any]) -> str:
    """Best-effort absolute path for one MediaStore row, or ``""``."""
    direct = _normalize_media_path(row.get("_data"))
    if direct:
        return direct
    volume = _text(row.get("volume_name"))
    relative = _text(row.get("relative_path")).strip("/")
    name = _text(row.get("_display_name"))
    if not volume or not name:
        return ""
    if volume in {"external_primary", "external"}:
        base = "/storage/emulated/0"
    else:
        base = f"/storage/{volume}"
    candidate = posixpath.join(base, relative, name) if relative else posixpath.join(base, name)
    return _normalize_media_path(candidate)


def _fallback_key(record: dict[str, Any]) -> str:
    return "|".join((
        "volume:" + _text(record.get("volume")),
        "relative:" + _text(record.get("relative_path")),
        "name:" + _text(record.get("display_name")),
        "size:" + str(int(record.get("size") or 0)),
        "collection:" + _text(record.get("collection")),
    ))


def normalize_record(collection: str, row: dict[str, Any]) -> dict[str, Any]:
    """Convert one raw content row to the stable JSONL shape."""
    path = derive_media_path(row)
    modified = _epoch_seconds(row.get("date_modified"))
    taken = _epoch_seconds(row.get("datetaken") or row.get("date_taken"))
    record = {
        "collection": collection,
        "collections": [collection],
        "media_id": _as_int(row.get("_id")),
        "path": path,
        "display_name": _text(row.get("_display_name")),
        "size": max(0, int(_as_int(row.get("_size")) or 0)),
        "mtime": modified or taken,
        "date_taken": taken,
        "mime_type": _text(row.get("mime_type")),
        "width": _as_int(row.get("width")),
        "height": _as_int(row.get("height")),
        "duration_ms": _as_int(row.get("duration")),
        "owner_package": _text(row.get("owner_package_name")),
        "volume": _text(row.get("volume_name")),
        "relative_path": _text(row.get("relative_path")),
        "pending": _as_flag(row.get("is_pending")),
        "trashed": _as_flag(row.get("is_trashed")),
        "media_type": _as_int(row.get("media_type")),
    }
    record["item_key"] = path or _fallback_key(record)
    return record


def _merge_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get("item_key") or record.get("path") or _fallback_key(record))
        existing = merged.get(key)
        if existing is None:
            record["item_key"] = key
            merged[key] = dict(record)
            continue
        existing["collections"] = sorted(set(existing.get("collections") or []) | set(record.get("collections") or []))
        for field in ("width", "height", "duration_ms", "mime_type", "owner_package",
                      "date_taken", "mtime", "volume", "relative_path", "media_id"):
            if existing.get(field) in (None, "", 0) and record.get(field) not in (None, "", 0):
                existing[field] = record[field]
        existing["pending"] = bool(existing.get("pending")) or bool(record.get("pending"))
        existing["trashed"] = bool(existing.get("trashed")) or bool(record.get("trashed"))
    return sorted(merged.values(), key=lambda item: (str(item.get("path") or ""), str(item.get("item_key") or "")))


class MediaStoreCollector:
    def __init__(
        self,
        transport: Transport,
        config: dict[str, Any],
        state: State,
        doctor: dict[str, Any],
    ) -> None:
        self.transport = transport
        self.config = config
        self.state = state
        self.doctor = doctor
        self.aux_dir = state.dir("scan") / "aux"
        self.settings = config.get("media_store") or {}
        self.platform = str((doctor.get("capabilities") or {}).get("platform") or "android")
        try:
            self.sdk = int((doctor.get("device") or {}).get("sdk") or 0)
        except (TypeError, ValueError):
            self.sdk = 0
        self.timeout = max(5, int(self.settings.get("timeout_seconds") or 60))
        self.max_items = max(0, int(self.settings.get("max_items") or 200000))
        self.max_output_bytes = max(0, int(self.settings.get("max_output_bytes") or 100_000_000))

    # -- lifecycle --------------------------------------------------------
    def collect(self, *, enabled: bool = True) -> dict[str, Any]:
        self._reset()
        started = time.monotonic()
        if not enabled or not bool(self.settings.get("enabled", True)):
            return self._finish(
                "disabled",
                "本轮扫描已关闭 MediaStore 采集（--no-media 或配置关闭）",
                [], [], started,
            )
        if self.platform == HARMONY_NEXT:
            return self._finish(
                "unsupported",
                "HarmonyOS NEXT 不使用 Android MediaStore；未执行 content query",
                [], [], started,
            )
        requested = self._collections()
        if not requested:
            return self._finish("disabled", "配置未启用任何媒体集合", [], [], started)
        raw: list[dict[str, Any]] = []
        collection_info: list[dict[str, Any]] = []
        limitations: list[str] = []
        truncated = False
        for name in requested:
            minimum = COLLECTION_MIN_SDK.get(name, 0)
            if minimum and self.sdk and self.sdk < minimum:
                error = f"需要 API {minimum}+，当前 API {self.sdk}"
                collection_info.append({
                    "name": name, "label": COLLECTION_LABELS.get(name, name),
                    "available": False, "status": "skipped", "rows": 0, "bytes": 0,
                    "projection": [], "attempts": 0,
                    "error": error,
                    "elapsed_seconds": 0.0,
                })
                limitations.append(f"{COLLECTION_LABELS.get(name, name)}媒体索引未采集：{error}")
                continue
            result = self._query_collection(name)
            info = {key: value for key, value in result.items() if key != "records"}
            collection_info.append(info)
            raw.extend(result.get("records") or [])
            if result.get("status") == "failed":
                error = str(result.get("error") or "未知错误")
                limitations.append(f"{COLLECTION_LABELS.get(name, name)}媒体索引不可用：{error}")
            if len(raw) > self.max_items:
                raw = raw[: self.max_items]
                truncated = True
        scan_counts = self._scan_counts()
        threshold = max(1, int(self.settings.get("zero_crosscheck_min_files") or 10))
        if not scan_counts:
            limitations.append("无法读取文件扫描快照，未做 MediaStore 零记录交叉核对。")
        zero_mismatch: list[str] = []
        empty_collections: list[str] = []
        for info in collection_info:
            name = str(info.get("name") or "")
            scan_count = int(scan_counts.get(name, 0))
            info["scan_files"] = scan_count
            if info.get("status") != "ok" or int(info.get("rows") or 0) != 0:
                continue
            if scan_count >= threshold:
                info["status"] = "zero_mismatch"
                info["crosscheck"] = "scan_has_files_but_index_empty"
                info["error"] = (
                    f"查询成功但返回 0 条；文件扫描发现 {scan_count} 个同类文件，"
                    "可能是权限、Provider 或索引异常"
                )
                zero_mismatch.append(name)
                limitations.append(
                    f"{info.get('label') or name}媒体索引返回 0 条，但文件扫描发现 {scan_count} 个同类文件；"
                    "不能据此认为媒体库为空。"
                )
            else:
                info["status"] = "empty"
                info["crosscheck"] = "no_scan_files"
                empty_collections.append(name)
        crosscheck = {
            "threshold": threshold,
            "scan_files": scan_counts,
            "zero_mismatch": zero_mismatch,
            "empty": empty_collections,
            "note": "零记录不直接判失败；只有文件扫描发现同类文件达到阈值时才标记异常。",
        }
        records = _merge_records(raw)
        if truncated or any(item.get("status") == "failed" for item in collection_info):
            limitations.append("媒体索引只完成部分采集；缺失集合不会影响文件扫描结果。")
        if any(item.get("status") == "failed" for item in collection_info):
            status = "partial" if any(
                item.get("status") in {"ok", "empty", "zero_mismatch"} for item in collection_info
            ) else "unavailable"
            reason = "部分媒体集合不可用" if status == "partial" else "所有媒体集合均不可用"
        elif zero_mismatch:
            status = "partial"
            reason = "部分媒体集合查询成功但返回 0 条，与文件扫描不一致"
        elif not any(item.get("status") in {"ok", "empty"} for item in collection_info):
            status = "unavailable"
            reason = "没有可采集的媒体集合"
        elif truncated:
            status = "partial"
            reason = "媒体索引超过条数上限，已截断"
        elif all(item.get("status") in {"empty", "skipped"} for item in collection_info):
            status = "collected"
            reason = "查询成功；所有集合均为 0 条（可能是空媒体库）"
        else:
            status = "collected"
            reason = ""
        if status in {"collected", "partial"}:
            limitations.append("媒体索引与文件扫描口径不同；索引延迟、隐藏目录、多用户和部分 SD 卡都可能造成差异。")
        return self._finish(
            status, reason, records, collection_info, started,
            limitations=limitations, truncated=truncated, crosscheck=crosscheck,
        )

    def _scan_counts(self) -> dict[str, int]:
        """Count same-kind files in the filesystem snapshot for zero-row checks."""
        db_path = self.state.dir("scan") / "snapshot.db"
        if not db_path.exists():
            return {}
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return {}
        try:
            def count_exts(exts: set[str]) -> int:
                placeholders = ",".join("?" for _ in exts)
                row = conn.execute(
                    "SELECT COUNT(*) FROM entries WHERE kind='file' "
                    "AND path LIKE '/sdcard/%' AND path NOT LIKE '/sdcard/Android/%' "
                    "AND path NOT LIKE '%/.%' "
                    f"AND lower(ext) IN ({placeholders})",
                    tuple(sorted(exts)),
                ).fetchone()
                return int(row[0]) if row else 0

            downloads = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE kind='file' "
                "AND path LIKE '/sdcard/Download/%' AND path NOT LIKE '%/.%'"
            ).fetchone()
            return {
                "images": count_exts(IMAGE_EXTS),
                "video": count_exts(VIDEO_EXTS),
                "audio": count_exts(AUDIO_EXTS),
                "downloads": int(downloads[0]) if downloads else 0,
            }
        except sqlite3.Error:
            return {}
        finally:
            conn.close()

    def _collections(self) -> list[str]:
        configured = self.settings.get("collections")
        names = configured if isinstance(configured, list) and configured else list(COLLECTION_LABELS)
        return [str(name) for name in names if str(name) in COLLECTION_LABELS]

    def _reset(self) -> None:
        if self.aux_dir.exists():
            shutil.rmtree(self.aux_dir, ignore_errors=True)
        self.aux_dir.mkdir(parents=True, exist_ok=True)

    def _finish(
        self,
        status: str,
        reason: str,
        records: list[dict[str, Any]],
        collection_info: list[dict[str, Any]],
        started: float,
        *,
        limitations: list[str] | None = None,
        truncated: bool = False,
        crosscheck: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        items_path = self.aux_dir / "media-index.jsonl"
        temp_path = items_path.with_suffix(".jsonl.tmp")
        with open(temp_path, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temp_path, items_path)
        path_known = sum(1 for item in records if item.get("path"))
        manifest = {
            "schema": 1,
            "generated_at": now_iso(),
            "status": status,
            "available": status in {"collected", "partial"},
            "reason": reason,
            "platform": self.platform,
            "sdk": self.sdk,
            "transport": self.transport.name,
            "collections": collection_info,
            "crosscheck": crosscheck or {},
            "items": len(records),
            "bytes": sum(int(item.get("size") or 0) for item in records),
            "path_known": path_known,
            "path_unknown": len(records) - path_known,
            "trashed": sum(1 for item in records if item.get("trashed")),
            "pending": sum(1 for item in records if item.get("pending")),
            "truncated": bool(truncated),
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "limitations": list(limitations or []),
            "items_path": str(items_path),
            "manifest_path": str(self.aux_dir / "sources.json"),
        }
        atomic_write_json(self.aux_dir / "sources.json", manifest)
        return manifest

    # -- querying ---------------------------------------------------------
    def _projections(self, name: str, sdk: int) -> list[tuple[str, list[str]]]:
        core = list(CORE_COLUMNS)
        dims = list(COLLECTION_DIMS.get(name, ()))
        safe = core + ["_data", "datetaken"] + dims
        full = safe + ["relative_path", "volume_name", "owner_package_name", "is_pending"]
        if sdk == 0 or sdk >= 30:
            full.append("is_trashed")
        tiers: list[tuple[str, list[str]]] = []
        if sdk == 0 or sdk >= 29:
            tiers.append(("full", full))
        tiers.append(("safe", safe))
        tiers.append(("core", core))
        seen: set[tuple[str, ...]] = set()
        result: list[tuple[str, list[str]]] = []
        for label, columns in tiers:
            key = tuple(columns)
            if key in seen:
                continue
            seen.add(key)
            result.append((label, columns))
        return result

    def _command(self, name: str, columns: list[str], *, sort: bool = True) -> str:
        uri = f"content://media/external/{name}/media"
        projection = ":".join(columns)
        command = (
            f"content query --uri {shell_quote(uri)} "
            f"--projection {shell_quote(projection)}"
        )
        if sort:
            command += f" --sort {shell_quote('_id ASC')}"
        return command

    def _query_collection(self, name: str) -> dict[str, Any]:
        started = time.monotonic()
        attempts = 0
        errors: list[str] = []
        last_command = ""
        last_rc: int | None = None
        last_stderr = ""
        last_stdout_bytes = 0
        for label, columns in self._projections(name, self.sdk):
            for use_sort in (True, False):
                attempts += 1
                command = self._command(name, columns, sort=use_sort)
                last_command = command
                try:
                    rc, output, error = self.transport.shell_rc(command, timeout=self.timeout)
                except TransportError as exc:
                    errors.append(f"{label}: {exc}")
                    break
                last_rc = rc
                last_stderr = (error or "").strip()[:500]
                last_stdout_bytes = len(output.encode("utf-8", errors="ignore"))
                if self.max_output_bytes and last_stdout_bytes > self.max_output_bytes:
                    errors.append(f"{label}: 输出超过 max_output_bytes={self.max_output_bytes}")
                    break
                if rc == 0:
                    rows = parse_content_rows(output)
                    if not rows and _looks_like_error(output, error):
                        errors.append(f"{label}: {(error or output).strip()[:400]}")
                        continue
                    records = []
                    malformed = 0
                    for row in rows:
                        try:
                            records.append(normalize_record(name, row))
                        except Exception:  # noqa: BLE001 - one bad row must not abort
                            malformed += 1
                    if malformed:
                        errors.append(f"{label}: {malformed} 行无法解析")
                    return {
                        "name": name,
                        "label": COLLECTION_LABELS.get(name, name),
                        "available": True,
                        "status": "ok",
                        "rows": len(records),
                        "bytes": sum(int(item.get("size") or 0) for item in records),
                        "projection": columns,
                        "projection_tier": label,
                        "attempts": attempts,
                        "error": "",
                        "command": command,
                        "exit_code": rc,
                        "stderr": last_stderr,
                        "stdout_bytes": last_stdout_bytes,
                        "elapsed_seconds": round(time.monotonic() - started, 1),
                        "records": records,
                    }
                message = (error or output).strip()[:500] or f"退出码 {rc}"
                errors.append(f"{label}: {message}")
                if use_sort and ("sort" in message.lower() or "unknown option" in message.lower()):
                    continue  # retry the same projection without --sort
                break
            if _fatal_error(errors[-1] if errors else ""):
                break
        return {
            "name": name,
            "label": COLLECTION_LABELS.get(name, name),
            "available": False,
            "status": "failed",
            "rows": 0,
            "bytes": 0,
            "projection": [],
            "projection_tier": "",
            "attempts": attempts,
            "error": "；".join(errors[-2:]),
            "command": last_command,
            "exit_code": last_rc,
            "stderr": last_stderr,
            "stdout_bytes": last_stdout_bytes,
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }


def _looks_like_error(output: str, error: str) -> bool:
    text = f"{error}\n{output}".strip().lower()
    return any(marker in text for marker in ("exception", "error:", "permission denial", "unknown command"))


def _fatal_error(message: str) -> bool:
    text = message.lower()
    return any(marker in text for marker in _FATAL_ERROR_MARKERS)
