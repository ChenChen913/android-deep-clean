"""Markdown report renderers (Chinese).

All renderers are pure functions over JSON dictionaries produced by the
planner/executor.  They never touch the device.
"""

from __future__ import annotations

from typing import Any

from .rules import category_title
from .util import (
    RISK_LABELS,
    safe_display_path,
    human_bytes,
    md_code,
    md_escape,
)

PROTECTED_NOTE = "受保护内容不会出现在可执行计划中。"


def _storage_lines(storage: dict[str, Any], coverage: dict[str, Any]) -> list[str]:
    if not storage:
        return []
    device = storage.get("device_storage") or {}
    scan = storage.get("file_scan") or {}
    unexplained = storage.get("unexplained") or {}
    ratio = scan.get("scan_ratio")
    lines = [
        "## 存储空间与扫描覆盖",
        "",
        f"覆盖率状态：**{coverage.get('status') or storage.get('coverage_status') or 'unknown'}**",
        "",
        "| 指标 | 数值 | 口径说明 |",
        "|---|---:|---|",
        f"| 设备已用空间 | {device.get('used_human') or '-'} | "
        f"`{device.get('path') or '-'}`，块占用 |",
        f"| 文件扫描唯一大小 | {scan.get('unique_human') or '-'} | "
        f"{scan.get('files', 0)} 个文件，逻辑大小；硬链接只计一次 |",
        f"| 扫描逻辑大小 | {scan.get('logical_human') or '-'} | 未去重；与上一行差额为硬链接 |",
    ]
    if ratio is not None:
        lines.append(f"| 去重后扫描大小 / 设备已用 | {float(ratio) * 100:.1f}% | 块占用与逻辑大小口径不同，仅作参考 |")
    if unexplained.get("used_minus_scan_bytes") is not None:
        lines.append(
            f"| 未由文件扫描解释（仅共享存储） | {unexplained.get('used_minus_scan_human') or '-'} | "
            "设备已用 − 共享存储文件扫描；不含基础 APK。口径说明见上方「空间去向速览」 |"
        )
    lines += ["", unexplained.get("note") or "", ""]
    largest = coverage.get("largest_dirs") or []
    if largest:
        lines += ["### 最大目录（扫描范围内）", "", "| 目录 | 逻辑大小 | 文件数 |", "|---|---:|---:|"]
        for item in largest[:10]:
            lines.append(
                f"| {md_code(safe_display_path(str(item.get('path') or '')))} | "
                f"{human_bytes(int(item.get('bytes') or 0))} | {int(item.get('files') or 0)} |"
            )
        lines.append("")
    reasons = coverage.get("reasons") or storage.get("coverage_reasons") or []
    if reasons:
        lines += ["覆盖限制：", ""]
        lines += [f"- {item}" for item in reasons]
        lines.append("")
    return lines


_MEDIA_STATUS_LABELS = {
    "collected": "已采集",
    "partial": "部分可用",
    "unavailable": "不可用",
    "unsupported": "不支持",
    "disabled": "本轮未采集",
    "not_collected": "未采集",
}
_COLLECTION_LABELS = {"images": "图片", "video": "视频", "audio": "音频", "downloads": "下载"}
_COLLECTION_STATUS_LABELS = {
    "ok": "正常",
    "empty": "0 条（未发现可参与核对的同类文件）",
    "zero_unconfirmed": "0 条（少量文件，无法确认）",
    "zero_mismatch": "0 条但与扫描不一致",
    "failed": "失败",
    "skipped": "跳过",
}


