"""Frozen copy of the v1 report renderer, kept as a rollback path.

The active renderer is ``html_report.py`` (v2 design).  Nothing imports this
module; it exists so the previous layout can be restored by copying it back
over ``html_report.py``.  Do not add features here.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .util import human_bytes

MAX_ITEMS = 400

_RISK_CLASS = {"SAFE": "safe", "CAUTION": "caution", "DANGEROUS": "danger"}
_MEDIA_STATUS = {
    "collected": "已采集",
    "partial": "部分可用",
    "unavailable": "不可用",
    "unsupported": "不支持",
    "disabled": "本轮未采集",
    "not_collected": "未采集",
}
_COLLECTION = {"images": "图片", "video": "视频", "audio": "音频", "downloads": "下载"}
_MEDIA_COLLECTION_STATUS = {
    "ok": ("正常", "safe"),
    "empty": ("0 条（未发现可参与核对的同类文件）", "caution"),
    "zero_unconfirmed": ("0 条（少量文件，无法确认）", "caution"),
    "zero_mismatch": ("0 条但与扫描不一致", "danger"),
    "failed": ("失败", "danger"),
    "skipped": ("跳过", "caution"),
}


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _num(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _pct(part: int, total: int) -> float:
    if not total:
        return 0.0
    return max(0.0, min(100.0, part / total * 100.0))


def _size(value: Any) -> str:
    return _e(human_bytes(_num(value)))


def render_analysis_html(analysis: dict[str, Any], config: dict[str, Any] | None = None) -> str:
    summary = analysis.get("summary") or {}
    items = list(analysis.get("items") or [])
    device = summary.get("device") or analysis.get("device") or {}
    meta_identity = analysis.get("device_identity") or summary.get("device_identity") or {}
    stats = summary.get("stats") or {}
    storage = summary.get("storage") or {}
    coverage = summary.get("coverage") or {}
    story = summary.get("space_story") or {}
    media = summary.get("media_index") or {}
    app = summary.get("app_storage") or {}
    diff = summary.get("directory_changes") or {}

    title = "深度清理分析报告"
    platform = str(analysis.get("platform") or "android")
    if platform.startswith("harmonyos"):
        title = "HarmonyOS 深度清理分析报告"

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        f"<title>{_e(title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<main class="wrap">',
    ]

    parts.append(
        '<header class="hero">'
        f"<h1>{_e(title)}</h1>"
        '<p class="sub">只读分析 · 尚未删除、移动或隔离任何文件</p>'
        "</header>"
    )

    model = " ".join(
        value for value in (str(device.get("manufacturer") or ""), str(device.get("model") or "")) if value
    ).strip()
    parts.append('<section class="grid meta">')
    for label, value in (
        ("设备", model or "-"),
        ("系统", summary.get("android") or "-"),
        ("序列号", meta_identity.get("serial") or "-"),
        ("分析 ID", analysis.get("analysis_id") or "-"),
        ("生成时间", analysis.get("generated_at") or "-"),
        ("扫描文件", f"{_num(stats.get('files'))} 个 / {human_bytes(_num(stats.get('bytes')))}"),
        ("覆盖率", _coverage_label(coverage.get("status"))),
    ):
        parts.append(
            f'<div class="card"><div class="k">{_e(label)}</div><div class="v">{_e(value)}</div></div>'
        )
    parts.append("</section>")

    parts.append(_nav())
    parts.append(_space_section(story))
    parts.append(_coverage_section(coverage))
    parts.append(_media_section(media))
    parts.append(_app_section(app))
    parts.append(_diff_section(diff))
    parts.append(_candidate_section(items, summary))
    parts.append(_limitations_section(summary, analysis))
    parts.append(
        '<footer class="foot">'
        "本报告由 android-deep-clean 生成，全程只读。"
        "“可释放空间”均为逻辑大小估算；隔离不会立即释放空间。"
        "</footer>"
    )
    parts += ["</main>", "</body>", "</html>", ""]
    return "\n".join(parts)


def write_analysis_html(analysis: dict[str, Any], path: Path, config: dict[str, Any] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_analysis_html(analysis, config), encoding="utf-8")
    return path


def _nav() -> str:
    links = [
        ("space", "空间去向"),
        ("coverage", "扫描覆盖"),
        ("media", "媒体索引"),
        ("apps", "应用存储"),
        ("diff", "目录变化"),
        ("candidates", "清理候选"),
        ("limits", "限制"),
    ]
    anchors = "".join(f'<a href="#{key}">{_e(label)}</a>' for key, label in links)
    return f'<nav class="toc">{anchors}</nav>'


def _space_section(story: dict[str, Any]) -> str:
    if not story:
        return ""
    total = _num(story.get("device_used"))
    known = _num(story.get("known_disjoint_bytes"))
    known_pct = _pct(known, total)
    parts = [
        '<section id="space" class="block">',
        "<h2>空间去向速览</h2>",
        f'<p class="lead">{_e(story.get("headline") or "")}</p>',
    ]
    if total:
        parts.append(
            '<div class="bar" role="img" aria-label="设备已用空间构成">'
            f'<span class="bar-known" style="width:{known_pct:.2f}%"></span>'
            f'<span class="bar-unknown" style="width:{100 - known_pct:.2f}%"></span>'
            "</div>"
            '<div class="legend">'
            f'<span><i class="dot known"></i>已知且不重叠 {_e(story.get("known_disjoint_human") or "-")}'
            f"（{known_pct:.1f}%）</span>"
            f'<span><i class="dot unknown"></i>未解释 {_e(story.get("unexplained_human") or "-")}'
            f"（{100 - known_pct:.1f}%）</span>"
            "</div>"
        )
    parts.append('<div class="scroll"><table><thead><tr>'
                 "<th>项目</th><th class=\"r\">大小</th><th>统计口径</th><th>范围</th><th>与其它数据的关系</th>"
                 "</tr></thead><tbody>")
    relations = {
        "total": "设备总量",
        "disjoint-with-apk": "与 APK 不重叠",
        "disjoint-with-file-scan": "与文件扫描不重叠",
        "subset-of-file-scan": "包含在文件扫描内",
        "overlaps-file-scan": "与文件扫描重叠",
    }
    for item in story.get("items") or []:
        parts.append(
            "<tr>"
            f'<td>{_e(item.get("label"))}</td>'
            f'<td class="r">{_e(item.get("human"))}</td>'
            f'<td>{_e(item.get("measure"))}</td>'
            f'<td>{_e(item.get("scope"))}</td>'
            f'<td>{_e(relations.get(str(item.get("relation")), "-"))}</td>'
            "</tr>"
        )
    parts.append("</tbody></table></div>")
    caveats = story.get("caveats") or []
    if caveats:
        parts.append('<div class="note warn"><strong>为什么不能相加</strong><ul>')
        parts += [f"<li>{_e(item)}</li>" for item in caveats]
        parts.append("</ul></div>")
    parts.append("</section>")
    return "".join(parts)


def _coverage_label(value: Any) -> str:
    return {
        "good": "完整",
        "partial": "部分覆盖",
        "suspect": "可疑（未扫到文件）",
        "scanned": "已扫描",
        "skipped": "未扫描",
        "unknown": "未知",
    }.get(str(value or ""), str(value or "-"))


def _coverage_section(coverage: dict[str, Any]) -> str:
    if not coverage:
        return ""
    parts = [
        '<section id="coverage" class="block">',
        "<h2>扫描覆盖</h2>",
        f'<p class="lead">逻辑大小 {_size(coverage.get("logical_bytes"))}，'
        f'按 (dev, inode) 去重后 {_size(coverage.get("unique_bytes"))}；'
        f'目录 {_num(coverage.get("dir_count"))} 个。</p>',
    ]
    roots = coverage.get("root_status") or []
    if roots:
        parts.append('<div class="scroll"><table><thead><tr><th>根目录</th><th>状态</th><th>说明</th></tr></thead><tbody>')
        for item in roots:
            parts.append(
                f'<tr><td><code>{_e(item.get("path"))}</code></td>'
                f'<td>{_e(_coverage_label(item.get("state")))}</td><td>{_e(item.get("reason") or "-")}</td></tr>'
            )
        parts.append("</tbody></table></div>")
    largest = coverage.get("largest_dirs") or []
    if largest:
        parts.append('<h3>最大目录</h3><div class="scroll"><table><thead><tr>'
                     '<th>目录</th><th class="r">逻辑大小</th><th class="r">文件数</th></tr></thead><tbody>')
        for item in largest[:10]:
            parts.append(
                f'<tr><td><code>{_e(item.get("path"))}</code></td>'
                f'<td class="r">{_size(item.get("bytes"))}</td>'
                f'<td class="r">{_num(item.get("files"))}</td></tr>'
            )
        parts.append("</tbody></table></div>")
    reasons = coverage.get("reasons") or []
    skipped = coverage.get("skipped_paths") or []
    if reasons or skipped:
        parts.append('<div class="note"><strong>覆盖限制</strong><ul>')
        parts += [f"<li>{_e(item)}</li>" for item in reasons]
        if skipped:
            parts.append(f"<li>跳过路径：{_e(', '.join(str(p) for p in skipped[:20]))}</li>")
        parts.append("</ul></div>")
    parts.append("</section>")
    return "".join(parts)


def _media_section(media: dict[str, Any]) -> str:
    if not media:
        return ""
    status = str(media.get("status") or "not_collected")
    index = media.get("index") or {}
    cross = media.get("crosscheck") or {}
    parts = [
        '<section id="media" class="block">',
        "<h2>媒体索引核对</h2>",
        f'<p class="lead">状态：{_e(_MEDIA_STATUS.get(status, status))}'
        + (f"（{_e(media.get('reason'))}）" if media.get("reason") else "")
        + "</p>",
    ]
    collections = media.get("collections") or []
    if collections:
        parts.append('<h3>集合状态</h3><div class="scroll"><table><thead><tr>'
                     '<th>集合</th><th>状态</th><th class="r">索引行数</th>'
                     '<th class="r">文件扫描同类</th><th>投影层级</th><th class="r">尝试</th>'
                     '<th>说明</th></tr></thead><tbody>')
        for item in collections:
            collection_status = str(item.get("status") or "")
            label, css = _MEDIA_COLLECTION_STATUS.get(collection_status, (collection_status or "-", "caution"))
            parts.append(
                f'<tr><td>{_e(item.get("label") or item.get("name"))}</td>'
                f'<td><span class="badge {css}">{_e(label)}</span></td>'
                f'<td class="r">{_num(item.get("rows"))}</td>'
                f'<td class="r">{_num(item.get("scan_files"))}</td>'
                f'<td>{_e(item.get("projection_tier") or "-")}</td>'
                f'<td class="r">{_num(item.get("attempts"))}</td>'
                f'<td>{_e(item.get("error") or "-")}</td></tr>'
            )
        parts.append("</tbody></table></div>")
    collection_cross = media.get("collection_crosscheck") or {}
    if collection_cross.get("zero_mismatch"):
        names = "、".join(
            _COLLECTION.get(str(name), str(name))
            for name in collection_cross.get("zero_mismatch") or []
        )
        parts.append(
            f'<div class="note warn"><strong>零记录异常</strong>'
            f"<p>{_e(names)} 的媒体索引返回 0 条，但文件扫描发现同类文件。"
            "不能认定媒体库为空；请检查权限、Provider 状态或索引是否延迟。</p></div>"
        )
    if collection_cross.get("zero_unconfirmed"):
        names = "、".join(
            _COLLECTION.get(str(name), str(name))
            for name in collection_cross.get("zero_unconfirmed") or []
        )
        threshold = collection_cross.get("threshold") or 10
        parts.append(
            f'<div class="note"><strong>零记录待确认</strong>'
            f"<p>{_e(names)} 的媒体索引返回 0 条，文件扫描只发现少量同类文件"
            f"（低于异常阈值 {_num(threshold)}）。既不能认定媒体库为空，也不判定为异常。</p></div>"
        )
    if not media.get("available"):
        parts.append('<div class="note">媒体索引不可用不影响文件扫描结果，但本轮无法解释媒体库与文件系统的差异。</div>')
        parts.append("</section>")
        return "".join(parts)
    parts.append('<div class="grid stats">')
    for label, value in (
        ("索引条目", f"{_num(index.get('items'))}"),
        ("声明大小", index.get("bytes_human") or _size(index.get("bytes"))),
        ("可解析路径", f"{_num(index.get('path_known'))}"),
        ("范围内匹配率", f"{float(cross.get('match_ratio') or 0) * 100:.1f}%" if cross.get("match_ratio") is not None else "-"),
        ("范围内索引", f"{_num(cross.get('index_in_scope'))}"),
        ("索引有扫描无", f"{_num(cross.get('index_only_in_scope'))}"),
        ("扫描有索引无", f"{_num(cross.get('scan_only'))}"),
        ("仅大小写不同", f"{_num(cross.get('case_mismatch'))}"),
    ):
        parts.append(f'<div class="card"><div class="k">{_e(label)}</div><div class="v">{_e(value)}</div></div>')
    parts.append("</div>")
    samples = cross.get("samples") or {}
    for key, label in (
        ("index_only", "索引有、文件扫描无（范围内）"),
        ("index_only_outside", "索引有、文件扫描无（范围外）"),
        ("size_mismatch", "大小不一致"),
        ("scan_only", "扫描有、媒体索引无"),
    ):
        rows = samples.get(key) or []
        if not rows:
            continue
        parts.append(f'<h3>{_e(label)}</h3><div class="scroll"><table><thead><tr>'
                     '<th>路径</th><th class="r">索引大小</th><th>集合</th><th>说明</th></tr></thead><tbody>')
        for row in rows[:8]:
            collections = "、".join(_COLLECTION.get(str(name), str(name)) for name in (row.get("collections") or [])) or "-"
            parts.append(
                f'<tr><td><code>{_e(row.get("path"))}</code></td>'
                f'<td class="r">{_e(row.get("human_size"))}</td>'
                f'<td>{_e(collections)}</td><td>{_e(row.get("reason"))}</td></tr>'
            )
        parts.append("</tbody></table></div>")
    limitations = media.get("limitations") or []
    if limitations:
        parts.append('<div class="note"><strong>媒体索引限制</strong><ul>')
        parts += [f"<li>{_e(item)}</li>" for item in limitations]
        parts.append("</ul></div>")
    parts.append("</section>")
    return "".join(parts)


def _app_section(app: dict[str, Any]) -> str:
    if not app:
        return ""
    known = app.get("known") or {}
    packages = app.get("packages") or {}
    status = str(app.get("status") or "not_collected")
    parts = [
        '<section id="apps" class="block">',
        "<h2>应用级存储（部分口径）</h2>",
        f'<p class="lead">状态：{_e(_MEDIA_STATUS.get(status, status))}'
        + (f"（{_e(app.get('reason'))}）" if app.get("reason") else "")
        + "；内部 data 仅在 root/su 下由只读 du 抽样统计，split APK 受查询上限约束。</p>",
    ]
    if app.get("available"):
        parts.append('<div class="grid stats">')
        for label, value in (
            ("应用包", f"{_num(packages.get('total'))}"),
            ("基础 APK", known.get("apk_base_human") or known.get("apk_human") or "-"),
            ("Split APK", known.get("apk_split_human") or "-"),
            ("内部 data", known.get("internal_data_human") or "-"),
            ("外部 data", known.get("external_data_human") or "-"),
            ("外部 media", known.get("external_media_human") or "-"),
            ("外部 obb", known.get("external_obb_human") or "-"),
            ("已知合计", known.get("total_human") or "-"),
        ):
            parts.append(f'<div class="card"><div class="k">{_e(label)}</div><div class="v">{_e(value)}</div></div>')
        parts.append("</div>")
        unknown = app.get("unknown") or {}
        if unknown.get("split_apk"):
            parts.append(f'<div class="note">Split APK：{_e(unknown.get("split_apk"))}；共 {_num(known.get("apk_file_count"))} 个 APK 文件。</div>')
        if unknown.get("apk_base"):
            parts.append(f'<div class="note">基础 APK：{_e(unknown.get("apk_base"))}；split APK 单独统计，不折算进基础 APK。</div>')
        if unknown.get("internal_data"):
            cache_note = str(unknown.get("internal_cache") or "")
            extra = (
                f"内部 cache：{_e(cache_note)}。"
                if cache_note and cache_note not in {"已读取", "未采集"}
                else "内部 cache 是 data 的子集。"
            )
            parts.append(f'<div class="note">内部 data：{_e(unknown.get("internal_data"))}；{extra}</div>')
        top = app.get("top_external") or []
        if top:
            parts.append('<h3>外部存储占用较高的应用</h3><div class="scroll"><table><thead><tr>'
                         '<th>应用</th><th>包名</th><th class="r">外部合计</th><th class="r">data</th>'
                         '<th class="r">media</th><th class="r">obb</th><th class="r">APK 合计</th>'
                         '<th class="r">内部 data</th></tr></thead><tbody>')
            for row in top[:12]:
                parts.append(
                    f'<tr><td>{_e(row.get("label"))}</td><td><code>{_e(row.get("package"))}</code></td>'
                    f'<td class="r">{_e(row.get("external_total_human"))}</td>'
                    f'<td class="r">{_e(row.get("data_human"))}</td>'
                    f'<td class="r">{_e(row.get("media_human"))}</td>'
                    f'<td class="r">{_e(row.get("obb_human"))}</td>'
                    f'<td class="r">{_e(row.get("apk_human"))}</td>'
                    f'<td class="r">{_e(row.get("internal_data_human"))}</td></tr>'
                )
            parts.append("</tbody></table></div>")
        residue = app.get("residue") or []
        if residue:
            parts.append('<h3>未安装应用仍保留的外部目录</h3><div class="scroll"><table><thead><tr>'
                         '<th>包名</th><th class="r">外部合计</th><th>说明</th></tr></thead><tbody>')
            for row in residue[:12]:
                parts.append(
                    f'<tr><td><code>{_e(row.get("package"))}</code></td>'
                    f'<td class="r">{_e(row.get("external_total_human"))}</td>'
                    "<td>仅线索；是否清理仍需逐包复核</td></tr>"
                )
            parts.append("</tbody></table></div>")
    limitations = app.get("limitations") or []
    if limitations:
        parts.append('<div class="note"><strong>应用级存储限制</strong><ul>')
        parts += [f"<li>{_e(item)}</li>" for item in limitations]
        parts.append("</ul></div>")
    parts.append("</section>")
    return "".join(parts)


def _diff_section(diff: dict[str, Any]) -> str:
    if not diff:
        return ""
    summary = diff.get("summary") or {}
    parts = ['<section id="diff" class="block">', "<h2>跨轮目录变化</h2>"]
    if diff.get("baseline"):
        parts.append('<p class="lead">本轮已建立目录基线，下一轮开始显示新增、增长、缩小、消失和复活。</p>')
    else:
        parts.append(
            f'<p class="lead">新增 {_num(summary.get("new"))}；增长 {_num(summary.get("grew"))}；'
            f'缩小 {_num(summary.get("shrunk"))}；未再出现 {_num(summary.get("vanished"))}；'
            f'复活 {_num(summary.get("resurrected"))}；疑似残留 {_num(summary.get("orphan_suspect"))}</p>'
        )
    for key, title in (
        ("grew", "增长明显"),
        ("new", "新增目录"),
        ("resurrected", "删后复活"),
        ("orphan_suspect", "疑似卸载残留"),
        ("app_updated", "应用版本变化"),
    ):
        rows = diff.get(key) or []
        if not rows:
            continue
        parts.append(f'<h3>{_e(title)}</h3><div class="scroll"><table><thead><tr>'
                     '<th>路径</th><th class="r">当前大小</th><th>线索</th></tr></thead><tbody>')
        for row in rows[:10]:
            detail = row.get("reason") or (
                f"+{row.get('delta_human') or human_bytes(_num(row.get('delta_bytes')))}"
                if key == "grew" else f"{_num(row.get('files'))} 文件"
            )
            parts.append(
                f'<tr><td><code>{_e(row.get("path"))}</code></td>'
                f'<td class="r">{_e(row.get("human_size") or _size(row.get("bytes")))}</td>'
                f"<td>{_e(detail)}</td></tr>"
            )
        parts.append("</tbody></table></div>")
    parts.append('<div class="note">目录档案只提供线索，不改变风险等级，也不构成删除授权。</div>')
    parts.append("</section>")
    return "".join(parts)


def _candidate_section(items: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    parts = [
        '<section id="candidates" class="block">',
        "<h2>清理候选</h2>",
        f'<p class="lead">共 {_num(summary.get("candidate_groups"))} 组 / '
        f'{_num(summary.get("candidate_files"))} 个文件 / '
        f'{human_bytes(_num(summary.get("candidate_bytes")))}；'
        f'需复核 {_num(summary.get("review_required"))} 组。</p>',
    ]
    categories = summary.get("categories") or []
    if categories:
        parts.append('<div class="scroll"><table><thead><tr><th>分类</th><th class="r">组数</th>'
                     '<th class="r">文件</th><th class="r">逻辑大小</th><th>最高风险</th></tr></thead><tbody>')
        for row in categories:
            risk = str(row.get("risk") or "SAFE")
            parts.append(
                f'<tr><td>{_e(row.get("title"))}</td><td class="r">{_num(row.get("groups"))}</td>'
                f'<td class="r">{_num(row.get("files"))}</td>'
                f'<td class="r">{_size(row.get("bytes"))}</td>'
                f'<td><span class="badge {_RISK_CLASS.get(risk, "caution")}">{_e(risk)}</span></td></tr>'
            )
        parts.append("</tbody></table></div>")

    shown = items[:MAX_ITEMS]
    for item in shown:
        risk = str(item.get("risk") or "CAUTION")
        explain = item.get("explain") or {}
        open_attr = " open" if len(shown) <= 20 else ""
        parts.append(
            f'<details class="cand"{open_attr}>'
            '<summary>'
            f'<span class="badge {_RISK_CLASS.get(risk, "caution")}">{_e(risk)}</span>'
            f'<span class="ctitle">{_e(item.get("title"))}</span>'
            f'<span class="csize">{_e(item.get("human_size"))}</span>'
            "</summary>"
            '<div class="cbody">'
            f'<p class="cid"><code>{_e(item.get("id"))}</code> · {_e(item.get("category_title"))}'
            f' · 动作 {_e(item.get("action"))} · 置信度 {float(item.get("confidence") or 0):.2f}</p>'
        )
        if explain.get("what"):
            parts.append(f'<p><strong>这是什么：</strong>{_e(explain["what"])}</p>')
        if explain.get("why"):
            parts.append(f'<p><strong>为什么列出：</strong>{_e(explain["why"])}</p>')
        if explain.get("impact"):
            parts.append(f'<p><strong>删除影响：</strong>{_e(explain["impact"])}</p>')
        parts.append(
            f'<p class="muted">文件 {_num(item.get("file_count"))} 个，目录 {_num(item.get("dir_count"))} 个，'
            f'逻辑大小 {_e(item.get("human_size"))}；'
            f'{"需要 AI 复核" if item.get("ai_review_required") else "无需强制复核"}；'
            f'{"可选" if item.get("selectable") else "不可选"}。</p>'
        )
        sample_paths = item.get("sample_paths") or []
        if sample_paths:
            parts.append('<p class="muted">路径示例：</p><ul class="paths">')
            parts += [f"<li><code>{_e(path)}</code></li>" for path in sample_paths[:8]]
            parts.append("</ul>")
        evidence = item.get("evidence") or []
        if evidence:
            parts.append('<p class="muted">证据：</p><ul class="ev">')
            parts += [f"<li>{_e(entry.get('text'))}</li>" for entry in evidence[:10]]
            parts.append("</ul>")
        reasons = item.get("reasons") or []
        if reasons:
            parts.append('<p class="muted">判断理由：</p><ul class="ev">')
            parts += [f"<li>{_e(reason)}</li>" for reason in reasons[:6]]
            parts.append("</ul>")
        if item.get("keep_suggestion"):
            parts.append(f'<p><strong>保留建议：</strong>{_e(item["keep_suggestion"])}</p>')
        parts.append("</div></details>")

    if len(items) > len(shown):
        parts.append(f'<div class="note">仅显示前 {len(shown)} 组；完整清单见 analysis.db 与 analysis.json。</div>')
    if not items:
        parts.append('<div class="note">未发现候选。</div>')

    protected = summary.get("protected_items") or []
    if protected:
        parts.append('<h3>受保护内容（不可选，仅展示）</h3><div class="scroll"><table><thead><tr>'
                     '<th>路径</th><th class="r">大小</th><th>原因</th></tr></thead><tbody>')
        for row in protected:
            parts.append(
                f'<tr><td><code>{_e(row.get("path"))}</code></td>'
                f'<td class="r">{_size(row.get("size"))}</td><td>{_e(row.get("reason"))}</td></tr>'
            )
        parts.append("</tbody></table></div>")
    parts.append("</section>")
    return "".join(parts)


def _limitations_section(summary: dict[str, Any], analysis: dict[str, Any]) -> str:
    limitations = list(summary.get("limitations") or analysis.get("limitations") or [])
    parts = ['<section id="limits" class="block">', "<h2>限制与缺口</h2>"]
    if limitations:
        parts.append("<ul>")
        parts += [f"<li>{_e(item)}</li>" for item in limitations]
        parts.append("</ul>")
    else:
        parts.append('<p class="lead">本轮没有额外限制。</p>')
    parts.append(
        '<div class="note"><strong>安全边界</strong><ul>'
        "<li>扫描与分析全程只读；本报告不代表已经删除任何内容。</li>"
        "<li>候选必须经过 AI 复核、用户选择、确认令牌与执行前文件校验。</li>"
        "<li>媒体索引、应用级存储与目录档案只作为证据，不授权删除。</li>"
        "<li>本报告包含设备序列号与文件路径；分享前请自行脱敏。</li>"
        "</ul></div>"
    )
    parts.append("</section>")
    return "".join(parts)


# ---------------------------------------------------------------- cleanup result
_CLEAN_STATUS = {
    "completed": ("已完成", "safe"),
    "partial": ("部分完成", "caution"),
    "aborted": ("已中止", "danger"),
    "purged": ("已永久释放", "safe"),
}
_ITEM_STATUS = {
    "success": ("成功", "safe"),
    "partial": ("部分成功", "caution"),
    "failed": ("失败", "danger"),
    "aborted": ("已中止", "danger"),
}
_MODE_LABEL = {"quarantine": "隔离（可恢复）", "delete": "永久删除"}


def _signed_size(value: Any) -> str:
    number = _num(value)
    return ("+" if number >= 0 else "-") + human_bytes(abs(number))


def _cards(pairs: list[tuple[str, Any]]) -> str:
    body = "".join(
        f'<div class="card"><div class="k">{_e(label)}</div><div class="v">{_e(value)}</div></div>'
        for label, value in pairs
    )
    return f'<div class="grid stats">{body}</div>'


def render_cleanup_html(
    analysis: dict[str, Any] | None,
    selection: dict[str, Any] | None,
    execution: dict[str, Any],
    verification: dict[str, Any] | None = None,
) -> str:
    """Render the post-clean archive from already-recorded state.

    Pure rendering: no device access, no external assets.  It reports what
    actually happened and never upgrades a failed or unverified step.
    """
    analysis = analysis or {}
    selection = selection or {}
    verification = verification or {}
    summary = analysis.get("summary") or {}
    device = summary.get("device") or {}
    identity = execution.get("device_identity") or {}
    ex = execution.get("summary") or {}
    sel = selection.get("summary") or {}
    verify = verification.get("summary") or {}
    purge = execution.get("purge") or {}
    restore = execution.get("restore") or {}
    status = str(execution.get("status") or "")
    status_label, status_class = _CLEAN_STATUS.get(status, (status or "-", "caution"))
    mode = str(execution.get("mode") or selection.get("mode") or "")

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        "<title>清理结果报告</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<main class="wrap">',
    ]

    parts.append(
        '<header class="hero">'
        "<h1>清理结果报告</h1>"
        f'<p class="sub"><span class="badge {status_class}">{_e(status_label)}</span> '
        f"模式：{_e(_MODE_LABEL.get(mode, mode or '-'))} · 本报告记录已经发生的操作，不是建议清单</p>"
        "</header>"
    )

    model = " ".join(
        value for value in (str(device.get("manufacturer") or ""), str(device.get("model") or "")) if value
    ).strip()
    parts.append(
        _cards([
            ("设备", model or identity.get("serial") or "-"),
            ("平台", execution.get("platform") or analysis.get("platform") or "-"),
            ("传输方式", execution.get("transport") or "-"),
            ("执行 ID", execution.get("execution_id") or "-"),
            ("开始", execution.get("started_at") or "-"),
            ("结束", execution.get("finished_at") or "-"),
            ("验证", "已运行" if verify else "未运行"),
            ("隔离区", execution.get("quarantine_root") or "-"),
        ])
    )

    cards: list[tuple[str, Any]] = [
        ("用户选择", f"{_num(sel.get('groups'))} 组 / {_num(sel.get('files'))} 个"),
        ("成功文件", f"{_num(ex.get('success_files'))} 个"),
        ("失败文件", f"{_num(ex.get('failed_files'))} 个"),
        ("处理逻辑大小", human_bytes(_num(ex.get('processed_bytes')))),
        ("已释放", human_bytes(_num(ex.get('released_bytes')))),
        ("隔离区占用", human_bytes(_num(
            (execution.get("quarantine_state") or {}).get("remaining_bytes", ex.get("quarantine_occupied_bytes"))
        ))),
        ("可用空间变化", _signed_size(ex.get("df_delta_bytes"))),
    ]
    if verify:
        cards.append(("验证通过", f"{_num(verify.get('passed'))} / {_num(verify.get('checked'))}"))
    if purge:
        cards.append(("永久删除（累计）", f"{_num(purge.get('purged_files'))} 个 / {human_bytes(_num(purge.get('purged_bytes')))}"))
    if restore:
        cards.append(("已恢复（累计）", f"{_num(restore.get('restored_files'))} 个 / {human_bytes(_num(restore.get('restored_bytes')))}"))
    parts.append(_cards(cards))

    parts.append(_cleanup_process(summary, sel, execution, verify, purge, restore, mode))
    parts.append(_cleanup_selection(selection, mode))
    parts.append(_cleanup_execution(execution, selection))
    parts.append(_cleanup_verification(verification))
    parts.append(_cleanup_release(execution, verification))
    parts.append(_cleanup_background(summary))
    parts.append(_cleanup_next(execution, verification))
    parts.append(
        '<footer class="foot">'
        "本报告由 android-deep-clean 生成，记录一次已经执行的操作。"
        "本报告包含设备序列号与文件路径，分享前请自行脱敏。"
        "</footer>"
    )
    parts += ["</main>", "</body>", "</html>", ""]
    return "\n".join(parts)


def write_cleanup_html(
    analysis: dict[str, Any] | None,
    selection: dict[str, Any] | None,
    execution: dict[str, Any],
    verification: dict[str, Any] | None,
    path: Path,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_cleanup_html(analysis, selection, execution, verification), encoding="utf-8")
    return path


def _cleanup_process(
    summary: dict[str, Any],
    sel: dict[str, Any],
    execution: dict[str, Any],
    verify: dict[str, Any],
    purge: dict[str, Any],
    restore: dict[str, Any],
    mode: str,
) -> str:
    ex = execution.get("summary") or {}
    steps: list[tuple[str, str]] = [
        (
            "清理前分析",
            f"{_num(summary.get('candidate_groups'))} 组候选 / {_num(summary.get('candidate_files'))} 个文件 / "
            f"{human_bytes(_num(summary.get('candidate_bytes')))}；需要复核 {_num(summary.get('review_required'))} 组。",
        ),
        (
            "用户选择",
            f"{_num(sel.get('groups'))} 组 / {_num(sel.get('files'))} 个文件 / "
            f"{sel.get('human_bytes') or human_bytes(_num(sel.get('bytes')))}；"
            f"模式：{_MODE_LABEL.get(mode, mode or '-')}。",
        ),
        (
            "执行",
            f"成功 {_num(ex.get('success_files'))} 个，失败 {_num(ex.get('failed_files'))} 个；"
            f"处理逻辑大小 {human_bytes(_num(ex.get('processed_bytes')))}。",
        ),
    ]
    if verify:
        checked = _num(verify.get("checked"))
        if checked <= 0:
            steps.append(("验证", "没有可验证项目（记录已全部 purge 或恢复）；这不等于验证通过。"))
        else:
            steps.append((
                "验证",
                f"检查 {checked}，通过 {_num(verify.get('passed'))}，"
                f"失败 {_num(verify.get('failed'))}。",
            ))
    else:
        steps.append(("验证", "尚未运行 verify；当前执行结果还没有经过独立核对。"))
    if purge:
        purge_runs = [item for item in purge.get("runs") or [] if isinstance(item, dict)]
        purge_last = purge.get("last_run") if isinstance(purge.get("last_run"), dict) else purge
        detail = (
            f"永久删除 {_num(purge.get('purged_files'))} 个 / "
            f"{human_bytes(_num(purge.get('purged_bytes')))}（累计）"
        )
        if len(purge_runs) > 1:
            detail += f"；最近一次删除 {_num(purge_last.get('purged_files'))} 个"
        detail += f"；可用空间变化 {_signed_size(purge_last.get('free_delta'))}。"
        steps.append(("释放空间", detail))
    if restore:
        restore_runs = [item for item in restore.get("runs") or [] if isinstance(item, dict)]
        restore_last = restore.get("last_run") if isinstance(restore.get("last_run"), dict) else restore
        detail = (
            f"已恢复 {_num(restore.get('restored_files'))} 个 / "
            f"{human_bytes(_num(restore.get('restored_bytes')))}（累计）"
        )
        if len(restore_runs) > 1:
            detail += f"；最近一次恢复 {_num(restore_last.get('restored_files'))} 个"
        detail += f"；失败 {_num(restore.get('failed'))}。"
        steps.append(("恢复文件", detail))
    if not purge and not restore and mode == "quarantine":
        steps.append(("空间状态", "隔离区仍占用设备空间；尚未 purge 时 released_bytes=0 是正确结果。"))
    rows = "".join(
        f'<li><span class="stepnum">{index}</span><div><strong>{_e(title)}</strong><p>{_e(detail)}</p></div></li>'
        for index, (title, detail) in enumerate(steps, start=1)
    )
    return (
        '<section id="process" class="block"><h2>清理过程</h2>'
        f'<ol class="steps">{rows}</ol></section>'
    )


def _cleanup_selection(selection: dict[str, Any], mode: str) -> str:
    items = selection.get("items") or []
    summary = selection.get("summary") or {}
    parts = [
        '<section id="selection" class="block">',
        "<h2>用户选择了什么</h2>",
        f'<p class="lead">模式：{_e(_MODE_LABEL.get(mode, mode or "-"))}；'
        f'共 {_num(summary.get("groups"))} 组 / {_num(summary.get("files"))} 个文件 / '
        f'{_e(summary.get("human_bytes") or human_bytes(_num(summary.get("bytes"))))}。</p>',
    ]
    if not items:
        parts.append('<div class="note">没有找到选择清单；本报告只能依据执行记录还原范围。</div>')
    else:
        parts.append('<div class="scroll"><table><thead><tr>'
                     '<th>候选</th><th>分类</th><th>风险</th><th>动作</th>'
                     '<th class="r">文件</th><th class="r">逻辑大小</th></tr></thead><tbody>')
        for item in items[:200]:
            risk = str(item.get("risk") or "CAUTION")
            parts.append(
                f'<tr><td>{_e(item.get("title") or item.get("id"))}</td>'
                f'<td>{_e(item.get("category_title") or item.get("category"))}</td>'
                f'<td><span class="badge {_RISK_CLASS.get(risk, "caution")}">{_e(risk)}</span></td>'
                f'<td><code>{_e(item.get("action"))}</code></td>'
                f'<td class="r">{_num(item.get("file_count"))}</td>'
                f'<td class="r">{_e(item.get("human_size") or human_bytes(_num(item.get("total_bytes"))))}</td></tr>'
            )
        parts.append("</tbody></table></div>")
        if len(items) > 200:
            parts.append(f'<div class="note">仅显示前 200 组；完整选择清单见 selection.json。</div>')
    parts.append("</section>")
    return "".join(parts)


def _cleanup_execution(execution: dict[str, Any], selection: dict[str, Any]) -> str:
    titles = {
        item.get("id"): item.get("title") or item.get("id")
        for item in selection.get("items") or []
    }
    results = execution.get("items") or []
    failures = execution.get("failures") or []
    parts = ['<section id="execution" class="block">', "<h2>实际执行了什么</h2>"]
    if not results:
        parts.append('<div class="note">执行记录中没有项目明细。</div>')
    else:
        parts.append('<div class="scroll"><table><thead><tr>'
                     '<th>候选</th><th>分类</th><th>状态</th><th class="r">成功</th>'
                     '<th class="r">失败</th><th class="r">处理大小</th><th>说明</th></tr></thead><tbody>')
        for item in results[:200]:
            item_status = str(item.get("status") or "")
            label, css = _ITEM_STATUS.get(item_status, (item_status or "-", "caution"))
            parts.append(
                f'<tr><td>{_e(titles.get(item.get("id")) or item.get("id"))}</td>'
                f'<td>{_e(item.get("category"))}</td>'
                f'<td><span class="badge {css}">{_e(label)}</span></td>'
                f'<td class="r">{_num(item.get("success_count"))}</td>'
                f'<td class="r">{_num(item.get("failed_count"))}</td>'
                f'<td class="r">{human_bytes(_num(item.get("processed_bytes")))}</td>'
                f'<td>{_e(item.get("error") or "-")}</td></tr>'
            )
        parts.append("</tbody></table></div>")
        if len(results) > 200:
            parts.append('<div class="note">仅显示前 200 组；完整明细见 execution.json。</div>')

    if execution.get("abort_reason"):
        parts.append(
            f'<div class="note warn"><strong>执行中止</strong><p>{_e(execution.get("abort_reason"))}。'
            "未执行的项目保持原状。</p></div>"
        )
    if failures:
        parts.append('<h3>失败项目（未删除，仍留在原位置）</h3>'
                     '<div class="scroll"><table><thead><tr>'
                     '<th>路径</th><th>原因</th><th>候选</th></tr></thead><tbody>')
        for failure in failures[:50]:
            parts.append(
                f'<tr><td><code>{_e(failure.get("path"))}</code></td>'
                f'<td>{_e(failure.get("reason"))}</td>'
                f'<td>{_e(titles.get(failure.get("item")) or failure.get("item") or "-")}</td></tr>'
            )
        parts.append("</tbody></table></div>")
        if len(failures) > 50:
            parts.append(f'<div class="note">另有 {len(failures) - 50} 条失败记录，见 execution.json。</div>')
    elif not execution.get("abort_reason"):
        parts.append('<div class="note">本次执行没有文件级失败。</div>')
    parts.append("</section>")
    return "".join(parts)


def _cleanup_verification(verification: dict[str, Any]) -> str:
    summary = verification.get("summary") or {}
    parts = ['<section id="verify" class="block">', "<h2>验证结果</h2>"]
    if not verification:
        parts.append(
            '<div class="note">本次执行后尚未运行 verify。'
            "建议运行 <code>verify</code>，确认源文件已消失、隔离文件仍然存在且大小一致。</div>"
        )
        parts.append("</section>")
        return "".join(parts)
    hash_verified = _num(summary.get("hash_verified"))
    hash_unavailable = _num(summary.get("hash_unavailable"))
    hash_incomplete = _num(summary.get("hash_incomplete"))
    cards = [
        ("检查", f"{_num(summary.get('checked'))}"),
        ("通过", f"{_num(summary.get('passed'))}"),
        ("失败", f"{_num(summary.get('failed'))}"),
        ("源文件仍存在", f"{_num(summary.get('source_present'))}"),
        ("隔离文件缺失", f"{_num(summary.get('quarantine_missing'))}"),
        ("可用空间变化", _signed_size(summary.get("free_delta"))),
    ]
    if hash_verified or hash_unavailable or hash_incomplete:
        cards += [
            ("内容校验通过", f"{hash_verified}"),
            ("仅大小校验", f"{hash_unavailable}"),
        ]
        if hash_incomplete:
            cards.append(("校验未完成", f"{hash_incomplete}"))
    parts.append(_cards(cards))
    failures = verification.get("failures") or []
    checked = _num(summary.get("checked"))
    if failures:
        parts.append('<h3>验证异常</h3><div class="scroll"><table><thead><tr>'
                     '<th>路径</th><th>原因</th></tr></thead><tbody>')
        for failure in failures[:50]:
            parts.append(
                f'<tr><td><code>{_e(failure.get("path"))}</code></td>'
                f'<td>{_e(failure.get("reason"))}</td></tr>'
            )
        parts.append("</tbody></table></div>")
        if len(failures) > 50:
            parts.append(f'<div class="note">另有 {len(failures) - 50} 条，见 verification.json。</div>')
    elif checked <= 0:
        parts.append(
            '<div class="note">没有可验证项目：本次没有需要检查的隔离或删除记录'
            "（例如相关文件已全部 purge 或恢复）。这不等于验证通过。</div>"
        )
    else:
        if hash_unavailable:
            parts.append(
                f'<div class="note">所有已执行检查项通过；其中 {hash_unavailable} 个文件仅校验大小，'
                "未做内容校验。</div>"
            )
        else:
            parts.append('<div class="note">所有检查项通过。</div>')
    if hash_unavailable:
        parts.append(
            f'<div class="note">有 {hash_unavailable} 个隔离文件因设备缺少 SHA-256/MD5 工具，'
            "仅校验了大小，未做内容校验。</div>"
        )
    if hash_incomplete:
        parts.append(
            f'<div class="note warn">有 {hash_incomplete} 个隔离文件完整性校验未完成，已按失败处理。</div>'
        )
    if verification.get("realpath_baseline") == "current":
        parts.append(
            '<div class="note">旧 execution.json 未记录隔离区真实路径基线；本次以当前解析结果为准。</div>'
        )
    if verification.get("note"):
        parts.append(f'<div class="note">{_e(verification.get("note"))}</div>')
    parts.append("</section>")
    return "".join(parts)


def _run_failure_details(title: str, runs: list[dict[str, Any]]) -> str:
    """Collapsible failure details for earlier purge/restore runs."""
    blocks: list[str] = []
    for index, run in enumerate(runs, start=1):
        failures = run.get("failures") or []
        if not failures:
            continue
        blocks.append(
            '<details class="cand"><summary>'
            f'<span class="ctitle">{_e(title)} 第 {index} 次 · {_e(run.get("at") or "-")}</span>'
            f'<span class="csize">{len(failures)} 条失败</span>'
            '</summary><div class="cbody">'
            "<table><thead><tr><th>路径</th><th>原因</th></tr></thead><tbody>"
        )
        for failure in failures[:50]:
            blocks.append(
                f'<tr><td><code>{_e(failure.get("path"))}</code></td>'
                f'<td>{_e(failure.get("reason"))}</td></tr>'
            )
        blocks.append("</tbody></table>")
        if len(failures) > 50:
            blocks.append(f'<div class="note">仅显示前 50 条；共 {len(failures)} 条。</div>')
        blocks.append("</div></details>")
    if not blocks:
        return ""
    return f'<h3>{_e(title)} 历史失败明细</h3>' + "".join(blocks)


def _cleanup_release(execution: dict[str, Any], verification: dict[str, Any]) -> str:
    mode = str(execution.get("mode") or "")
    purge = execution.get("purge") or {}
    restore = execution.get("restore") or {}
    ex = execution.get("summary") or {}
    qstate = execution.get("quarantine_state") or {}
    remaining_files = qstate.get("remaining_files")
    remaining_bytes = qstate.get("remaining_bytes")
    if remaining_files is None:
        remaining_files = ex.get("quarantine_files")
    if remaining_bytes is None:
        remaining_bytes = ex.get("quarantine_bytes")
    parts = ['<section id="release" class="block">', "<h2>隔离区、释放与恢复</h2>"]

    if mode == "quarantine":
        parts.append(
            _cards([
                ("隔离区", execution.get("quarantine_root") or "-"),
                ("隔离文件", f"{_num(remaining_files)} 个"),
                ("隔离占用", human_bytes(_num(remaining_bytes))),
                ("电脑备份", f"{_num(ex.get('backup_files'))} 个 / {human_bytes(_num(ex.get('backup_bytes')))}"),
            ])
        )
        if qstate and _num(remaining_files) == 0:
            parts.append(
                '<div class="note">隔离区已清空：本次记录中的隔离文件均已 purge 或恢复。</div>'
            )
        elif purge:
            parts.append(
                f'<div class="note">隔离区仍有 {_num(remaining_files)} 个文件'
                f"（{human_bytes(_num(remaining_bytes))}）占用空间；已 purge 的文件无法恢复。</div>"
            )
        else:
            parts.append(
                '<div class="note">隔离区仍位于手机上，会继续占用空间；它不是电脑备份。'
                "确认无误后再 purge。</div>"
            )
    else:
        parts.append(
            '<div class="note warn">本次使用永久删除模式，文件不可恢复；'
            "released_bytes 是删除文件逻辑大小的估算。</div>"
        )

    if purge:
        purge_runs = [item for item in purge.get("runs") or [] if isinstance(item, dict)]
        purge_last = purge.get("last_run") if isinstance(purge.get("last_run"), dict) else purge
        cards = [
            ("永久删除（累计）", f"{_num(purge.get('purged_files'))} 个"),
            ("删除逻辑大小", human_bytes(_num(purge.get("purged_bytes")))),
        ]
        if len(purge_runs) > 1:
            cards.append(("最近一次删除", f"{_num(purge_last.get('purged_files'))} 个"))
        cards += [
            ("失败", f"{_num(purge.get('failed'))}"),
            ("可用空间变化", _signed_size(purge_last.get("free_delta"))),
        ]
        parts.append('<h3>永久释放（purge）</h3>')
        parts.append(_cards(cards))
        if _num(purge.get("missing_files")):
            parts.append(
                f'<div class="note">另有 {_num(purge.get("missing_files"))} 个隔离文件在 purge 前已不存在，'
                "标记为 missing，未计入永久删除。</div>"
            )
        if _num(purge.get("hash_unavailable_files")):
            parts.append(
                f'<div class="note">有 {_num(purge.get("hash_unavailable_files"))} 个隔离文件'
                "因设备缺少哈希工具，仅校验了大小，未做内容校验。</div>"
            )
        if _num(purge.get("hash_incomplete_files")):
            parts.append(
                f'<div class="note warn">有 {_num(purge.get("hash_incomplete_files"))} 个隔离文件'
                "完整性校验未完成，已拒绝操作。</div>"
            )
        if purge.get("realpath_baseline") == "current":
            parts.append(
                '<div class="note">旧 execution.json 未记录隔离区真实路径基线；本次以当前解析结果为准。</div>'
            )
        if len(purge_runs) > 1:
            parts.append('<h3>purge 执行记录</h3><div class="scroll"><table><thead><tr>'
                         '<th>时间</th><th class="r">删除</th><th class="r">逻辑大小</th>'
                         '<th class="r">失败</th></tr></thead><tbody>')
            for item in purge_runs:
                parts.append(
                    f'<tr><td>{_e(item.get("at") or "-")}</td>'
                    f'<td class="r">{_num(item.get("purged_files"))}</td>'
                    f'<td class="r">{human_bytes(_num(item.get("purged_bytes")))}</td>'
                    f'<td class="r">{_num(item.get("failed"))}</td></tr>'
                )
            parts.append("</tbody></table></div>")
        purge_failures = purge.get("failures") or []
        if purge_failures:
            parts.append('<div class="scroll"><table><thead><tr>'
                         '<th>隔离路径</th><th>原因</th></tr></thead><tbody>')
            for failure in purge_failures[:30]:
                parts.append(
                    f'<tr><td><code>{_e(failure.get("path"))}</code></td>'
                    f'<td>{_e(failure.get("reason"))}</td></tr>'
                )
            parts.append("</tbody></table></div>")
            if len(purge_failures) > 30:
                parts.append(f'<div class="note">另有 {len(purge_failures) - 30} 条，见 execution.json。</div>')
        if len(purge_runs) > 1:
            parts.append(_run_failure_details("purge", purge_runs[:-1]))
    elif mode == "quarantine" and _num(remaining_files) > 0:
        parts.append(
            '<div class="note">尚未 purge：隔离文件仍占用设备空间，'
            f"released_bytes={human_bytes(_num(ex.get('released_bytes')))} 是正确结果。</div>"
        )

    if restore:
        restore_runs = [item for item in restore.get("runs") or [] if isinstance(item, dict)]
        restore_last = restore.get("last_run") if isinstance(restore.get("last_run"), dict) else restore
        cards = [
            ("已恢复（累计）", f"{_num(restore.get('restored_files'))} 个"),
            ("恢复逻辑大小", human_bytes(_num(restore.get("restored_bytes")))),
        ]
        if len(restore_runs) > 1:
            cards.append(("最近一次恢复", f"{_num(restore_last.get('restored_files'))} 个"))
        cards.append(("失败", f"{_num(restore.get('failed'))}"))
        parts.append('<h3>恢复（restore）</h3>')
        parts.append(_cards(cards))
        if _num(restore.get("hash_unavailable_files")):
            parts.append(
                f'<div class="note">有 {_num(restore.get("hash_unavailable_files"))} 个隔离文件'
                "因设备缺少哈希工具，仅校验了大小，未做内容校验。</div>"
            )
        if _num(restore.get("hash_incomplete_files")):
            parts.append(
                f'<div class="note warn">有 {_num(restore.get("hash_incomplete_files"))} 个隔离文件'
                "完整性校验未完成，已拒绝操作。</div>"
            )
        if restore.get("realpath_baseline") == "current":
            parts.append(
                '<div class="note">旧 execution.json 未记录隔离区真实路径基线；本次以当前解析结果为准。</div>'
            )
        if len(restore_runs) > 1:
            parts.append('<h3>restore 执行记录</h3><div class="scroll"><table><thead><tr>'
                         '<th>时间</th><th class="r">恢复</th><th class="r">逻辑大小</th>'
                         '<th class="r">失败</th></tr></thead><tbody>')
            for item in restore_runs:
                parts.append(
                    f'<tr><td>{_e(item.get("at") or "-")}</td>'
                    f'<td class="r">{_num(item.get("restored_files"))}</td>'
                    f'<td class="r">{human_bytes(_num(item.get("restored_bytes")))}</td>'
                    f'<td class="r">{_num(item.get("failed"))}</td></tr>'
                )
            parts.append("</tbody></table></div>")
        restore_failures = restore.get("failures") or []
        if restore_failures:
            parts.append('<div class="scroll"><table><thead><tr>'
                         '<th>路径</th><th>原因</th></tr></thead><tbody>')
            for failure in restore_failures[:30]:
                parts.append(
                    f'<tr><td><code>{_e(failure.get("path"))}</code></td>'
                    f'<td>{_e(failure.get("reason"))}</td></tr>'
                )
            parts.append("</tbody></table></div>")
            if len(restore_failures) > 30:
                parts.append(f'<div class="note">另有 {len(restore_failures) - 30} 条，见 execution.json。</div>')
        if len(restore_runs) > 1:
            parts.append(_run_failure_details("restore", restore_runs[:-1]))
    elif mode == "quarantine" and not purge and _num(remaining_files) > 0:
        parts.append('<div class="note">如需恢复，可运行 <code>restore --all</code> 或按候选 ID 恢复。</div>')

    free_rows: list[tuple[str, str]] = []
    for label, data in (
        ("执行前可用空间", execution.get("free_before")),
        ("执行后可用空间", execution.get("free_after")),
        ("验证时可用空间", verification.get("free_now")),
    ):
        if isinstance(data, dict) and data.get("available_bytes") is not None:
            free_rows.append((label, human_bytes(_num(data.get("available_bytes")))))
    if free_rows:
        free_rows.append(("执行期间变化", _signed_size(ex.get("df_delta_bytes"))))
        if purge:
            purge_last = purge.get("last_run") if isinstance(purge.get("last_run"), dict) else purge
            free_rows.append(("purge 后变化", _signed_size(purge_last.get("free_delta"))))
        parts.append('<h3>可用空间记录</h3><div class="scroll"><table><thead><tr>'
                     '<th>时间点</th><th class="r">可用空间</th></tr></thead><tbody>')
        for label, value in free_rows:
            parts.append(f'<tr><td>{_e(label)}</td><td class="r">{_e(value)}</td></tr>')
        parts.append("</tbody></table></div>")
        parts.append('<div class="note">可用空间受系统写入、日志与缓存影响，短时间内的负变化不一定是清理失败。</div>')
    parts.append("</section>")
    return "".join(parts)


def _cleanup_background(summary: dict[str, Any]) -> str:
    categories = summary.get("categories") or []
    parts = ['<section id="background" class="block">', "<h2>清理前发现了什么</h2>"]
    parts.append(
        f'<p class="lead">共 {_num(summary.get("candidate_groups"))} 组 / '
        f'{_num(summary.get("candidate_files"))} 个文件 / '
        f'{human_bytes(_num(summary.get("candidate_bytes")))}；'
        f'需复核 {_num(summary.get("review_required"))} 组。</p>'
    )
    if categories:
        parts.append('<div class="scroll"><table><thead><tr><th>分类</th><th class="r">组数</th>'
                     '<th class="r">文件</th><th class="r">逻辑大小</th><th>最高风险</th></tr></thead><tbody>')
        for row in categories:
            risk = str(row.get("risk") or "SAFE")
            parts.append(
                f'<tr><td>{_e(row.get("title"))}</td><td class="r">{_num(row.get("groups"))}</td>'
                f'<td class="r">{_num(row.get("files"))}</td>'
                f'<td class="r">{human_bytes(_num(row.get("bytes")))}</td>'
                f'<td><span class="badge {_RISK_CLASS.get(risk, "caution")}">{_e(risk)}</span></td></tr>'
            )
        parts.append("</tbody></table></div>")
    else:
        parts.append('<div class="note">没有找到清理前分析摘要；本报告只依据执行记录。</div>')
    parts.append('<div class="note">完整的清理前报告见 <code>analysis/report-before.html</code> 或 '
                 "<code>analysis/report-before.md</code>。</div>")
    parts.append("</section>")
    return "".join(parts)


def _cleanup_next(execution: dict[str, Any], verification: dict[str, Any]) -> str:
    ex = execution.get("summary") or {}
    verify = verification.get("summary") or {}
    mode = str(execution.get("mode") or "")
    purge = execution.get("purge") or {}
    restore = execution.get("restore") or {}
    qstate = execution.get("quarantine_state") or {}
    remaining = qstate.get("remaining_files")
    parts = ['<section id="next" class="block">', "<h2>剩余问题与下一步</h2>"]
    items: list[str] = []
    if _num(ex.get("failed_files")):
        items.append(f"有 {_num(ex.get('failed_files'))} 个文件失败，仍留在原位置；请查看失败原因。")
    if _num(verify.get("failed")):
        items.append(f"验证有 {_num(verify.get('failed'))} 项异常，请先确认再继续清理或释放。")
    if execution.get("abort_reason"):
        items.append("执行曾经中止，未执行的项目保持原状。")
    if mode == "quarantine" and not purge and (remaining is None or _num(remaining) > 0):
        items.append("隔离区仍占用设备空间；确认无需恢复后再运行 quarantine purge。")
    if purge:
        items.append("已永久删除的隔离文件无法恢复；restore 只会恢复仍留在隔离区的文件。")
    if restore:
        if remaining is not None and _num(remaining) == 0:
            items.append(
                f"已累计恢复 {_num(restore.get('restored_files'))} 个文件；"
                "本次记录中的隔离文件已全部回到原路径或已永久删除。"
            )
        else:
            items.append(
                f"已累计恢复 {_num(restore.get('restored_files'))} 个文件；"
                "隔离区剩余文件可继续恢复或 purge。"
            )
    if not items:
        items.append("本轮没有发现需要额外处理的问题。")
    parts.append("<ul>")
    parts += [f"<li>{_e(item)}</li>" for item in items]
    parts.append("</ul>")

    warnings = execution.get("warnings") or []
    if warnings:
        parts.append('<div class="note warn"><strong>执行注意事项</strong><ul>')
        parts += [f"<li>{_e(item)}</li>" for item in warnings]
        parts.append("</ul></div>")

    next_actions = [str(item) for item in (execution.get("next_actions") or [])]
    if remaining is not None and _num(remaining) == 0:
        # Nothing is left in quarantine; stale "purge/restore" suggestions
        # would contradict the current state.
        next_actions = [
            item for item in next_actions
            if "purge" not in item.lower() and "restore" not in item.lower()
        ]
    if next_actions:
        parts.append('<div class="note"><strong>建议的后续命令</strong><ul>')
        parts += [f"<li>{_e(item)}</li>" for item in next_actions]
        parts.append("</ul></div>")

    parts.append(
        '<div class="note"><strong>安全说明</strong><ul>'
        "<li>本报告只记录已经发生的操作，不会触发新的删除、移动或恢复。</li>"
        "<li>失败项目没有静默忽略；未验证的步骤不会显示为通过。</li>"
        "<li>隔离文件仍在手机上；电脑备份（如有）是独立副本，不是隔离区。</li>"
        "<li>本报告包含设备序列号与文件路径；分享前请自行脱敏。</li>"
        "</ul></div>"
    )
    parts.append("</section>")
    return "".join(parts)



_CSS = """
:root{
--bg:#fff;--surface:#fff;--surface-2:#f5f5f7;
--ink:#1d1d1f;--ink-soft:#3a3a3c;--muted:#6e6e73;
--line:#e8e8ed;--line-strong:#d2d2d7;
--accent:#0071e3;--accent-ink:#0066cc;
--safe-bg:#e9f6ef;--safe-ink:#1d7a4a;
--caution-bg:#fff4e5;--caution-ink:#8a5a00;
--danger-bg:#fdeceb;--danger-ink:#a8322a;
--known:#1d1d1f;--unknown:#e8e8ed;
--glass:rgba(255,255,255,.72);--glass-edge:rgba(0,0,0,.08);--fallback:#fff;
--shadow:0 1px 2px rgba(0,0,0,.04),0 8px 24px -18px rgba(0,0,0,.18);
--shadow-hover:0 2px 4px rgba(0,0,0,.05),0 16px 36px -22px rgba(0,0,0,.22);
--radius:20px
}
*{box-sizing:border-box}
body{margin:0;background:#fff;color:var(--ink);min-height:100vh;
font:15px/1.6 -apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Helvetica Neue","Segoe UI","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.wrap{max-width:1040px;margin:0 auto;padding:56px 24px 96px}
.hero{padding:0 0 32px;margin:0 0 28px;border-bottom:1px solid var(--line)}
h1{font-size:44px;line-height:1.08;letter-spacing:-.015em;font-weight:600;margin:0 0 14px;color:var(--ink)}
.sub{margin:0;color:var(--muted);font-size:17px;letter-spacing:-.01em}
h2{font-size:24px;line-height:1.2;letter-spacing:-.015em;font-weight:600;margin:0 0 14px}
h3{font-size:15px;letter-spacing:-.01em;font-weight:600;color:var(--ink-soft);margin:26px 0 10px}
.block{background:#fff;border:1px solid var(--line);border-radius:var(--radius);
padding:28px 30px;margin:20px 0;scroll-margin-top:88px;box-shadow:var(--shadow)}
.lead{margin:0 0 16px;color:var(--ink-soft);font-size:15.5px}
.grid{display:grid;gap:12px}
.grid.meta{grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin:20px 0 4px}
.grid.stats{grid-template-columns:repeat(auto-fit,minmax(140px,1fr));margin:14px 0}
.card{background:var(--surface-2);border:1px solid transparent;border-radius:14px;padding:14px 16px;
transition:transform .18s ease,box-shadow .18s ease,background .18s ease,border-color .18s ease}
.card:hover{background:#fff;border-color:var(--line);transform:translateY(-1px);box-shadow:var(--shadow-hover)}
.card .k{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);font-weight:500}
.card .v{font-weight:600;font-size:16px;letter-spacing:-.01em;margin-top:2px;word-break:break-all}
.toc{position:sticky;top:16px;z-index:30;display:flex;flex-wrap:wrap;gap:4px;
margin:0 0 12px;padding:6px;border-radius:999px;
background:var(--glass);border:1px solid var(--glass-edge);
-webkit-backdrop-filter:saturate(180%) blur(20px);backdrop-filter:saturate(180%) blur(20px);
box-shadow:0 4px 20px -14px rgba(0,0,0,.25)}
.toc a{color:var(--muted);text-decoration:none;padding:6px 13px;border-radius:999px;font-size:13px;
transition:background .15s ease,color .15s ease}
.toc a:hover{background:var(--surface-2);color:var(--ink)}
.scroll{overflow-x:auto;border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:4px 0}
th,td{border-bottom:1px solid var(--line);padding:11px 10px;text-align:left;vertical-align:top}
th{font-size:11.5px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);
white-space:nowrap;background:transparent}
tbody tr:last-child td{border-bottom:0}
tbody tr{transition:background .15s ease}
tbody tr:hover{background:#fafafc}
td.r,th.r{text-align:right;white-space:nowrap}
code{font:12.5px/1.5 ui-monospace,SFMono-Regular,"SF Mono",Consolas,monospace;
background:var(--surface-2);padding:2px 6px;border-radius:6px;color:var(--ink-soft);word-break:break-all}
.bar{display:flex;height:10px;border-radius:999px;overflow:hidden;background:var(--unknown);margin:14px 0}
.bar-known{background:var(--known);border-radius:999px 0 0 999px}
.bar-unknown{background:var(--unknown);border-radius:0 999px 999px 0}
.legend{display:flex;flex-wrap:wrap;gap:22px;font-size:13px;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:7px}
.dot.known{background:var(--known)}
.dot.unknown{background:var(--unknown)}
.note{background:var(--surface-2);border:1px solid transparent;border-radius:14px;
padding:14px 16px;margin-top:16px;font-size:13.5px;color:var(--ink-soft)}
.note.warn{background:var(--surface-2);border-left:3px solid #e0a63a;border-radius:6px 14px 14px 6px}
.note ul{margin:6px 0 0;padding-left:20px}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:3px 9px;border-radius:999px;
letter-spacing:.01em;border:1px solid transparent}
.badge.safe{background:var(--safe-bg);color:var(--safe-ink)}
.badge.caution{background:var(--caution-bg);color:var(--caution-ink)}
.badge.danger{background:var(--danger-bg);color:var(--danger-ink)}
details.cand{background:#fff;border:1px solid var(--line);border-radius:14px;padding:0;margin:10px 0;
transition:box-shadow .18s ease,border-color .18s ease}
details.cand:hover{box-shadow:var(--shadow)}
details.cand[open]{border-color:var(--line-strong);box-shadow:var(--shadow)}
details.cand>summary{cursor:pointer;display:flex;align-items:center;gap:10px;padding:14px 18px;
list-style:none;border-radius:13px;transition:background .15s ease}
details.cand>summary:hover{background:#fafafc}
details.cand>summary::-webkit-details-marker{display:none}
details.cand>summary::before{content:"›";color:var(--muted);font-size:16px;line-height:1;
transition:transform .18s ease}
details.cand[open]>summary::before{transform:rotate(90deg)}
.ctitle{flex:1;font-weight:600;letter-spacing:-.01em;word-break:break-all}
.csize{color:var(--muted);font-size:13px;white-space:nowrap}
.cbody{padding:6px 18px 18px;border-top:1px solid var(--line);overflow-x:auto}
.cbody p{margin:8px 0}
.cid{color:var(--muted);font-size:12.5px}
.muted{color:var(--muted);font-size:13px;margin:10px 0 4px}
ul.paths,ul.ev{margin:4px 0 8px;padding-left:20px;font-size:13px}
ul.ev li{margin:2px 0}
.steps{list-style:none;margin:6px 0 0;padding:0}
.steps li{display:flex;gap:14px;padding:14px 0;border-bottom:1px solid var(--line)}
.steps li:last-child{border-bottom:0}
.stepnum{flex:0 0 24px;height:24px;border-radius:50%;background:var(--ink);color:#fff;
font-size:12px;font-weight:600;display:flex;align-items:center;justify-content:center}
.steps p{margin:2px 0 0;color:var(--muted);font-size:13px}
.foot{color:var(--muted);font-size:12.5px;text-align:center;
margin-top:40px;padding-top:24px;border-top:1px solid var(--line)}
@media (max-width:640px){
.wrap{padding:28px 14px 64px}
.hero{padding-bottom:20px;margin-bottom:18px}
h1{font-size:30px;line-height:1.12;margin-bottom:10px}
.sub{font-size:14px;line-height:1.5;overflow-wrap:anywhere}
h2{font-size:20px;margin-bottom:10px}
h3{font-size:14px;margin:20px 0 8px}
.block{padding:18px 14px;margin:12px 0;border-radius:16px;scroll-margin-top:60px}
.lead{font-size:14px;margin-bottom:12px;overflow-wrap:anywhere}
.grid{gap:8px}
.grid.meta{grid-template-columns:repeat(2,minmax(0,1fr));margin:14px 0 4px}
.grid.stats{grid-template-columns:repeat(2,minmax(0,1fr));margin:10px 0}
.card{padding:11px 12px;border-radius:12px;min-width:0}
.card .k{font-size:10.5px;letter-spacing:.04em}
.card .v{font-size:14px;line-height:1.35;word-break:break-word;overflow-wrap:anywhere}
.toc{top:8px;margin:0 -2px 10px;padding:4px;border-radius:14px;flex-wrap:nowrap;
overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch}
.toc::-webkit-scrollbar{display:none}
.toc a{flex:0 0 auto;padding:7px 11px;font-size:12.5px;white-space:nowrap}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 -2px;padding-bottom:2px;
box-shadow:inset -10px 0 10px -12px rgba(0,0,0,.18)}
table{width:max-content;min-width:100%;font-size:13px}
th,td{padding:9px 8px}
th{font-size:10.5px}
td{max-width:260px;word-break:break-word;overflow-wrap:anywhere}
td.r,th.r{white-space:nowrap}
code{font-size:11.5px;padding:1px 5px}
.note{padding:11px 12px;font-size:12.5px;border-radius:12px;margin-top:12px;overflow-wrap:anywhere}
.badge{font-size:10.5px;padding:2px 7px}
details.cand{margin:8px 0;border-radius:12px}
details.cand>summary{padding:11px 12px;gap:8px}
.ctitle{font-size:14px;line-height:1.35;overflow-wrap:anywhere}
.csize{font-size:11.5px}
.cbody{padding:4px 12px 12px}
ul.paths,ul.ev{padding-left:16px;font-size:12.5px}
ul.paths li,ul.ev li{overflow-wrap:anywhere}
.steps li{gap:10px;padding:11px 0}
.stepnum{flex-basis:22px;height:22px;font-size:11px}
.steps p{font-size:12.5px;overflow-wrap:anywhere}
.foot{margin-top:28px;padding-top:18px;font-size:11.5px}
.bar{height:8px;margin:10px 0}
.legend{gap:10px;font-size:12px}
}
@media (max-width:340px){
.wrap{padding:22px 10px 56px}
h1{font-size:26px}
h2{font-size:18px}
.block{padding:15px 11px}
.grid.meta,.grid.stats{grid-template-columns:1fr}
.card .v{font-size:13.5px}
}
@media (prefers-color-scheme:dark){
:root{--bg:#000;--surface:#000;--surface-2:#1c1c1e;
--ink:#f5f5f7;--ink-soft:#d2d2d7;--muted:#98989d;
--line:#2c2c2e;--line-strong:#3a3a3c;
--accent:#2997ff;--accent-ink:#64b5ff;
--safe-bg:rgba(48,209,88,.16);--safe-ink:#7ee2a1;
--caution-bg:rgba(255,159,10,.16);--caution-ink:#ffc46b;
--danger-bg:rgba(255,69,58,.16);--danger-ink:#ff9a92;
--known:#f5f5f7;--unknown:#2c2c2e;
--glass:rgba(28,28,30,.72);--glass-edge:rgba(255,255,255,.12);--fallback:#1c1c1e;
--shadow:0 1px 2px rgba(0,0,0,.5),0 8px 24px -18px rgba(0,0,0,.8);
--shadow-hover:0 2px 4px rgba(0,0,0,.55),0 16px 36px -22px rgba(0,0,0,.9)}
body{background:#000}
.block,details.cand{background:#000;border-color:var(--line)}
.card:hover{background:#2c2c2e;border-color:var(--line)}
details.cand>summary:hover{background:#171719}
tbody tr:hover{background:#171719}
.note{background:#1c1c1e}
.note.warn{background:#1c1c1e;border-left-color:#ff9f0a}
code{background:#2c2c2e;color:#e5e5ea}
.bar-known{background:var(--known)}
.bar-unknown{background:var(--unknown)}
}
"""
