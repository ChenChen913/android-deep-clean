"""Self-contained HTML report for the pre-clean analysis.

Pure rendering over the analysis JSON: no device access, no external assets,
no CDN, no network.  Everything is inline so the file opens offline.
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
        ("覆盖率", coverage.get("status") or "-"),
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
                f'<td>{_e(item.get("state"))}</td><td>{_e(item.get("reason") or "-")}</td></tr>'
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
        + "；内部 data/cache 与 split APK 未计入。</p>",
    ]
    if app.get("available"):
        parts.append('<div class="grid stats">')
        for label, value in (
            ("应用包", f"{_num(packages.get('total'))}"),
            ("基础 APK", known.get("apk_human") or "-"),
            ("外部 data", known.get("external_data_human") or "-"),
            ("外部 media", known.get("external_media_human") or "-"),
            ("外部 obb", known.get("external_obb_human") or "-"),
            ("已知合计", known.get("total_human") or "-"),
        ):
            parts.append(f'<div class="card"><div class="k">{_e(label)}</div><div class="v">{_e(value)}</div></div>')
        parts.append("</div>")
        top = app.get("top_external") or []
        if top:
            parts.append('<h3>外部存储占用较高的应用</h3><div class="scroll"><table><thead><tr>'
                         '<th>应用</th><th>包名</th><th class="r">外部合计</th><th class="r">data</th>'
                         '<th class="r">media</th><th class="r">obb</th><th class="r">基础 APK</th>'
                         '</tr></thead><tbody>')
            for row in top[:12]:
                parts.append(
                    f'<tr><td>{_e(row.get("label"))}</td><td><code>{_e(row.get("package"))}</code></td>'
                    f'<td class="r">{_e(row.get("external_total_human"))}</td>'
                    f'<td class="r">{_e(row.get("data_human"))}</td>'
                    f'<td class="r">{_e(row.get("media_human"))}</td>'
                    f'<td class="r">{_e(row.get("obb_human"))}</td>'
                    f'<td class="r">{_e(row.get("apk_human"))}</td></tr>'
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
        "</ul></div>"
    )
    parts.append("</section>")
    return "".join(parts)


_CSS = """
:root{--bg:#f6f7f9;--panel:#fff;--ink:#1c1f24;--muted:#666d78;--line:#e3e6ea;
--safe:#1f8a4c;--caution:#b26b00;--danger:#b3261e;--accent:#2b6cb0;--known:#3f8f5f;--unknown:#c9ced6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 64px}
.hero{padding:8px 0 20px;border-bottom:1px solid var(--line);margin-bottom:20px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:.01em}
.sub{margin:0;color:var(--muted)}
h2{font-size:19px;margin:0 0 10px}
h3{font-size:15px;margin:20px 0 8px;color:#333}
.block{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:16px 0}
.lead{margin:0 0 12px;color:#333}
.grid{display:grid;gap:10px}
.grid.meta{grid-template-columns:repeat(auto-fit,minmax(160px,1fr));margin:16px 0}
.grid.stats{grid-template-columns:repeat(auto-fit,minmax(130px,1fr));margin:12px 0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.card .k{font-size:12px;color:var(--muted)}
.card .v{font-weight:600;word-break:break-all}
.toc{display:flex;flex-wrap:wrap;gap:14px;margin:8px 0 4px;font-size:13px}
.toc a{color:var(--accent);text-decoration:none}
.toc a:hover{text-decoration:underline}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin:4px 0}
th,td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}
th{background:#fafbfc;font-weight:600;color:#444;white-space:nowrap}
td.r,th.r{text-align:right;white-space:nowrap}
code{font:12.5px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;background:#f2f4f6;
padding:1px 5px;border-radius:4px;word-break:break-all}
.bar{display:flex;height:14px;border-radius:7px;overflow:hidden;background:#eceff3;margin:8px 0}
.bar-known{background:var(--known)}
.bar-unknown{background:var(--unknown)}
.legend{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#444}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px}
.dot.known{background:var(--known)}
.dot.unknown{background:var(--unknown)}
.note{background:#fafbfc;border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-top:12px;font-size:13.5px}
.note.warn{background:#fff8ec;border-color:#f0dcc0}
.note ul{margin:6px 0 0;padding-left:20px}
.badge{display:inline-block;font-size:11.5px;font-weight:700;padding:2px 8px;border-radius:999px;color:#fff;letter-spacing:.02em}
.badge.safe{background:var(--safe)}
.badge.caution{background:var(--caution)}
.badge.danger{background:var(--danger)}
details.cand{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:0;margin:10px 0}
details.cand>summary{cursor:pointer;display:flex;align-items:center;gap:10px;padding:11px 14px;list-style:none}
details.cand>summary::-webkit-details-marker{display:none}
details.cand>summary::before{content:"▸";color:var(--muted);font-size:12px}
details.cand[open]>summary::before{content:"▾"}
.ctitle{flex:1;font-weight:600;word-break:break-all}
.csize{color:var(--muted);font-size:13px;white-space:nowrap}
.cbody{padding:4px 16px 16px;border-top:1px dashed var(--line)}
.cbody p{margin:8px 0}
.cid{color:var(--muted);font-size:12.5px}
.muted{color:var(--muted);font-size:13px;margin:10px 0 4px}
ul.paths,ul.ev{margin:4px 0 8px;padding-left:20px;font-size:13px}
ul.ev li{margin:2px 0}
.foot{color:var(--muted);font-size:12.5px;text-align:center;margin-top:28px}
@media (prefers-color-scheme:dark){
:root{--bg:#14171c;--panel:#1b1f26;--ink:#e6e9ee;--muted:#9aa3af;--line:#2a3038;
--accent:#7ab0e8;--known:#4f9e6f;--unknown:#3a414b}
th{background:#20252d;color:#cfd6df}
code{background:#232932}
.card{background:var(--panel)}
.note{background:#1f242c}
.note.warn{background:#2a2318;border-color:#4a3a1c}
}
"""