def _media_lines(media: dict[str, Any]) -> list[str]:
    if not media:
        return []
    status = str(media.get("status") or "not_collected")
    available = bool(media.get("available"))
    lines = [
        "## 媒体索引核对（第二数据源）",
        "",
        "> 媒体索引只作为证据，不能授权删除；其大小不计入文件扫描合计。",
        "",
        f"- 状态：{_MEDIA_STATUS_LABELS.get(status, status)}",
    ]
    if media.get("reason"):
        lines.append(f"- 原因：{md_escape(str(media['reason']))}")
    if not available:
        for item in media.get("collections") or []:
            if item.get("status") != "ok" and item.get("error"):
                lines.append(f"- {item.get('label') or item.get('name')}：{md_escape(str(item['error']))}")
        lines += [
            "",
            "媒体索引缺失或不可用不影响文件扫描结果，但本轮无法解释媒体库与文件系统之间的差异。",
            "",
        ]
        return lines
    index = media.get("index") or {}
    cross = media.get("crosscheck") or {}
    ratio = cross.get("match_ratio")
    lines += [
        "",
        "| 指标 | 数值 | 口径说明 |",
        "|---|---:|---|",
        f"| 媒体库条目 | {index.get('items', 0)} | 来自系统 MediaStore |",
        f"| 媒体库声明大小 | {index.get('bytes_human') or human_bytes(int(index.get('bytes') or 0))} | 索引字段 `_size`，不等于扫描大小 |",
        f"| 可解析路径 | {index.get('path_known', 0)} | 其余 {index.get('path_unknown', 0)} 条无法与文件路径核对 |",
        f"| 文件扫描中可找到 | {cross.get('found_in_scan', 0)} | "
        + (f"范围内匹配率 {float(ratio) * 100:.1f}%" if ratio is not None else "匹配率未知") + " |",
        f"| 扫描范围内的索引条目 | {cross.get('index_in_scope', 0)} | "
        + (
            f"占全部可解析索引 {float(cross['index_scope_ratio']) * 100:.1f}%"
            if cross.get("index_scope_ratio") is not None else "范围占比未知"
        ) + " |",
        f"| 索引有、扫描无（范围内） | {cross.get('index_only_in_scope', 0)} | "
        "可能位于不可读区域、索引延迟或文件已移动；不能认定文件已删除 |",
        f"| 索引有、扫描无（范围外） | {cross.get('index_only_outside_scope', 0)} | 不在本轮扫描根目录内，无法核对 |",
        f"| 扫描有、索引无 | {cross.get('scan_only', 0)} | "
        f"共享目录内约 {human_bytes(int(cross.get('scan_only_bytes') or 0))}；可能未索引/隐藏目录，不代表异常 |",
        f"| 大小不一致 | {cross.get('size_mismatch', 0)} | 索引可能过期，以文件系统为准 |",
        f"| 仅大小写不同 | {cross.get('case_mismatch', 0)} | 共享存储通常大小写不敏感，已按同一路径核对 |",
        f"| 回收站 / pending | {index.get('trashed', 0)} / {index.get('pending', 0)} | 仅提示索引状态，不改变文件风险 |",
        "",
    ]
    collections = media.get("collections") or []
    if collections:
        lines += [
            "### 媒体集合状态", "",
            "| 集合 | 状态 | 索引行数 | 文件扫描同类 | 投影层级 | 尝试 | 说明 |",
            "|---|---|---:|---:|---|---:|---|",
        ]
        for item in collections:
            collection_status = str(item.get("status") or "")
            lines.append(
                f"| {md_escape(str(item.get('label') or item.get('name') or ''))} | "
                f"{_COLLECTION_STATUS_LABELS.get(collection_status, collection_status)} | "
                f"{int(item.get('rows') or 0)} | {int(item.get('scan_files') or 0)} | "
                f"{md_escape(str(item.get('projection_tier') or '-'))} | "
                f"{int(item.get('attempts') or 0)} | "
                f"{md_escape(str(item.get('error') or ''))} |"
            )
        lines.append("")
    collection_cross = media.get("collection_crosscheck") or {}
    if collection_cross.get("zero_mismatch"):
        names = "、".join(
            _COLLECTION_LABELS.get(str(name), str(name))
            for name in collection_cross.get("zero_mismatch") or []
        )
        lines += [
            f"- 零记录异常：{md_escape(names)}；文件扫描发现同类文件，不能认定媒体库为空。",
            "",
        ]
    if collection_cross.get("zero_unconfirmed"):
        names = "、".join(
            _COLLECTION_LABELS.get(str(name), str(name))
            for name in collection_cross.get("zero_unconfirmed") or []
        )
        threshold = collection_cross.get("threshold") or 10
        lines += [
            f"- 零记录待确认：{md_escape(names)}；文件扫描只发现少量同类文件（低于异常阈值 {threshold}），"
            "无法确认媒体库为空，也不判定为异常。",
            "",
        ]

    def sample_table(title: str, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        lines.extend([
            f"### {title}", "",
            "| 路径 | 索引大小 | 集合 | 说明 |", "|---|---:|---|---|",
        ])
        for item in items[:8]:
            collections = "、".join(
                _COLLECTION_LABELS.get(str(name), str(name))
                for name in (item.get("collections") or [])
            ) or "-"
            lines.append(
                f"| {md_code(safe_display_path(str(item.get('path') or '')))} | "
                f"{item.get('human_size') or human_bytes(int(item.get('size') or 0))} | "
                f"{md_escape(collections)} | {md_escape(str(item.get('reason') or ''))} |"
            )
        lines.append("")

    samples = cross.get("samples") or {}
    sample_table("索引有、文件扫描无（本轮范围内）", samples.get("index_only") or [])
    sample_table("索引有、文件扫描无（范围外）", samples.get("index_only_outside") or [])
    sample_table("大小不一致", samples.get("size_mismatch") or [])
    sample_table("扫描有、媒体索引无", samples.get("scan_only") or [])
    limitations = media.get("limitations") or []
    if limitations:
        lines += ["媒体索引限制：", ""]
        lines += [f"- {md_escape(str(item))}" for item in limitations]
        lines.append("")
    return lines


_RELATION_LABELS = {
    "total": "设备总量",
    "disjoint-with-apk": "与 APK 不重叠",
    "disjoint-with-file-scan": "与文件扫描不重叠",
    "subset-of-file-scan": "包含在文件扫描内",
    "overlaps-file-scan": "与文件扫描重叠",
}


def _space_story_lines(story: dict[str, Any]) -> list[str]:
    if not story:
        return []
    lines = [
        "## 空间去向速览",
        "",
        f"> {md_escape(str(story.get('headline') or ''))}",
        "",
        "| 项目 | 大小 | 统计口径 | 与其它数据的关系 |",
        "|---|---:|---|---|",
    ]
    for item in story.get("items") or []:
        relation = _RELATION_LABELS.get(str(item.get("relation") or ""), "-")
        lines.append(
            f"| {md_escape(str(item.get('label') or ''))} | {item.get('human') or '-'} | "
            f"{md_escape(str(item.get('measure') or ''))}；{md_escape(str(item.get('scope') or ''))} | "
            f"{relation} |"
        )
    known = story.get("known_disjoint_human")
    unexplained = story.get("unexplained_human")
    if known:
        lines.append(
            f"| **已知且不重叠合计** | **{known}** | 文件扫描 + APK + 可读的内部 data | 可与设备已用空间比较 |"
        )
    if unexplained:
        lines.append(
            f"| **未解释差额** | **{unexplained}** | 设备已用 − 已知不重叠 | "
            "含应用内部数据、系统分区、未扫描区域和文件系统开销 |"
        )
    lines += ["", "**为什么不能相加**：", ""]
    lines += [f"- {md_escape(str(item))}" for item in story.get("caveats") or []]
    lines += ["", "各数据源的关系：", ""]
    lines += [
        "- 文件扫描：共享存储内真实存在、本轮能读到的文件。",
        "- 应用外部目录：`Android/data|media|obb`，已经包含在文件扫描里。",
        "- 媒体索引：系统媒体库条目，绝大多数也包含在文件扫描里。",
        "- 基础 APK：位于 `/data/app` 与系统分区，不在 `/sdcard` 扫描范围内。",
        "- APK 已尝试包含 split APK；查询上限与权限限制会如实标记。",
        "- 应用内部 data/cache：普通 ADB shell 读不到；root/su 下由只读 du 抽样统计，计入不重叠部分。",
        "",
    ]
    return lines


def _app_storage_lines(app: dict[str, Any]) -> list[str]:
    if not app:
        return []
    status = str(app.get("status") or "not_collected")
    known = app.get("known") or {}
    packages = app.get("packages") or {}
    lines = [
        "## 应用级存储（部分口径）",
        "",
        "> APK 含基础与已查询的 split；内部 data 仅在 root/su 下用只读 du 统计；外部目录已包含在文件扫描里。分项是子集，不能重复相加，也不能与设备已用空间相加。",
        "",
        f"- 状态：{_MEDIA_STATUS_LABELS.get(status, status)}",
    ]
    if app.get("reason"):
        lines.append(f"- 原因：{md_escape(str(app['reason']))}")
    if not app.get("available"):
        lines += ["", "应用级存储统计不可用；这不影响文件扫描结果。", ""]
        for item in app.get("limitations") or []:
            lines.append(f"- {md_escape(str(item))}")
        lines.append("")
        return lines
    unknown = app.get("unknown") or {}
    cache_state = str(unknown.get("internal_cache") or "")
    cache_note = (
        f"（{md_escape(cache_state)}）"
        if cache_state and cache_state not in {"已读取", "未采集"} else ""
    )
    cache_human = str(known.get("internal_cache_human") or "未知")
    lines += [
        "",
        "| 指标 | 数值 | 口径 |",
        "|---|---:|---|",
        f"| 应用包 | {packages.get('total', 0)} 个 | 已安装 {packages.get('installed', 0)}，系统 {packages.get('system', 0)} |",
        f"| 基础 APK | {known.get('apk_base_human') or human_bytes(int(known.get('apk_base_bytes') or 0))} | "
        + md_escape(
            "基础安装包" + (f"；{unknown['apk_base']}" if unknown.get("apk_base") else "")
        ) + " |",
        f"| Split APK | {known.get('apk_split_human') or human_bytes(int(known.get('apk_split_bytes') or 0))} | "
        f"共 {known.get('apk_file_count', 0)} 个 APK 文件；{md_escape(str(unknown.get('split_apk') or '未采集'))} |",
        f"| 内部 data / cache | {known.get('internal_data_human') or human_bytes(int(known.get('internal_data_bytes') or 0))} | "
        f"含内部 cache {cache_human}{cache_note}；{md_escape(str(unknown.get('internal_data') or '不可读'))} |",
        f"| 外部 data | {known.get('external_data_human') or human_bytes(int(known.get('external_data_bytes') or 0))} | 含 cache {known.get('external_cache_human') or '0 B'} |",
        f"| 外部 media | {known.get('external_media_human') or human_bytes(int(known.get('external_media_bytes') or 0))} | `/sdcard/Android/media` |",
        f"| 外部 obb | {known.get('external_obb_human') or human_bytes(int(known.get('external_obb_bytes') or 0))} | `/sdcard/Android/obb` |",
        f"| 已知合计 | {known.get('total_human') or human_bytes(int(known.get('total_bytes') or 0))} | APK + 内部 data + 外部目录，不是应用总占用 |",
        "",
    ]
    top = app.get("top_external") or []
    if top:
        lines += [
            "### 外部存储占用较高的应用",
            "",
            "| 应用 | 包名 | 外部合计 | data | media | obb | 外部 cache | APK 合计 | 内部 data |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
        for item in top[:8]:
            lines.append(
                f"| {md_escape(str(item.get('label') or '-'))} | {md_code(str(item.get('package') or ''))} | "
                f"{item.get('external_total_human') or '-'} | {item.get('data_human') or '-'} | "
                f"{item.get('media_human') or '-'} | {item.get('obb_human') or '-'} | "
                f"{item.get('cache_human') or '-'} | {item.get('apk_human') or '-'} | "
                f"{item.get('internal_data_human') or '-'} |"
            )
        lines.append("")
    residue = app.get("residue") or []
    if residue:
        lines += [
            "### 未安装应用仍保留的外部目录",
            "",
            "| 包名 | 外部合计 | data | media | obb |",
            "|---|---:|---:|---:|---:|",
        ]
        for item in residue[:8]:
            lines.append(
                f"| {md_code(str(item.get('package') or ''))} | {item.get('external_total_human') or '-'} | "
                f"{item.get('data_human') or '-'} | {item.get('media_human') or '-'} | {item.get('obb_human') or '-'} |"
            )
        lines.append("> 这些目录只作为线索，是否清理仍需逐包复核，不能仅凭包名未安装就删除。")
        lines.append("")
    limitations = app.get("limitations") or []
    if limitations:
        lines += ["应用级存储限制：", ""]
        lines += [f"- {md_escape(str(item))}" for item in limitations]
        lines.append("")
    return lines


def _directory_lines(diff: dict[str, Any]) -> list[str]:
    if not diff:
        return []
    summary = diff.get("summary") or {}
    lines = [
        "## 跨轮目录变化",
        "",
        "> 目录档案只提供线索，不改变风险等级，也不构成删除授权。",
        "",
    ]
    if diff.get("baseline"):
        reason = {
            "first_scan": "首次扫描，已建立目录基线",
            "environment_changed": "设备环境变化，已重新建立基线",
        }.get(str(diff.get("baseline_reason") or ""), "已建立目录基线")
        lines.append(f"- {reason}；下一轮开始显示新增、增长、缩小、消失和复活。")
    else:
        lines.append(
            f"- 新增 {summary.get('new', 0)}；增长 {summary.get('grew', 0)}；"
            f"缩小 {summary.get('shrunk', 0)}；本轮未再出现 {summary.get('vanished', 0)}；"
            f"删后复活 {summary.get('resurrected', 0)}；疑似残留 {summary.get('orphan_suspect', 0)}"
        )
    if diff.get("scan_complete") is False:
        lines.append("- 本轮扫描不完整：消失类变化未计入，避免把未扫描区域误判为已删除。")
    if diff.get("environment_changed"):
        lines.append("- 设备系统/用户/指纹发生变化，本轮按新基线处理，未把全部目录标成新增或消失。")
    lines.append("")

    def table(title: str, items: list[dict[str, Any]], columns: str) -> None:
        rows = items[:10]
        if not rows:
            return
        lines.extend([f"### {title}", "", f"| {columns} |", "|---|---:|---|---|"])
        for item in rows:
            path = md_code(safe_display_path(str(item.get("path") or "")))
            pkg = item.get("app_pkg") or "-"
            if title == "增长明显":
                lines.append(
                    f"| {path} | {item.get('human_size') or human_bytes(int(item.get('bytes') or 0))} | "
                    f"+{human_bytes(int(item.get('delta_bytes') or 0))} | {md_escape(pkg)} |"
                )
            elif title == "新增目录":
                lines.append(
                    f"| {path} | {item.get('human_size') or human_bytes(int(item.get('bytes') or 0))} | "
                    f"{item.get('files', 0)} | {md_escape(pkg)} |"
                )
            elif title == "删后复活":
                lines.append(
                    f"| {path} | {item.get('human_size') or human_bytes(int(item.get('bytes') or 0))} | "
                    f"{item.get('resurrect_count', 1)} | {md_escape(pkg)} |"
                )
            elif title == "未审查目录":
                lines.append(
                    f"| {path} | {item.get('human_size') or human_bytes(int(item.get('bytes') or 0))} | "
                    f"{item.get('files', 0)} | {md_escape(pkg)} |"
                )
            elif title == "应用版本变化":
                lines.append(
                    f"| {path} | {item.get('human_size') or human_bytes(int(item.get('bytes') or 0))} | "
                    f"{md_escape(str(item.get('previous_version') or '-'))} -> "
                    f"{md_escape(str(item.get('version') or '-'))} | {md_escape(pkg)} |"
                )
            else:
                lines.append(
                    f"| {path} | {item.get('human_size') or human_bytes(int(item.get('bytes') or 0))} | "
                    f"{item.get('reason') or '-'} | {md_escape(pkg)} |"
                )
        lines.append("")

    table("增长明显", diff.get("grew") or [], "路径 | 当前大小 | 增量 | 应用")
    table("新增目录", diff.get("new") or [], "路径 | 大小 | 文件数 | 应用")
    table("删后复活", diff.get("resurrected") or [], "路径 | 大小 | 复活次数 | 应用")
    table("疑似卸载残留", diff.get("orphan_suspect") or [], "路径 | 大小 | 原因 | 应用")
    table("应用版本变化", diff.get("app_updated") or [], "路径 | 大小 | 旧版本 -> 新版本 | 应用")
    table("未审查目录", diff.get("unreviewed") or [], "路径 | 大小 | 文件数 | 应用")
    limitations = diff.get("limitations") or []
    if limitations:
        lines += ["档案限制：", ""]
        lines += [f"- {item}" for item in limitations]
        lines.append("")
    return lines


def _risk_badge(risk: str) -> str:
    return f"{risk}（{RISK_LABELS.get(risk, risk)}）"


def _fmt_ts(value: Any) -> str:
    return str(value or "-")


def analysis_markdown(analysis: dict[str, Any], config: dict[str, Any]) -> str:
    summary = analysis.get("summary") or {}
    items = analysis.get("items") or []
    platform = str(analysis.get("platform") or "android")
    title = {"harmonyos_next": "HarmonyOS NEXT", "harmonyos_compat": "HarmonyOS 兼容版"}.get(platform, "Android")
    lines = [
        f"# {title} 深度清理分析报告（清理前）",
        "",
        "> 本报告为**只读分析**，尚未删除、移动或隔离任何文件。所有“可释放空间”均为逻辑大小估算。",
        "",
        f"- 分析 ID：{_fmt_ts(analysis.get('analysis_id'))}",
        f"- 生成时间：{_fmt_ts(analysis.get('generated_at'))}",
        f"- 设备：{(summary.get('device') or {}).get('manufacturer', '')} {(summary.get('device') or {}).get('model', '')}".strip(),
        f"- 系统：{_fmt_ts(summary.get('android'))}",
        f"- 扫描文件：{(summary.get('stats') or {}).get('files', 0)} 个 / 逻辑大小 {human_bytes((summary.get('stats') or {}).get('bytes', 0))}",
        f"- 候选组：{summary.get('candidate_groups', 0)}，涉及文件 {summary.get('candidate_files', 0)} 个，逻辑大小 {human_bytes(summary.get('candidate_bytes', 0))}",
        f"- 需要 AI/人工复核：{summary.get('review_required', 0)} 组",
        "",
    ]
    lines += _space_story_lines(summary.get("space_story") or {})
    lines += _storage_lines(summary.get("storage") or {}, summary.get("coverage") or {})
    lines += _media_lines(summary.get("media_index") or {})
    lines += _app_storage_lines(summary.get("app_storage") or {})
    lines += _directory_lines(summary.get("directory_changes") or {})
    lines += [
        "## 分类总览",
        "",
        "| 分类 | 组数 | 文件数 | 逻辑大小 | 最高风险 | 建议 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for category in summary.get("categories") or []:
        lines.append(
            f"| {category.get('title')} | {category.get('groups', 0)} | {category.get('files', 0)} | "
            f"{human_bytes(category.get('bytes', 0))} | {_risk_badge(category.get('risk', 'SAFE'))} | "
            f"{_advice(category.get('category', ''))} |"
        )
    if not (summary.get("categories") or []):
        lines.append("| - | 0 | 0 | 0 B | - | 未发现候选 |")
    lines += ["", "## 风险分布", "", "| 风险 | 组数 | 文件数 | 逻辑大小 |", "|---|---:|---:|---:|"]
    for risk in ("SAFE", "CAUTION", "DANGEROUS"):
        info = (summary.get("risk_totals") or {}).get(risk)
        if info:
            lines.append(f"| {_risk_badge(risk)} | {info.get('groups', 0)} | {info.get('files', 0)} | {human_bytes(info.get('bytes', 0))} |")
    if not summary.get("risk_totals"):
        lines.append("| - | 0 | 0 | 0 B |")

    lines += ["", "## 候选明细", ""]
    by_category: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_category.setdefault(item.get("category", ""), []).append(item)
    max_detail = int(config.get("report", {}).get("max_detail_items", 120))
    for category, cat_items in by_category.items():
        lines += [f"### {category_title(category)}（{len(cat_items)} 组）", ""]
        for item in cat_items[:max_detail]:
            lines.append(f"#### {item.get('id')} {item.get('title')}")
            lines.append("")
            lines.append(
                f"- 风险：**{_risk_badge(item.get('risk', 'CAUTION'))}**；"
                f"置信度：{float(item.get('confidence') or 0):.2f}；动作：{item.get('action')}"
            )
            lines.append(
                f"- 文件 {item.get('file_count', 0)} 个，目录 {item.get('dir_count', 0)} 个，"
                f"逻辑大小 {item.get('human_size', human_bytes(item.get('total_bytes', 0)))}"
            )
            explain = item.get("explain") or {}
            if explain.get("what"):
                lines.append(f"- 这是什么：{md_escape(str(explain['what']))}")
            if explain.get("why"):
                lines.append(f"- 为什么列出：{md_escape(str(explain['why']))}")
            if explain.get("impact"):
                lines.append(f"- 删除影响：{md_escape(str(explain['impact']))}")
            lines.append("- 路径示例：")
            for path in (item.get("sample_paths") or [])[:8]:
                lines.append(f"  - {md_code(safe_display_path(path))}")
            if int(item.get("file_count", 0)) > len(item.get("sample_paths") or []):
                lines.append(f"  - ……另有 {int(item.get('file_count', 0)) - len(item.get('sample_paths') or [])} 个文件，完整清单在 analysis.db")
            if item.get("evidence"):
                lines.append("- 证据：")
                for evidence in item["evidence"][:8]:
                    lines.append(f"  - {md_escape(evidence.get('text', ''))}")
            if item.get("reasons"):
                lines.append("- 判断理由：")
                for reason in item["reasons"]:
                    lines.append(f"  - {md_escape(reason)}")
            if item.get("keep_suggestion"):
                lines.append(f"- 保留建议：{md_escape(item['keep_suggestion'])}")
            if item.get("ai_review_required"):
                lines.append("- AI 复核：**必须**在 review.json 中给出 verdict 和 reason 后才能选择。")
            lines.append("")

    protected = summary.get("protected_items") or []
    if protected:
        lines += ["## 受保护内容（不可选，仅展示）", "", "| 路径 | 大小 | 原因 |", "|---|---:|---|"]
        for item in protected:
            lines.append(
                f"| {md_code(safe_display_path(item.get('path', '')))} | {human_bytes(item.get('size', 0))} | "
                f"{md_escape(item.get('reason', ''))} |"
            )
        lines.append("")

    limitations = summary.get("limitations") or []
    if limitations:
        lines += ["## 权限限制与扫描缺口（必须向用户说明）", ""]
        lines += [f"- {md_escape(item)}" for item in limitations]
        lines.append("")

    lines += [
        "## 下一步",
        "",
        "1. AI 先读 analysis-summary.json，对 review_items 逐组复核；必要时运行 inspect 查看文件详情。",
        "2. 把复核结论写入 review-template.json 的副本（建议命名 review.json）。",
        "3. 运行 plan 生成清理计划，再让用户选择范围。",
        "4. 用户明确确认后，用 select 生成确认令牌；没有用户确认与令牌时禁止执行。",
        "",
        f"> {PROTECTED_NOTE}",
        "",
    ]
    return "\n".join(lines)


def plan_markdown(plan: dict[str, Any]) -> str:
    items = plan.get("items") or []
    summary = plan.get("summary") or {}
    lines = [
        "# 清理计划（尚未执行）",
        "",
        "> 计划不是执行。必须由用户明确选择并确认后，才能生成选择令牌并执行。",
        "",
        f"- Plan ID：{plan.get('plan_id')}",
        f"- 分析 ID：{plan.get('analysis_id')}",
        f"- 目标设备：{(plan.get('device_identity') or {}).get('serial', '-')} / {(plan.get('device_identity') or {}).get('id', '-')}",
        f"- 生成时间：{plan.get('generated_at')}",
        f"- 计划项：{summary.get('groups', 0)} 组 / {summary.get('files', 0)} 个文件 / {human_bytes(summary.get('bytes', 0))}",
        f"- 可选：{summary.get('selectable_groups', 0)} 组；需复核：{summary.get('review_required', 0)} 组",
        "",
        "## 计划项",
        "",
        "| ID | 分类 | 风险 | 文件 | 逻辑大小 | 可选 | AI 结论 | 理由 |",
        "|---|---|---|---:|---:|---|---|---|",
    ]
    for item in items:
        verdict = item.get("ai_verdict") or "未复核"
        reason = item.get("ai_reason") or ""
        lines.append(
            f"| {item.get('id')} | {category_title(item.get('category', ''))} | {_risk_badge(item.get('risk', 'CAUTION'))} | "
            f"{item.get('file_count', 0)} | {human_bytes(item.get('total_bytes', 0))} | "
            f"{'是' if item.get('selectable') else '否'} | {md_escape(verdict)} | {md_escape(reason)[:100]} |"
        )
    if not items:
        lines.append("| - | - | - | 0 | 0 B | - | - | - |")
    lines += ["", "## 风险提醒", ""]
    for risk in ("SAFE", "CAUTION", "DANGEROUS"):
        group = [item for item in items if item.get("risk") == risk]
        if group:
            lines.append(f"- {_risk_badge(risk)}：{len(group)} 组。{_risk_advice(risk)}")
    limitations = plan.get("limitations") or []
    if limitations:
        lines += ["", "## 限制", ""]
        lines += [f"- {md_escape(item)}" for item in limitations]
    lines += [
        "",
        "## 下一步",
        "",
        "1. 向用户逐类展示本计划，明确说明风险、保留建议与不确定项。",
        "2. 用户选择后运行 select；select 只生成选择清单和确认令牌，不会删除文件。",
        "3. 把令牌交给用户核对后，再运行 execute。用户拒绝时不要执行。",
        "",
    ]
    return "\n".join(lines)


def selection_markdown(selection: dict[str, Any]) -> str:
    items = selection.get("items") or []
    summary = selection.get("summary") or {}
    lines = [
        "# 清理选择清单（等待用户确认）",
        "",
        "> 当前尚未执行。下面的确认令牌只对本次选择有效。",
        "",
        f"- Selection ID：{selection.get('selection_id')}",
        f"- Plan ID：{selection.get('plan_id')}",
        f"- 目标设备：{(selection.get('device_identity') or {}).get('serial', '-')} / {(selection.get('device_identity') or {}).get('id', '-')}",
        f"- 已选择：{summary.get('groups', 0)} 组 / {summary.get('files', 0)} 个文件 / {human_bytes(summary.get('bytes', 0))}",
        f"- 执行模式：{selection.get('mode', 'quarantine')}",
        f"- **确认令牌：{selection.get('token')}**",
        "",
        "| ID | 分类 | 风险 | 文件 | 逻辑大小 | 路径示例 |",
        "|---|---|---|---:|---:|---|",
    ]
    for item in items:
        first_path = (item.get("sample_paths") or [""])[0]
        lines.append(
            f"| {item.get('id')} | {category_title(item.get('category', ''))} | {_risk_badge(item.get('risk', 'CAUTION'))} | "
            f"{item.get('file_count', 0)} | {human_bytes(item.get('total_bytes', 0))} | {md_code(safe_display_path(first_path))} |"
        )
    if not items:
        lines.append("| - | - | - | 0 | 0 B | - |")
    lines += [
        "",
        "## 用户确认要求",
        "",
        "- 向用户展示本清单，并请用户自己确认是否执行；用户拒绝、未回复或要求修改时，禁止执行。",
        "- 用户确认后，把确认令牌交给执行命令；令牌错误、计划被改动、文件变化都会导致拒绝或跳过。",
        "- 默认使用隔离模式：文件移动到设备隔离区，可恢复；隔离不会立即释放空间，需验证后再清理隔离区。",
        "",
        "## 下一步",
        "",
        "execute --state <state> --token <令牌>（默认隔离模式）。",
        "",
    ]
    return "\n".join(lines)


def execution_markdown(execution: dict[str, Any]) -> str:
    summary = execution.get("summary") or {}
    lines = [
        "# 清理执行报告",
        "",
        f"- Execution ID：{execution.get('execution_id')}",
        f"- 设备：{(execution.get('device_identity') or {}).get('serial', '-')} / {(execution.get('device_identity') or {}).get('id', '-')}",
        f"- 传输方式：{execution.get('transport', '-')}",
        f"- 时间：{execution.get('started_at')} → {execution.get('finished_at')}",
        f"- 模式：{execution.get('mode')}",
        f"- 状态：{execution.get('status')}",
        f"- 计划项：{summary.get('groups', 0)}；成功文件：{summary.get('success_files', 0)}；失败文件：{summary.get('failed_files', 0)}",
        f"- 已移动/删除逻辑大小：{human_bytes(summary.get('processed_bytes', 0))}",
        f"- 实际已释放：{human_bytes(summary.get('released_bytes', 0))}（隔离模式需 purge 后才释放）",
        f"- 隔离区：{execution.get('quarantine_root', '-')}",
        f"- 电脑备份：{execution.get('backup_files', 0)} 个 / {human_bytes(execution.get('backup_bytes', 0))}"
        + (f"（{execution.get('backup_root')}）" if execution.get("backup_root") else ""),
        f"- Purge 令牌：{execution.get('purge_token', '-')}",
        "",
        "## 空间账目",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 发现的可疑垃圾（计划全部） | {human_bytes(summary.get('discovered_bytes', 0))} |",
        f"| 本次用户选择 | {human_bytes(summary.get('selected_bytes', 0))} |",
        f"| 已处理（隔离或删除） | {human_bytes(summary.get('processed_bytes', 0))} |",
        f"| 尚未处理 | {human_bytes(summary.get('unprocessed_bytes', 0))} |",
        f"| 已实际释放 | {human_bytes(summary.get('released_bytes', 0))} |",
        f"| 隔离区占用 | {human_bytes(summary.get('quarantine_occupied_bytes', 0))} |",
        "",
        "> 隔离区仍然位于手机上，会继续占用空间；它不是电脑备份。重要文件请另存到电脑或独立存储，确认无误后再 purge。",
        "",
        "## 结果",
        "",
        "| ID | 分类 | 状态 | 成功/失败 | 处理大小 | 说明 |",
        "|---|---|---|---:|---:|---|",
    ]
    for item in execution.get("items") or []:
        lines.append(
            f"| {item.get('id')} | {category_title(item.get('category', ''))} | {item.get('status')} | "
            f"{item.get('success_count', 0)}/{item.get('failed_count', 0)} | {human_bytes(item.get('processed_bytes', 0))} | "
            f"{md_escape(item.get('error', ''))} |"
        )
    if execution.get("abort_reason"):
        lines += ["", f"> ⚠ 执行中止：{md_escape(execution['abort_reason'])}。未执行的项目保持原状。"]
    failures = execution.get("failures") or []
    if failures:
        lines += ["", "## 失败项目（未删除，仍留在原位置）", ""]
        for failure in failures[:100]:
            lines.append(
                f"- {md_code(safe_display_path(failure.get('path', '')))}：{md_escape(failure.get('reason', ''))}"
            )
        if len(failures) > 100:
            lines.append(f"- ……另有 {len(failures) - 100} 条，见 execution.json")
    lines += ["", "## 重要说明", ""]
    if execution.get("mode") == "quarantine":
        lines += [
            "- 文件已移动到隔离区，而不是立即永久删除；可通过 restore 命令恢复。",
            "- 在清理隔离区之前，存储空间不会明显减少；请先运行 verify 检查结果。",
            "- 确认没有问题后，才可用 quarantine purge --token 真正释放空间。",
        ]
    else:
        lines += ["- 本次使用永久删除模式，文件不可恢复。请检查失败项与权限限制。"]
    lines += [
        "",
        "## 下一步",
        "",
        "1. 运行 verify 检查源文件是否消失、隔离文件是否完整。",
        "2. 向用户报告成功、失败、未释放空间和剩余问题。",
        "3. 如需恢复，运行 restore；确认无误后再考虑 quarantine purge。",
        "",
    ]
    return "\n".join(lines)


def verification_markdown(verification: dict[str, Any]) -> str:
    summary = verification.get("summary") or {}
    lines = [
        "# 清理结果验证",
        "",
        f"- 验证 ID：{verification.get('verification_id')}",
        f"- 时间：{verification.get('verified_at')}",
        f"- 检查文件：{summary.get('checked', 0)}；通过：{summary.get('passed', 0)}；失败：{summary.get('failed', 0)}",
        f"- 源文件仍然存在：{summary.get('source_present', 0)}",
        f"- 隔离文件缺失：{summary.get('quarantine_missing', 0)}",
        f"- 存储可用空间变化：{human_bytes(summary.get('free_delta', 0))}（负数表示减少）",
        "",
        "## 失败/异常",
        "",
    ]
    failures = verification.get("failures") or []
    if failures:
        for failure in failures[:100]:
            lines.append(f"- {md_code(safe_display_path(failure.get('path', '')))}：{md_escape(failure.get('reason', ''))}")
    else:
        lines.append("- 无")
    lines += [
        "",
        "> 隔离模式不会立即释放空间；只有 purge 隔离区后，可用空间才会增加。",
        "",
    ]
    return "\n".join(lines)


def recheck_markdown(recheck: dict[str, Any]) -> str:
    summary = recheck.get("summary") or {}
    lines = [
        "# 清理复查报告",
        "",
        f"- 复查 ID：{recheck.get('recheck_id')}",
        f"- 时间：{recheck.get('generated_at')}",
        f"- 上一轮分析：{recheck.get('previous_analysis_id', '-')}",
        f"- 本轮候选：{summary.get('candidate_groups', 0)} 组 / {human_bytes(summary.get('candidate_bytes', 0))}",
        f"- 新增候选：{summary.get('new_count', 0)}；仍存在：{summary.get('remaining_count', 0)}；已消失：{summary.get('vanished_count', 0)}",
        "",
        "## 新增候选",
        "",
        "| ID | 分类 | 风险 | 大小 | 路径示例 |",
        "|---|---|---|---:|---|",
    ]
    for item in (recheck.get("new_items") or [])[:100]:
        first_path = (item.get("sample_paths") or [""])[0]
        lines.append(
            f"| {item.get('id')} | {category_title(item.get('category', ''))} | {_risk_badge(item.get('risk', 'CAUTION'))} | "
            f"{human_bytes(item.get('total_bytes', 0))} | {md_code(safe_display_path(first_path))} |"
        )
    if not recheck.get("new_items"):
        lines.append("| - | - | - | 0 B | 无 |")
    lines += [""]
    lines += _directory_lines(recheck.get("directory_changes") or {})
    lines += [
        "## 说明",
        "",
        "- 复查会重新扫描相同根目录，并生成新的分析；不会自动删除任何内容。",
        "- 新增候选需要重新复核、计划和用户确认，不能沿用旧令牌。",
        "",
    ]
    return "\n".join(lines)


def _advice(category: str) -> str:
    mapping = {
        "app_cache": "可批量清理（用户确认后）",
        "browser_cache": "可批量清理（用户确认后）",
        "thumbnails": "可批量清理（用户确认后）",
        "temp_logs": "可批量清理（用户确认后）",
        "residue": "逐包确认，检查存档/下载",
        "chat_cache": "逐项确认，不碰聊天记录",
        "trash": "逐项确认，可能仍可恢复",
        "duplicates": "逐组确认，保留建议的一份",
        "apk": "逐项确认是否还需安装包",
        "archives": "逐项确认备份价值",
        "screenshots": "查看内容后逐项确认",
        "large_old": "逐项确认用途",
        "media_old": "逐项确认，建议先备份",
        "download_old": "逐项确认",
        "ota": "确认是否还需刷机包",
        "empty_dirs": "逐项确认，rmdir 不会递归删除",
    }
    return mapping.get(category, "人工确认")


def _risk_advice(risk: str) -> str:
    return {
        "SAFE": "有明确证据证明可再生/无用，仍需用户授权后批量处理。",
        "CAUTION": "必须逐项复核并得到用户明确确认，不得默认全选。",
        "DANGEROUS": "默认禁止删除，不应出现在可执行计划中。",
    }.get(risk, "")
