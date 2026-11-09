"""Human-readable space attribution across sources with different scopes.

Every number below is measured by a different method:

- device used: filesystem block usage of ``/data`` (includes everything);
- file scan: unique logical bytes of the scanned shared storage;
- base APK: ``stat`` of installed base APKs under ``/data/app`` / system;
- app external: ``Android/data|media|obb``, which is **inside** the file scan;
- media index: MediaStore ``_size``, mostly **inside** the file scan.

The module never adds overlapping numbers.  It reports the disjoint part
(APK + file scan) and the residual gap with explicit caveats.
"""

from __future__ import annotations

import time
from typing import Any

from .util import human_bytes

MAX_SAMPLES = 6


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _pick_storage(scan_meta: dict[str, Any]) -> dict[str, Any]:
    storage = list(scan_meta.get("storage") or [])
    for preferred in ("/data", "/sdcard"):
        for item in storage:
            if str(item.get("path")) == preferred and item.get("used_bytes"):
                return item
    for item in storage:
        if item.get("used_bytes"):
            return item
    return {}


def _with_human(entry: dict[str, Any]) -> dict[str, Any]:
    entry["human"] = human_bytes(_int(entry.get("bytes")))
    return entry


def build_space_story(
    scan_meta: dict[str, Any],
    app_storage: dict[str, Any],
    media_index: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    coverage = scan_meta.get("coverage") or {}
    stats = scan_meta.get("stats") or {}
    storage = _pick_storage(scan_meta)
    device_used = _int(storage.get("used_bytes") or coverage.get("used_bytes"))
    file_scan = _int(coverage.get("unique_bytes") or stats.get("bytes"))
    app_known = (app_storage.get("known") or {})
    apk_bytes = _int(app_known.get("apk_bytes"))
    app_external = _int(app_known.get("external_total_bytes"))
    media_bytes = _int((media_index.get("index") or {}).get("bytes"))

    disjoint = apk_bytes + file_scan
    residual = device_used - disjoint if device_used else None
    scan_ratio = round(file_scan / device_used, 4) if device_used else None

    items: list[dict[str, Any]] = [
        _with_human({
            "key": "device_used",
            "label": "设备已用空间",
            "bytes": device_used,
            "measure": "块占用",
            "scope": "设备文件系统（包含所有其他行）",
            "relation": "total",
            "note": f"来自 `{storage.get('path') or '/data'}` 的 df",
        }),
        _with_human({
            "key": "file_scan",
            "label": "共享存储文件扫描",
            "bytes": file_scan,
            "measure": "逻辑大小，按 (dev, inode) 去重",
            "scope": "本轮扫描根目录内",
            "relation": "disjoint-with-apk",
            "note": "文件扫描只覆盖 /sdcard；不含应用内部数据与系统分区",
        }),
        _with_human({
            "key": "app_apk",
            "label": "应用基础 APK",
            "bytes": apk_bytes,
            "measure": "stat 基础 APK",
            "scope": "已安装应用的基础 APK（split 未计入）",
            "relation": "disjoint-with-file-scan",
            "note": "位于 /data/app 与系统分区，不在 /sdcard 扫描范围内",
        }),
        _with_human({
            "key": "app_external",
            "label": "应用外部目录",
            "bytes": app_external,
            "measure": "文件扫描子树求和",
            "scope": "Android/data + media + obb",
            "relation": "subset-of-file-scan",
            "note": "已包含在共享存储文件扫描中，不能重复相加",
        }),
        _with_human({
            "key": "media_index",
            "label": "媒体索引声明大小",
            "bytes": media_bytes,
            "measure": "MediaStore `_size`",
            "scope": "系统媒体库条目",
            "relation": "overlaps-file-scan",
            "note": "与文件扫描大量重叠，仅用于交叉核对，不能相加",
        }),
    ]
    if not app_storage.get("available"):
        items = [item for item in items if item["key"] != "app_apk"]
        items[0]["note"] += "；应用 APK 本轮不可用"
    if not media_index.get("available"):
        items = [item for item in items if item["key"] != "media_index"]

    known_items = [item for item in items if item["relation"] == "disjoint-with-apk"]
    known_items += [item for item in items if item["relation"] == "disjoint-with-file-scan"]
    known_disjoint = sum(item["bytes"] for item in known_items)

    caveats: list[str] = [
        "设备已用空间是块占用；文件扫描是逻辑大小，两者口径不同。",
        "应用外部目录与媒体索引都包含在文件扫描内，不能重复相加。",
        "应用内部 data/cache、系统分区、未扫描区域和文件系统开销目前无法解释，属于下方“未解释差额”。",
    ]
    if coverage.get("status") != "good":
        caveats.append("本轮扫描覆盖率不是完整状态，未解释差额会更大。")
    if not app_storage.get("available"):
        caveats.append("应用基础 APK 本轮不可用，未计入已知部分。")
    if media_index.get("status") in {None, "not_collected", "unavailable"}:
        caveats.append("媒体索引本轮不可用，无法交叉核对媒体大小。")

    headline = _headline(device_used, file_scan, known_disjoint, residual, coverage)
    return {
        "schema": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "headline": headline,
        "device_used": _int(device_used),
        "device_used_human": human_bytes(_int(device_used)),
        "known_disjoint_bytes": known_disjoint,
        "known_disjoint_human": human_bytes(known_disjoint),
        "unexplained_bytes": residual,
        "unexplained_human": ("-" if residual is None else human_bytes(abs(residual))),
        "scan_ratio": scan_ratio,
        "items": items,
        "caveats": caveats,
    }


def _headline(
    device_used: int,
    file_scan: int,
    known_disjoint: int,
    residual: int | None,
    coverage: dict[str, Any],
) -> str:
    if not device_used:
        return f"本次扫描到 {human_bytes(file_scan)} 共享存储文件；设备已用空间不可用，无法计算差额。"
    parts = [
        f"设备已用 {human_bytes(device_used)}，",
        f"共享存储文件扫描 {human_bytes(file_scan)}",
        f"（占已用空间 {file_scan / device_used * 100:.1f}%）",
    ]
    if residual is not None:
        if residual >= 0:
            parts.append(f"，加上基础 APK 后仍有 {human_bytes(residual)} 未解释")
        else:
            parts.append(f"，已知部分比设备已用多 {human_bytes(abs(residual))}")
    parts.append("。")
    if coverage.get("status") != "good":
        parts.append("本轮扫描不完整，差额会偏大。")
    return "".join(parts)
