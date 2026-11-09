"""Storage accounting across sources that measure different things.

The rule is simple: never add or subtract bytes from different sources to make
one "total".  This module keeps each source in its own column, records overlap,
and exposes the unexplained gap with its caveats.
"""

from __future__ import annotations

from typing import Any

from .util import human_bytes


def _choose_storage(storage: list[dict[str, Any]]) -> dict[str, Any]:
    for preferred in ("/data", "/sdcard"):
        for item in storage:
            if str(item.get("path")) == preferred and item.get("used_bytes"):
                return item
    return storage[0] if storage else {}


def build_storage_accounting(scan_meta: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    coverage = scan_meta.get("coverage") or {}
    stats = scan_meta.get("stats") or {}
    storage = _choose_storage(list(scan_meta.get("storage") or []))
    used = int(storage.get("used_bytes") or coverage.get("used_bytes") or 0)
    unique = int(coverage.get("unique_bytes") or stats.get("bytes") or 0)
    logical = int(stats.get("bytes") or 0)
    residual = used - unique if used else None
    if residual is None:
        residual_human = "-"
    elif residual >= 0:
        residual_human = human_bytes(residual)
    else:
        residual_human = "-" + human_bytes(abs(residual))
    reasons = list(coverage.get("reasons") or [])
    status = str(coverage.get("status") or ("partial" if reasons else "unknown"))
    media = ((scan_meta.get("aux") or {}).get("media_store") or {})
    media_status = str(media.get("status") or "not_collected")
    media_available = bool(media.get("available")) or media_status in {"collected", "partial"}
    media_reason = str(media.get("reason") or "")
    if not media and not media_reason:
        media_reason = "本轮扫描未采集 MediaStore 索引"
    app = ((scan_meta.get("aux") or {}).get("app_storage") or {})
    app_status = str(app.get("status") or "not_collected")
    app_available = bool(app.get("available")) or app_status in {"collected", "partial"}
    app_reason = str(app.get("reason") or "")
    if not app and not app_reason:
        app_reason = "本轮扫描未采集应用级存储统计"

    return {
        "schema": 1,
        "generated_at": scan_meta.get("scanned_at", ""),
        "coverage_status": status,
        "coverage_reasons": reasons,
        "device_storage": {
            "path": storage.get("path", coverage.get("storage_path", "")),
            "filesystem": storage.get("filesystem", ""),
            "total_bytes": int(storage.get("total_bytes") or 0),
            "used_bytes": used,
            "available_bytes": int(storage.get("available_bytes") or 0),
            "total_human": human_bytes(int(storage.get("total_bytes") or 0)),
            "used_human": human_bytes(used),
            "available_human": human_bytes(int(storage.get("available_bytes") or 0)),
        },
        "file_scan": {
            "available": True,
            "roots_requested": list(coverage.get("roots_requested") or scan_meta.get("roots_requested") or []),
            "roots_scanned": list(coverage.get("roots_scanned") or scan_meta.get("roots_scanned") or []),
            "files": int(stats.get("files") or 0),
            "entries": int(stats.get("entries") or 0),
            "logical_bytes": logical,
            "unique_bytes": unique,
            "logical_human": human_bytes(logical),
            "unique_human": human_bytes(unique),
            "hardlink_saved_bytes": int(coverage.get("hardlink_saved_bytes") or 0),
            "truncated": bool(scan_meta.get("truncated")),
            "scan_ratio": round(float(unique) / used, 4) if used else None,
        },
        "unexplained": {
            "used_minus_scan_bytes": residual,
            "used_minus_scan_human": residual_human,
            "note": (
                "设备已用空间是块占用，文件扫描是逻辑大小；差额包含应用内部数据、"
                "系统数据、未扫描区域、块对齐/压缩/稀疏文件等，不能当作可回收空间。"
            ),
        },
        "media_index": {
            "status": media_status,
            "available": media_available,
            "reason": media_reason,
            "items": int(media.get("items") or 0),
            "bytes": int(media.get("bytes") or 0),
            "path_known": int(media.get("path_known") or 0),
            "trashed": int(media.get("trashed") or 0),
            "pending": int(media.get("pending") or 0),
            "note": "媒体索引来自系统媒体库，与文件扫描、应用统计、系统已用空间口径不同，不能相加。",
        },
        "app_storage": {
            "status": app_status,
            "available": app_available,
            "reason": app_reason,
            "packages": int(app.get("installed") or 0),
            "apk_bytes": int((app.get("apk") or {}).get("base_bytes") or 0)
            + int((app.get("apk") or {}).get("split_bytes") or 0),
            "apk_base_bytes": int((app.get("apk") or {}).get("base_bytes") or 0),
            "apk_split_bytes": int((app.get("apk") or {}).get("split_bytes") or 0),
            "apk_file_count": int((app.get("apk") or {}).get("file_count") or 0),
            "split_status": str((app.get("apk") or {}).get("split_status") or ""),
            "internal_data_bytes": int((app.get("internal") or {}).get("data_bytes") or 0),
            "internal_cache_bytes": int((app.get("internal") or {}).get("cache_bytes") or 0),
            "internal_status": str((app.get("internal") or {}).get("status") or ""),
            "note": "APK 含基础与已查询的 split；内部 data 仅在 root/su 下由只读 du 统计；分项是子集，不能重复相加。",
        },
        "sources": [
            {"name": "file_scan", "available": True, "kind": "filesystem", "read_only": True},
            {
                "name": "media_store",
                "available": media_available,
                "kind": "media_index",
                "read_only": True,
                "status": media_status,
                "items": int(media.get("items") or 0),
                "bytes": int(media.get("bytes") or 0),
                "path_known": int(media.get("path_known") or 0),
                "trashed": int(media.get("trashed") or 0),
                "pending": int(media.get("pending") or 0),
                "reason": media_reason,
            },
            {
                "name": "app_storage",
                "available": app_available,
                "kind": "app_stats",
                "read_only": True,
                "status": app_status,
                "items": int(app.get("installed") or 0),
                "bytes": int(app.get("bytes") or 0),
                "reason": app_reason,
            },
        ],
    }
