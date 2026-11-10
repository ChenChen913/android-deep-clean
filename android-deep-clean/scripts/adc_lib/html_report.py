"""HTML reports (v2 design): read-only analysis and post-cleanup archive.

Pure rendering: no device access, no external assets, single self-contained
file.  Layout follows the "data credibility" design language: every number
carries one of five explicit trust states (known / partial / unknown /
unconfirmed / failed), and the first screen answers "what should I worry
about" instead of listing seven equal metrics.

The v1 renderer is kept verbatim in ``html_report_legacy.py`` for rollback.
"""

from __future__ import annotations

import html as _html
import posixpath
from typing import Any

from .util import RISK_ORDER, human_bytes

MAX_ITEMS = 60
MAX_MEDIA_SAMPLES = 8
MAX_APP_ROWS = 12
MAX_DIR_ROWS = 10
MAX_DIFF_ROWS = 8


# ---------------------------------------------------------------- helpers
def _e(value: Any) -> str:
    return _html.escape("" if value is None else str(value), quote=True)


def _num(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ratio(part: Any, total: Any) -> float:
    p, t = _num(part), _num(total)
    if t <= 0:
        return 0.0
    return max(0.0, min(100.0, p / t * 100.0))


def _pct(part: Any, total: Any) -> str:
    return f"{_ratio(part, total):.1f}%"


def _size(value: Any) -> str:
    if value is None:
        return "未知"
    return human_bytes(_num(value))


def _short(text: Any, limit: int = 120) -> str:
    raw = "" if text is None else str(text)
    return raw if len(raw) <= limit else raw[: limit - 1] + "…"


# ---------------------------------------------------------------- trust states
# The five states are the core vocabulary of this report.  Anything that is
# missing a trustworthy number must not fall back to "0 B".
_STATE_LABEL = {
    "known": "已知",
    "partial": "部分",
    "unknown": "未知",
    "unconfirmed": "无法确认",
    "failed": "失败",
    "skipped": "跳过",
}
_STATE_ICON = {
    "known": "●",
    "partial": "▲",
    "unknown": "?",
    "unconfirmed": "≈",
    "failed": "✕",
    "skipped": "–",
}


def _state(state: str, label: str | None = None) -> str:
    key = state if state in _STATE_LABEL else "unknown"
    text = label if label is not None else _STATE_LABEL[key]
    return (
        f'<span class="state-badge {key}">'
        f'<span aria-hidden="true">{_STATE_ICON[key]}</span>{_e(text)}</span>'
    )


_RISK_CLASS = {"SAFE": "SAFE", "CAUTION": "CAUTION", "DANGEROUS": "DANGEROUS"}
_RISK_LABEL = {
    "SAFE": "安全",
    "CAUTION": "需谨慎",
    "DANGEROUS": "高风险",
}


def _risk_badge(risk: Any) -> str:
    key = str(risk or "CAUTION").upper()
    key = key if key in _RISK_CLASS else "CAUTION"
    return f'<span class="risk-badge {key}">{_e(key)} {_e(_RISK_LABEL[key])}</span>'


_MEDIA_STATUS_LABEL = {
    "collected": "已采集",
    "partial": "部分可用",
    "unavailable": "不可用",
    "not_collected": "未采集",
    "skipped": "跳过",
}
_MEDIA_COLLECTION = {
    "ok": ("正常", "known"),
    "empty": ("0 条（未发现可参与核对的同类文件）", "known"),
    "zero_unconfirmed": ("0 条（少量文件，无法确认）", "unconfirmed"),
    "zero_mismatch": ("0 条但与扫描不一致", "failed"),
    "failed": ("查询失败", "failed"),
    "skipped": ("跳过", "skipped"),
}
_COLLECTION_NAME = {
    "images": "图片",
    "video": "视频",
    "audio": "音频",
    "downloads": "下载",
}
_RELATION_LABEL = {
    "total": "设备总量",
    "disjoint-with-apk": "与 APK 不重叠",
    "disjoint-with-file-scan": "与文件扫描不重叠",
    "subset-of-file-scan": "包含在文件扫描内",
    "overlaps-file-scan": "与文件扫描重叠",
}
_RELATION_STATE = {
    "total": "known",
    "disjoint-with-apk": "known",
    "disjoint-with-file-scan": "known",
    "subset-of-file-scan": "known",
    "overlaps-file-scan": "known",
}
_COVERAGE_LABEL = {
    "good": ("完整", "known"),
    "partial": ("部分覆盖", "partial"),
    "suspect": ("可疑（未扫到文件）", "unconfirmed"),
    "scanned": ("已扫描", "known"),
    "skipped": ("未扫描", "skipped"),
    "unknown": ("未知", "unknown"),
}


# ---------------------------------------------------------------- css
_CSS = """
:root{
  --font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Microsoft YaHei",sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,"Liberation Mono","Courier New",monospace;

  --bg-page:#f7fafd;
  --bg-glass:rgba(255,255,255,.74);
  --bg-glass-solid:rgba(255,255,255,.9);
  --bg-subtle:rgba(241,245,250,.66);
  --bg-hover:rgba(235,242,250,.82);

  --border-glass:rgba(255,255,255,.88);
  --border-subtle:rgba(226,232,240,.72);
  --border-soft:rgba(203,213,225,.62);

  --text-primary:#0f172a;
  --text-secondary:#475569;
  --text-tertiary:#94a3b8;

  --shadow-card:0 8px 30px rgba(15,23,42,.045),inset 0 1px 1px rgba(255,255,255,.82);
  --shadow-sm:0 2px 8px rgba(15,23,42,.035);
  --shadow-float:0 10px 36px rgba(37,99,235,.055),0 2px 10px rgba(0,0,0,.02),inset 0 1px 1px rgba(255,255,255,.95);

  --accent:#0284c7;
  --accent-soft:rgba(2,132,199,.1);
  --accent-line:rgba(2,132,199,.35);

  --state-known-bg:rgba(16,185,129,.1);
  --state-known-border:rgba(16,185,129,.42);
  --state-known-text:#047857;
  --state-partial-bg:rgba(245,158,11,.11);
  --state-partial-border:rgba(245,158,11,.44);
  --state-partial-text:#b45309;
  --state-unknown-bg:rgba(100,116,139,.09);
  --state-unknown-border:rgba(148,163,184,.46);
  --state-unknown-text:#475569;
  --state-unconfirmed-bg:rgba(139,92,246,.1);
  --state-unconfirmed-border:rgba(139,92,246,.42);
  --state-unconfirmed-text:#6d28d9;
  --state-failed-bg:rgba(239,68,68,.09);
  --state-failed-border:rgba(239,68,68,.42);
  --state-failed-text:#b91c1c;
  --state-skipped-bg:rgba(148,163,184,.1);
  --state-skipped-border:rgba(148,163,184,.4);
  --state-skipped-text:#64748b;

  --risk-safe-bg:rgba(16,185,129,.1);--risk-safe-text:#047857;--risk-safe-border:rgba(16,185,129,.34);
  --risk-caution-bg:rgba(249,115,22,.11);--risk-caution-text:#c2410c;--risk-caution-border:rgba(249,115,22,.34);
  --risk-dangerous-bg:rgba(239,68,68,.1);--risk-dangerous-text:#b91c1c;--risk-dangerous-border:rgba(239,68,68,.34);

  --stripe-pattern:repeating-linear-gradient(45deg,rgba(148,163,184,.18),rgba(148,163,184,.18) 9px,transparent 9px,transparent 18px);
  --radius-lg:18px;--radius-md:12px;--radius-sm:9px;
}

@media (prefers-color-scheme: dark){
  :root{
    --bg-page:#0a1120;
    --bg-glass:rgba(20,30,46,.72);
    --bg-glass-solid:rgba(20,30,46,.9);
    --bg-subtle:rgba(30,41,59,.55);
    --bg-hover:rgba(38,52,74,.7);

    --border-glass:rgba(148,163,184,.14);
    --border-subtle:rgba(148,163,184,.18);
    --border-soft:rgba(148,163,184,.26);

    --text-primary:#e7eef8;
    --text-secondary:#a9b7cb;
    --text-tertiary:#71809a;

    --shadow-card:0 8px 30px rgba(0,0,0,.35),inset 0 1px 1px rgba(255,255,255,.04);
    --shadow-sm:0 2px 8px rgba(0,0,0,.28);
    --shadow-float:0 10px 36px rgba(0,0,0,.42),inset 0 1px 1px rgba(255,255,255,.05);

    --accent:#38bdf8;--accent-soft:rgba(56,189,248,.14);--accent-line:rgba(56,189,248,.42);

    --state-known-bg:rgba(16,185,129,.16);--state-known-border:rgba(52,211,153,.4);--state-known-text:#6ee7b7;
    --state-partial-bg:rgba(245,158,11,.16);--state-partial-border:rgba(251,191,36,.4);--state-partial-text:#fcd34d;
    --state-unknown-bg:rgba(148,163,184,.14);--state-unknown-border:rgba(148,163,184,.36);--state-unknown-text:#cbd5e1;
    --state-unconfirmed-bg:rgba(139,92,246,.18);--state-unconfirmed-border:rgba(167,139,250,.42);--state-unconfirmed-text:#c4b5fd;
    --state-failed-bg:rgba(239,68,68,.16);--state-failed-border:rgba(248,113,113,.42);--state-failed-text:#fca5a5;
    --state-skipped-bg:rgba(148,163,184,.12);--state-skipped-border:rgba(148,163,184,.3);--state-skipped-text:#94a3b8;

    --risk-safe-bg:rgba(16,185,129,.16);--risk-safe-text:#6ee7b7;--risk-safe-border:rgba(52,211,153,.36);
    --risk-caution-bg:rgba(249,115,22,.18);--risk-caution-text:#fdba74;--risk-caution-border:rgba(251,146,60,.4);
    --risk-dangerous-bg:rgba(239,68,68,.16);--risk-dangerous-text:#fca5a5;--risk-dangerous-border:rgba(248,113,113,.4);

    --stripe-pattern:repeating-linear-gradient(45deg,rgba(148,163,184,.16),rgba(148,163,184,.16) 9px,transparent 9px,transparent 18px);
  }
  body{
    background-image:
      radial-gradient(at 10% 8%,rgba(56,189,248,.1) 0,transparent 45%),
      radial-gradient(at 90% 12%,rgba(129,140,248,.1) 0,transparent 40%),
      radial-gradient(at 80% 85%,rgba(52,211,153,.07) 0,transparent 45%);
  }
}

*{box-sizing:border-box;margin:0;padding:0}
body{
  font-family:var(--font-sans);
  background-color:var(--bg-page);
  background-image:
    radial-gradient(at 10% 8%,rgba(56,189,248,.12) 0,transparent 45%),
    radial-gradient(at 90% 12%,rgba(129,140,248,.12) 0,transparent 40%),
    radial-gradient(at 50% 50%,rgba(241,245,255,.7) 0,transparent 80%),
    radial-gradient(at 80% 85%,rgba(52,211,153,.09) 0,transparent 45%),
    radial-gradient(at 15% 90%,rgba(192,132,252,.08) 0,transparent 45%);
  background-attachment:fixed;
  color:var(--text-primary);
  line-height:1.56;
  padding-bottom:64px;
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
}
a{color:inherit;text-decoration:none}
code{font-family:var(--font-mono);font-size:.92em;word-break:break-all}

/* ---------- top nav ---------- */
.top-nav{position:sticky;top:0;z-index:100;background:var(--bg-glass-solid);
  backdrop-filter:blur(20px) saturate(180%);-webkit-backdrop-filter:blur(20px) saturate(180%);
  border-bottom:1px solid var(--border-subtle)}
.nav-inner{max-width:1240px;margin:0 auto;padding:11px 20px;display:flex;align-items:center;
  justify-content:space-between;gap:14px}
.nav-brand{display:flex;align-items:center;gap:9px;font-weight:700;font-size:.95rem;
  letter-spacing:-.01em;white-space:nowrap}
.readonly-badge{font-size:.7rem;font-weight:600;padding:3px 9px;border-radius:999px;
  background:var(--state-known-bg);color:var(--state-known-text);
  border:1px solid var(--state-known-border);white-space:nowrap}
.nav-links{display:flex;gap:4px;overflow-x:auto;scrollbar-width:none;-webkit-overflow-scrolling:touch;padding:2px 0}
.nav-links::-webkit-scrollbar{display:none}
.nav-link{font-size:.82rem;font-weight:500;color:var(--text-secondary);padding:6px 11px;
  border-radius:8px;white-space:nowrap;transition:.2s}
.nav-link:hover{background:var(--bg-subtle);color:var(--text-primary)}

.container{max-width:1240px;margin:22px auto 0;padding:0 20px}

/* ---------- trust legend ---------- */
.legend-bar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 15px;
  background:var(--bg-glass);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border:1px solid var(--border-glass);border-radius:var(--radius-md);font-size:.78rem;
  margin-bottom:20px;box-shadow:var(--shadow-sm)}
.legend-title{font-weight:600;color:var(--text-secondary);display:flex;align-items:center;gap:6px;margin-right:2px}
.state-badge{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:6px;
  font-size:.73rem;font-weight:600;line-height:1.35;letter-spacing:.01em;white-space:nowrap;
  border:1px solid transparent}
.state-badge.known{background:var(--state-known-bg);color:var(--state-known-text);border-color:var(--state-known-border)}
.state-badge.partial{background:var(--state-partial-bg);color:var(--state-partial-text);border-color:var(--state-partial-border)}
.state-badge.unknown{background:var(--state-unknown-bg);color:var(--state-unknown-text);border-color:var(--state-unknown-border)}
.state-badge.unconfirmed{background:var(--state-unconfirmed-bg);color:var(--state-unconfirmed-text);border-color:var(--state-unconfirmed-border)}
.state-badge.failed{background:var(--state-failed-bg);color:var(--state-failed-text);border-color:var(--state-failed-border)}
.state-badge.skipped{background:var(--state-skipped-bg);color:var(--state-skipped-text);border-color:var(--state-skipped-border)}

/* ---------- hero ---------- */
.hero-verdict{display:grid;grid-template-columns:1fr;gap:16px;margin-bottom:18px}
.verdict-box{background:var(--bg-glass);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);
  border:1px solid var(--border-glass);border-radius:var(--radius-lg);padding:26px 26px 20px;
  box-shadow:var(--shadow-float)}
.verdict-header{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.verdict-title{font-size:1.5rem;line-height:1.3;font-weight:800;letter-spacing:-.02em;
  color:var(--text-primary);margin-bottom:7px}
.verdict-subtitle{font-size:.88rem;color:var(--text-secondary);line-height:1.55}
.honesty-callout{margin-top:16px;padding:13px 15px;border-radius:var(--radius-md);
  background:var(--accent-soft);border:1px solid var(--accent-line);
  font-size:.83rem;line-height:1.65;color:var(--text-secondary)}
.honesty-callout strong{color:var(--text-primary)}
.verdict-strip{display:flex;flex-wrap:wrap;gap:12px;font-size:.79rem;color:var(--text-secondary);
  border-top:1px solid var(--border-subtle);padding-top:12px;margin-top:18px}
.verdict-strip b{color:var(--text-primary);font-family:var(--font-mono)}
.verdict-strip .sep{color:var(--border-soft)}

.device-meta-card{background:var(--bg-glass);border:1px solid var(--border-glass);
  border-radius:var(--radius-lg);padding:18px 20px;box-shadow:var(--shadow-card)}
.meta-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 14px}
.meta-item-label{font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;
  color:var(--text-tertiary);font-weight:600;margin-bottom:2px}
.meta-item-val{font-size:.83rem;font-weight:600;color:var(--text-primary);word-break:break-word}

/* ---------- section shell ---------- */
.section-block{background:var(--bg-glass);backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border:1px solid var(--border-glass);border-radius:var(--radius-lg);padding:22px 24px;
  margin-bottom:18px;box-shadow:var(--shadow-card);scroll-margin-top:72px}
.section-header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
  flex-wrap:wrap;margin-bottom:14px}
.section-title{font-size:1.02rem;font-weight:750;color:var(--text-primary);
  display:flex;align-items:center;gap:8px;letter-spacing:-.01em}
.section-title svg{flex:0 0 auto;color:var(--accent)}
.section-note{font-size:.76rem;color:var(--text-tertiary);font-family:var(--font-mono)}
.section-lead{font-size:.84rem;color:var(--text-secondary);line-height:1.6;margin-bottom:14px}

/* ---------- meter ---------- */
.meter-caption{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;
  font-size:.78rem;color:var(--text-secondary);margin-bottom:7px}
.meter-bar{display:flex;height:34px;border-radius:10px;overflow:hidden;
  border:1px solid var(--border-subtle);background:var(--bg-subtle)}
.meter-seg{display:flex;align-items:center;justify-content:center;overflow:hidden;
  font-size:.7rem;font-weight:600;color:#fff;white-space:nowrap;
  transition:filter .2s}
.meter-seg.known{background:linear-gradient(180deg,#0ea5e9,#0284c7)}
.meter-seg.other-known{background:linear-gradient(180deg,#10b981,#059669)}
.meter-seg.partial{background-image:var(--stripe-pattern);background-color:#f59e0b;color:#3f2d00}
.meter-seg.unexplained{background-color:transparent;background-image:var(--stripe-pattern);
  color:var(--state-unknown-text);font-weight:700}
.meter-seg.zero{display:none}
.meter-legend-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:10px;margin-top:14px}
.meter-legend-card{background:var(--bg-subtle);border:1px solid var(--border-subtle);
  border-radius:var(--radius-md);padding:11px 13px}
.meter-legend-card.highlight{background:var(--state-unknown-bg);
  border-color:var(--state-unknown-border)}
.legend-card-header{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:5px}
.legend-card-name{font-size:.74rem;font-weight:600;color:var(--text-secondary)}
.legend-card-val{font-size:1.18rem;font-weight:800;font-family:var(--font-mono);letter-spacing:-.02em}
.legend-card-val small{font-size:.72rem;font-weight:600;margin-left:2px}
.legend-card-note{font-size:.69rem;color:var(--text-tertiary);margin-top:4px;line-height:1.45}

/* ---------- tables ---------- */
.audit-table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:var(--radius-md);
  border:1px solid var(--border-subtle);background:var(--bg-glass-solid)}
.audit-table{width:100%;border-collapse:collapse;font-size:.79rem;text-align:left}
.audit-table th{background:var(--bg-subtle);padding:9px 11px;font-weight:700;
  color:var(--text-secondary);border-bottom:1px solid var(--border-subtle);
  font-size:.7rem;letter-spacing:.03em;white-space:nowrap}
.audit-table td{padding:9px 11px;border-bottom:1px solid var(--border-subtle);
  vertical-align:middle}
.audit-table tbody tr:last-child td{border-bottom:0}
.audit-table tbody tr:hover{background:var(--bg-hover)}
.audit-table .r{text-align:right;white-space:nowrap}
.audit-table .mono{font-family:var(--font-mono);font-weight:600}
.audit-table .path{font-family:var(--font-mono);font-size:.94em;word-break:break-all;
  overflow-wrap:anywhere}
.overlap-tag{display:inline-block;font-size:.66rem;font-weight:600;padding:2px 6px;
  border-radius:5px;background:var(--bg-subtle);color:var(--text-secondary);
  border:1px solid var(--border-subtle);margin:1px 3px 1px 0;white-space:nowrap}

.math-warning{margin-top:14px;padding:13px 16px;border-radius:var(--radius-md);
  background:var(--state-partial-bg);border:1px solid var(--state-partial-border);
  font-size:.79rem;line-height:1.7;color:var(--text-secondary)}
.math-warning strong{color:var(--state-partial-text)}
.math-warning ol{margin:6px 0 0;padding-left:20px}
.math-warning li{margin:3px 0}

/* ---------- media ---------- */
.media-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
  gap:12px;align-items:start}
.media-card{background:var(--bg-glass-solid);border:1px solid var(--border-subtle);
  border-radius:var(--radius-md);padding:13px 14px;box-shadow:var(--shadow-sm)}
.media-card.discrepancy{border-color:var(--state-unconfirmed-border);
  background:var(--state-unconfirmed-bg)}
.media-card.broken{border-color:var(--state-failed-border);background:var(--state-failed-bg)}
.media-card-top{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:10px}
.media-card-title{font-size:.82rem;font-weight:700;color:var(--text-primary)}
.compare-row{display:flex;align-items:center;gap:7px;margin:5px 0;font-size:.72rem}
.compare-label{flex:0 0 52px;color:var(--text-tertiary);font-weight:600}
.compare-bar-track{flex:1;height:7px;border-radius:4px;background:var(--bg-subtle);overflow:hidden}
.compare-bar-fill{display:block;height:100%;border-radius:4px;min-width:0}
.compare-bar-fill.idx{background:linear-gradient(90deg,#0284c7,#38bdf8)}
.compare-bar-fill.scan{background:linear-gradient(90deg,#059669,#34d399)}
.compare-val{flex:0 0 auto;font-family:var(--font-mono);font-weight:700;
  font-size:.74rem;color:var(--text-secondary);min-width:44px;text-align:right}
.media-card-foot{font-size:.71rem;color:var(--text-secondary);line-height:1.5;
  border-top:1px solid var(--border-subtle);padding-top:8px;margin-top:8px}
.media-card-foot.warn{color:var(--state-unconfirmed-text)}

/* ---------- stat grid ---------- */
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));
  gap:9px;margin:14px 0}
.stat-tile{background:var(--bg-subtle);border:1px solid var(--border-subtle);
  border-radius:var(--radius-md);padding:10px 12px}
.stat-label{font-size:.68rem;color:var(--text-tertiary);font-weight:600;
  letter-spacing:.03em;margin-bottom:3px}
.stat-val{font-size:1.02rem;font-weight:750;font-family:var(--font-mono);
  letter-spacing:-.02em;color:var(--text-primary);word-break:break-word}
.stat-val small{font-size:.68rem;font-weight:600;color:var(--text-tertiary);margin-left:3px}

/* ---------- app bars ---------- */
.app-name-block{display:flex;flex-direction:column;gap:1px;min-width:120px}
.app-cn-name{font-weight:650;font-size:.82rem;color:var(--text-primary)}
.app-pkg-name{font-family:var(--font-mono);font-size:.68rem;color:var(--text-tertiary);
  word-break:break-all}
.app-bar-vis{height:6px;border-radius:3px;background:var(--bg-subtle);overflow:hidden;min-width:80px}
.app-bar-inner{height:100%;border-radius:3px;background:linear-gradient(90deg,#0284c7,#38bdf8)}
.app-bar-inner.caution{background:linear-gradient(90deg,#ea580c,#fb923c)}

/* ---------- candidates ---------- */
.candidates-summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:10px;margin-bottom:20px}
.candidate-stat-card{background:var(--bg-subtle);border:1px solid var(--border-subtle);
  border-radius:var(--radius-md);padding:12px 14px}
.c-stat-label{font-size:.7rem;color:var(--text-tertiary);font-weight:600;margin-bottom:4px}
.c-stat-val{font-size:1.22rem;font-weight:800;font-family:var(--font-mono);letter-spacing:-.02em}
.c-stat-val small{font-size:.7rem;font-weight:600;color:var(--text-tertiary);margin-left:3px}

.focus-title{font-size:.85rem;font-weight:700;color:var(--text-secondary);
  margin:4px 0 11px;display:flex;align-items:center;gap:6px}
.top-focus-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));
  gap:11px;margin-bottom:22px}
.focus-card{background:var(--bg-glass-solid);border:1px solid var(--border-subtle);
  border-radius:var(--radius-md);padding:14px 15px;position:relative;
  box-shadow:var(--shadow-sm);border-top:3px solid var(--accent)}
.focus-card.largest{border-top-color:#0284c7}
.focus-card.riskiest{border-top-color:var(--risk-caution-text)}
.focus-card.review{border-top-color:var(--state-unconfirmed-text)}
.focus-tag{display:inline-block;font-size:.64rem;font-weight:700;padding:2px 7px;
  border-radius:5px;margin-bottom:7px;letter-spacing:.02em;background:var(--bg-subtle);
  color:var(--text-secondary)}
.focus-card.largest .focus-tag{background:var(--accent-soft);color:var(--accent)}
.focus-card.riskiest .focus-tag{background:var(--risk-caution-bg);color:var(--risk-caution-text)}
.focus-card.review .focus-tag{background:var(--state-unconfirmed-bg);color:var(--state-unconfirmed-text)}
.focus-cat{font-size:.7rem;color:var(--text-tertiary);margin-bottom:3px}
.focus-path{font-size:.88rem;font-weight:700;color:var(--text-primary);word-break:break-all;
  line-height:1.4}
.focus-size{font-size:1.4rem;font-weight:800;font-family:var(--font-mono);
  letter-spacing:-.03em;margin:7px 0 5px}
.focus-size small{font-size:.78rem;font-weight:700;margin-left:2px}
.focus-desc{font-size:.74rem;color:var(--text-secondary);line-height:1.55}

.candidate-item{background:var(--bg-glass-solid);border:1px solid var(--border-subtle);
  border-radius:var(--radius-md);margin-bottom:10px;overflow:hidden;box-shadow:var(--shadow-sm)}
.candidate-item>summary{cursor:pointer;list-style:none;transition:background .15s}
.candidate-item>summary::-webkit-details-marker{display:none}
.candidate-item>summary::after{content:"⌄";color:var(--text-tertiary);font-size:.9rem;
  line-height:1;margin-left:2px;transition:transform .2s}
.candidate-item[open]>summary::after{transform:rotate(180deg)}
.candidate-item>summary:hover{background:var(--bg-hover)}
.candidate-item[open]{border-color:var(--border-soft)}
.candidate-header{display:flex;justify-content:space-between;align-items:flex-start;
  gap:10px;padding:12px 15px;background:var(--bg-subtle);flex-wrap:wrap}
.candidate-meta-left{display:flex;align-items:center;gap:7px;flex-wrap:wrap;flex:1;min-width:0}
.candidate-title{font-size:.83rem;font-weight:650;color:var(--text-primary);
  word-break:break-all;overflow-wrap:anywhere;line-height:1.45}
.candidate-size{font-size:1rem;font-weight:800;font-family:var(--font-mono);
  color:var(--text-primary);white-space:nowrap;letter-spacing:-.02em}
.risk-badge{display:inline-flex;align-items:center;font-size:.68rem;font-weight:800;
  padding:3px 8px;border-radius:5px;letter-spacing:.03em;white-space:nowrap;border:1px solid transparent}
.risk-badge.SAFE{background:var(--risk-safe-bg);color:var(--risk-safe-text);border-color:var(--risk-safe-border)}
.risk-badge.CAUTION{background:var(--risk-caution-bg);color:var(--risk-caution-text);border-color:var(--risk-caution-border)}
.risk-badge.DANGEROUS{background:var(--risk-dangerous-bg);color:var(--risk-dangerous-text);border-color:var(--risk-dangerous-border)}
.candidate-body{padding:12px 15px 15px}
.three-stage{display:grid;grid-template-columns:1fr;gap:8px;margin-bottom:10px}
.stage-item{background:var(--bg-subtle);border:1px solid var(--border-subtle);
  border-radius:var(--radius-sm);padding:9px 11px;font-size:.76rem}
.stage-label{font-weight:700;color:var(--text-tertiary);font-size:.66rem;
  letter-spacing:.04em;margin-bottom:3px;display:flex;align-items:center;gap:4px}
.stage-content{color:var(--text-secondary);line-height:1.55}
.candidate-meta{display:flex;flex-wrap:wrap;gap:10px;font-size:.72rem;
  color:var(--text-tertiary);margin-bottom:9px;font-family:var(--font-mono)}
.evidence-box{font-size:.72rem;background:var(--bg-subtle);border:1px solid var(--border-subtle);
  border-radius:var(--radius-sm);padding:9px 11px;margin-top:8px}
.evidence-box-title{font-weight:700;color:var(--text-secondary);margin-bottom:4px;font-size:.72rem}
.evidence-box ul{margin:0;padding-left:15px;line-height:1.55}
.evidence-box li{font-family:var(--font-mono);word-break:break-all;color:var(--text-secondary);
  margin:1px 0;font-size:.96em}
.evidence-box li.plain{font-family:var(--font-sans);font-size:.98em}

/* ---------- notes / footer ---------- */
.note{background:var(--bg-subtle);border:1px solid var(--border-subtle);
  border-radius:var(--radius-md);padding:11px 14px;margin-top:12px;
  font-size:.78rem;color:var(--text-secondary);line-height:1.6}
.note.warn{background:var(--state-partial-bg);border-color:var(--state-partial-border)}
.note.danger{background:var(--state-failed-bg);border-color:var(--state-failed-border)}
.note.unconfirmed{background:var(--state-unconfirmed-bg);border-color:var(--state-unconfirmed-border)}
.note strong{color:var(--text-primary)}
.note ul{margin:5px 0 0;padding-left:18px}
.note li{margin:2px 0}
/* The global reset zeroes padding, which would push list markers outside the
   card.  Any bare list inside a section needs its own gutter. */
.bullet-list{margin:6px 0 0;padding-left:20px;line-height:1.72;color:var(--text-secondary)}
.bullet-list li{margin:5px 0}
.section-block>ul,.security-footer-box>ul{margin:6px 0 0;padding-left:20px;line-height:1.72}
.note .note-title{font-weight:700;display:block;margin-bottom:3px}

.security-footer-box{background:var(--bg-glass);border:1px solid var(--border-glass);
  border-left:4px solid var(--accent);border-radius:var(--radius-md);
  padding:18px 20px;margin-bottom:18px;box-shadow:var(--shadow-sm)}
.security-title{font-weight:750;font-size:.9rem;color:var(--text-primary);
  margin-bottom:9px;display:flex;align-items:center;gap:7px}
.security-list{padding-left:18px;line-height:1.72;font-size:.79rem;color:var(--text-secondary)}
.security-list li{margin:4px 0}
.foot{color:var(--text-tertiary);font-size:.74rem;text-align:center;
  margin-top:28px;padding-top:18px;border-top:1px solid var(--border-subtle);line-height:1.7}

/* ---------- cleanup receipt ---------- */
.receipt-wrapper{max-width:1080px;margin:22px auto 0;padding:0 20px}
.receipt-header{display:flex;justify-content:space-between;align-items:flex-start;
  gap:16px;flex-wrap:wrap;margin-bottom:16px}
.receipt-code-pill{display:inline-block;font-family:var(--font-mono);font-size:.7rem;
  font-weight:700;letter-spacing:.05em;padding:4px 10px;border-radius:999px;
  background:var(--accent-soft);color:var(--accent);border:1px solid var(--accent-line);
  margin-bottom:9px}
.receipt-header h1{font-size:1.42rem;font-weight:800;letter-spacing:-.02em;line-height:1.3}
.receipt-status-stamp{font-size:.82rem;font-weight:700;padding:8px 14px;border-radius:var(--radius-md);
  border:2px solid var(--state-partial-border);background:var(--state-partial-bg);
  color:var(--state-partial-text);white-space:nowrap}
.receipt-status-stamp.safe{border-color:var(--state-known-border);background:var(--state-known-bg);
  color:var(--state-known-text)}
.receipt-status-stamp.danger{border-color:var(--state-failed-border);background:var(--state-failed-bg);
  color:var(--state-failed-text)}
.meta-tape{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:11px 18px;
  background:var(--bg-glass);border:1px solid var(--border-glass);border-radius:var(--radius-md);
  padding:14px 18px;margin-bottom:16px;box-shadow:var(--shadow-sm)}
.tape-item label{display:block;font-size:.66rem;text-transform:uppercase;letter-spacing:.05em;
  color:var(--text-tertiary);font-weight:700;margin-bottom:2px}
.tape-item .val{font-size:.8rem;font-weight:600;color:var(--text-primary);word-break:break-all}
.receipt-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:11px;margin-bottom:16px}
.metric-tile{background:var(--bg-glass);border:1px solid var(--border-glass);
  border-radius:var(--radius-md);padding:14px 16px;box-shadow:var(--shadow-sm)}
.metric-tile.failed{border-color:var(--state-failed-border);background:var(--state-failed-bg)}
.metric-label{font-size:.71rem;color:var(--text-tertiary);font-weight:600;margin-bottom:5px}
.metric-num{font-size:1.38rem;font-weight:800;font-family:var(--font-mono);
  letter-spacing:-.03em;line-height:1.2}
.metric-num small{display:block;font-size:.58em;font-weight:600;color:var(--text-tertiary);
  letter-spacing:0;margin-top:3px}
.metric-unit{font-size:.72rem;font-weight:600;color:var(--text-tertiary);margin-left:3px}

.stepper-flow{background:var(--bg-glass);border:1px solid var(--border-glass);
  border-radius:var(--radius-md);padding:13px 16px;margin-bottom:16px;overflow-x:auto}
.stepper-inner{display:flex;align-items:center;gap:8px;min-width:max-content}
.step-node{display:flex;align-items:center;gap:6px;font-size:.75rem;font-weight:600;
  color:var(--text-secondary);padding:4px 9px;border-radius:7px;background:var(--bg-subtle)}
.step-node.done{color:var(--state-known-text);background:var(--state-known-bg)}
.step-node.alert{color:var(--state-partial-text);background:var(--state-partial-bg)}
.step-node.todo{color:var(--text-tertiary);opacity:.72}
.step-indicator{font-weight:800}
.step-arrow{color:var(--text-tertiary);font-size:.8rem}

.failed-callout{background:var(--state-failed-bg);border:1px solid var(--state-failed-border);
  border-radius:var(--radius-md);padding:14px 17px;margin-bottom:16px}
.failed-callout-title{font-weight:800;color:var(--state-failed-text);font-size:.85rem;
  margin-bottom:5px;display:flex;align-items:center;gap:6px}
.failed-callout-desc{font-size:.78rem;color:var(--text-secondary);line-height:1.65}
.verify-pill{display:inline-flex;align-items:center;gap:5px;font-size:.72rem;font-weight:600;
  color:var(--state-known-text);background:var(--state-known-bg);padding:3px 9px;
  border-radius:999px;border:1px solid var(--state-known-border)}
.receipt-footer{display:flex;justify-content:space-between;align-items:center;gap:12px;
  flex-wrap:wrap;background:var(--bg-glass);border:1px solid var(--border-glass);
  border-radius:var(--radius-md);padding:13px 17px;font-size:.78rem;
  color:var(--text-secondary);margin-bottom:18px}
.badge-tag{display:inline-block;font-size:.68rem;font-weight:700;padding:2px 7px;
  border-radius:5px;white-space:nowrap}
.badge-tag.success{background:var(--state-known-bg);color:var(--state-known-text)}
.badge-tag.fail{background:var(--state-failed-bg);color:var(--state-failed-text)}
.badge-tag.skip{background:var(--state-unknown-bg);color:var(--state-unknown-text)}
.badge-tag.purge{background:var(--state-unconfirmed-bg);color:var(--state-unconfirmed-text)}
.row-failed td{background:var(--state-failed-bg)}
.hash-code{font-family:var(--font-mono);font-size:.7rem;padding:1px 5px;border-radius:4px;
  background:var(--bg-subtle);border:1px solid var(--border-subtle);word-break:break-all}

/* ---------- responsive ---------- */
@media (min-width:900px){
  .hero-verdict{grid-template-columns:1.55fr 1fr;align-items:start}
  .three-stage{grid-template-columns:repeat(3,1fr)}
}
@media (max-width:760px){
  .container,.receipt-wrapper{padding:0 13px;margin-top:14px}
  .section-block{padding:16px 14px;border-radius:14px}
  .verdict-box{padding:18px 16px 16px;border-radius:14px}
  .verdict-title{font-size:1.18rem}
  .verdict-subtitle{font-size:.82rem}
  .honesty-callout{font-size:.78rem;padding:11px 12px}
  .meta-grid{grid-template-columns:1fr 1fr;gap:9px 11px}
  .section-title{font-size:.94rem}
  .section-note{font-size:.7rem}
  .stat-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .meter-seg{font-size:.62rem}
  .meter-legend-cards{grid-template-columns:repeat(2,minmax(0,1fr))}
  .meter-legend-card.highlight{grid-column:span 2}
  .legend-card-val{font-size:1.04rem}
  .audit-table{font-size:.74rem}
  .audit-table th,.audit-table td{padding:8px 9px}
  .candidate-header{padding:10px 12px}
  .candidate-body{padding:10px 12px 12px}
  .candidate-size{font-size:.9rem}
  .focus-size{font-size:1.2rem}
  .receipt-header h1{font-size:1.15rem}
  .metric-num{font-size:1.2rem}
  .nav-inner{padding:9px 13px;gap:8px}
  .nav-brand{font-size:.86rem}
  .nav-brand .brand-text{display:none}
}
@media (max-width:520px){
  .legend-bar{font-size:.7rem;padding:8px 10px;gap:5px}
  .legend-title{width:auto;margin-bottom:0;font-size:.68rem}
  .legend-bar .hint{display:none}
  .state-badge{font-size:.66rem;padding:2px 6px}
  .meta-grid{grid-template-columns:1fr}
  .media-grid{grid-template-columns:1fr}
  .top-focus-grid{grid-template-columns:1fr}
  .candidates-summary{grid-template-columns:1fr 1fr}
  .receipt-metrics{grid-template-columns:1fr 1fr}
}
@media (max-width:560px){
  /* Six columns cannot be squeezed into 390px.  Turn each row into a
     labelled card so the reader scrolls down, not sideways. */
  .audit-table{display:block;width:100%}
  .audit-table thead{display:none}
  .audit-table tbody{display:block}
  .audit-table tr{display:block;padding:10px 11px;border-bottom:1px solid var(--border-subtle);
    border-left:3px solid transparent}
  .audit-table tr:nth-child(odd){background:var(--bg-subtle)}
  .audit-table tr:last-child{border-bottom:0}
  .audit-table td,.audit-table td.r{display:block;text-align:left;white-space:normal;
    border:0;padding:2px 0;font-size:.78rem}
  .audit-table td[data-label]::before{content:attr(data-label);display:block;
    font-size:.64rem;font-weight:700;letter-spacing:.04em;color:var(--text-tertiary);
    text-transform:uppercase;margin-bottom:1px}
  .audit-table td:first-child{font-size:.84rem}
  .audit-table td:first-child::before{display:none}
  .audit-table td.path{word-break:break-all}
  .audit-table td .app-bar-vis{margin-top:4px}
  .audit-table-wrap{overflow-x:visible;border:0;background:transparent}
  .audit-table tr:hover{background:transparent}
}
@media print{
  .top-nav{position:static}
  body{background:#fff}
  .section-block,.verdict-box,.device-meta-card{break-inside:avoid}
}
"""


# ---------------------------------------------------------------- shared pieces
def _nav(items: list[tuple[str, str]]) -> str:
    links = "".join(f'<a class="nav-link" href="#{_e(key)}">{_e(label)}</a>' for key, label in items)
    return (
        '<header class="top-nav"><div class="nav-inner">'
        '<div class="nav-brand">'
        '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#0284c7" '
        'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M12 8v4"/><path d="M12 16h.01"/>'
        "</svg>"
        '<span class="brand-text">Android 深度清理</span>'
        '<span class="readonly-badge">只读</span>'
        f"</div><nav class=\"nav-links\">{links}</nav></div></header>"
    )


def _legend() -> str:
    pairs = [
        ("known", "已知", "直接引用"),
        ("partial", "部分", "≥ 真实下限"),
        ("unknown", "未知", "读不到，绝非 0"),
        ("unconfirmed", "无法确认", "证据不足"),
        ("failed", "失败", "明确报错"),
    ]
    bad = "".join(
        f'<span class="state-badge {key}">'
        f'<span aria-hidden="true">{_STATE_ICON[key]}</span>{_e(name)}'
        f'<span class="hint">（{_e(hint)}）</span></span>'
        for key, name, hint in pairs
    )
    return (
        '<aside class="legend-bar"><span class="legend-title">'
        '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/>'
        '<path d="M12 8h.01"/></svg>数据可信度：</span>'
        f"{bad}</aside>"
    )


def _icon(paths: str, size: int = 17) -> str:
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
        f'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )


_ICON_SPACE = _icon('<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>')
_ICON_SCAN = _icon('<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M3 12h18"/>')
_ICON_MEDIA = _icon('<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>')
_ICON_APP = _icon('<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>')
_ICON_DIFF = _icon('<path d="M3 12h4l3-8 4 16 3-8h4"/>')
_ICON_BROOM = _icon('<polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>')
_ICON_SHIELD = _icon('<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>')
_ICON_INFO = _icon('<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>', 13)
_ICON_CHECK = _icon('<polyline points="20 6 9 17 4 12"/>', 13)
_ICON_WARN = _icon('<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>', 13)


def _section(anchor: str, title: str, icon: str, body: str, note: str = "", extra: str = "") -> str:
    head = (
        '<div class="section-header"><h2 class="section-title">'
        f"{icon}{_e(title)}</h2>"
        f"{extra}"
        f'<span class="section-note">{_e(note)}</span></div>'
        if note
        else '<div class="section-header"><h2 class="section-title">'
        f"{icon}{_e(title)}</h2>{extra}</div>"
    )
    return f'<section class="section-block" id="{_e(anchor)}">{head}{body}</section>'


def _stat_tiles(pairs: list[tuple[str, str, str]]) -> str:
    """pairs: (label, value_html, extra_class)"""
    tiles = "".join(
        f'<div class="stat-tile"><div class="stat-label">{_e(label)}</div>'
        f'<div class="stat-val">{value}</div></div>'
        for label, value, _cls in pairs
    )
    return f'<div class="stat-grid">{tiles}</div>'


def _c(value: Any, css: str = "") -> tuple[str, str]:
    """A table cell: rendered html plus an extra class."""
    return (str(value), css)


def _table(
    headers: list[str],
    rows: list[list[Any]],
    *,
    min_width: int = 0,
    empty: str = "没有可展示的记录。",
) -> str:
    """A table that turns into stacked key/value cards on narrow screens.

    Every ``td`` carries ``data-label`` so the mobile layout can show the
    column name above the value instead of squeezing six columns into 390px.
    """
    if not rows:
        return f'<div class="note">{_e(empty)}</div>'
    head = "".join(f"<th>{_e(title)}</th>" for title in headers)
    body: list[str] = []
    for row in rows:
        cells: list[str] = []
        for index, cell in enumerate(row):
            if isinstance(cell, tuple):
                html, css = cell
            else:
                html, css = str(cell), ""
            label = headers[index] if index < len(headers) else ""
            cls = f' class="{_e(css)}"' if css else ""
            cells.append(f'<td data-label="{_e(label)}"{cls}>{html}</td>')
        body.append(f"<tr>{''.join(cells)}</tr>")
    style = f' style="min-width:{min_width}px"' if min_width else ""
    return (
        '<div class="audit-table-wrap"><table class="audit-table">'
        f"<thead><tr>{head}</tr></thead>"
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


# ================================================================ analysis
def _device_label(device: dict[str, Any], identity: dict[str, Any], summary: dict[str, Any]) -> str:
    model = " ".join(
        value
        for value in (str(device.get("manufacturer") or ""), str(device.get("model") or ""))
        if value
    ).strip()
    return model or str(identity.get("serial") or "-")


def _hero(
    summary: dict[str, Any],
    device: dict[str, Any],
    identity: dict[str, Any],
    stats: dict[str, Any],
    coverage: dict[str, Any],
    story: dict[str, Any],
) -> str:
    """First screen: what is the situation, and what deserves attention."""
    used = _num(story.get("device_used"))
    known = _num(story.get("known_disjoint_bytes"))
    residual = _num(story.get("unexplained_bytes"))
    residual_pct = _ratio(residual, used) if used else 0.0
    known_pct = _ratio(known, used) if used else 0.0

    device_storage = (summary.get("storage") or {}).get("device_storage") or {}
    total_bytes = _num(device_storage.get("total_bytes"))
    available_bytes = _num(device_storage.get("available_bytes"))

    cand_groups = _num(summary.get("candidate_groups"))
    cand_bytes = _num(summary.get("candidate_bytes"))
    review = _num(summary.get("review_required"))
    cov_status = str(coverage.get("status") or "")
    cov_label, cov_state = _COVERAGE_LABEL.get(cov_status, (cov_status or "-", "unknown"))

    if used and residual:
        title = f"已用 {human_bytes(used)} 中，{residual_pct:.1f}% 无法解释"
        parts = []
        if total_bytes:
            parts.append(f"设备总量 {human_bytes(total_bytes)}")
        parts.append(f"已用 {human_bytes(used)}")
        if available_bytes:
            parts.append(f"可用 {human_bytes(available_bytes)}")
        subtitle = (
            "，".join(parts)
            + f"。本轮 {human_bytes(known)} 有可信证据，"
            + f"剩余 {human_bytes(residual)} 位于系统保护区或无权限目录。"
        )
    elif used:
        title = f"已用 {human_bytes(used)}，本轮已解释 {known_pct:.1f}%"
        subtitle = f"设备已用 {human_bytes(used)}，其中 {human_bytes(known)} 有可信证据。"
    else:
        title = "本轮未能读取设备容量"
        subtitle = "扫描结果仍然有效，但无法把文件大小与设备已用空间做对比。"

    if cand_groups:
        callout = (
            f"<strong>客观结论：</strong>本次共发现 {cand_groups} 组清理候选、"
            f"{_num(summary.get('candidate_files'))} 个文件、{human_bytes(cand_bytes)}，"
            f"其中 {review} 组需要复核。"
        )
        if used and residual:
            callout += (
                f" 未解释的 {human_bytes(residual)} 来自应用私有数据与系统保护区，"
                "普通权限无法穿透 —— 任何声称能清理它的工具都在夸大。"
            )
        callout += " 候选只是证据链，不等于已经删除任何内容。"
    else:
        callout = (
            "<strong>客观结论：</strong>本次没有发现符合规则的清理候选。"
            "这可能是手机确实很干净，也可能是扫描覆盖不足，请先看下方覆盖率与限制说明。"
        )

    strip_items = [
        f"扫描条目 <b>{_num(stats.get('entries')):,}</b>",
        f"文件数 <b>{_num(stats.get('files')):,}</b>",
        f"逻辑大小 <b>{human_bytes(_num(stats.get('bytes')))}</b>",
        f"候选 <b>{cand_groups} 组 / {human_bytes(cand_bytes)}</b>",
    ]
    if coverage.get("unique_bytes") is not None:
        strip_items.append(f"去重后 <b>{_size(coverage.get('unique_bytes'))}</b>")
    strip = '<span class="sep">·</span>'.join(f"<span>{item}</span>" for item in strip_items)

    meta_pairs = [
        ("设备型号", _device_label(device, identity, summary)),
        ("Android 系统", str(summary.get("android") or "-")),
        ("设备序列号", str(identity.get("serial") or "-")),
        ("分析批次", str(summary.get("analysis_id") or "-")),
        ("生成时间", str(summary.get("generated_at") or "-")),
        ("传输方式", str(summary.get("transport") or "-")),
    ]
    meta_html = "".join(
        f'<div><div class="meta-item-label">{_e(label)}</div>'
        f'<div class="meta-item-val">{_e(value)}</div></div>'
        for label, value in meta_pairs
    )

    return (
        '<section class="hero-verdict" id="overview">'
        '<div class="verdict-box">'
        '<div class="verdict-header"><div>'
        f'<h1 class="verdict-title">{_e(title)}</h1>'
        f'<p class="verdict-subtitle">{_e(subtitle)}</p>'
        "</div>"
        + _state(cov_state, f"覆盖率：{cov_label}")
        + "</div>"
        f'<div class="honesty-callout">{callout}</div>'
        '<p style="font-size:.75rem;color:var(--text-tertiary);margin-top:10px;">'
        "只读分析 · 尚未删除、移动或隔离任何文件</p>"
        f'<div class="verdict-strip">{strip}</div>'
        "</div>"
        '<div class="device-meta-card">'
        '<div style="font-weight:700;font-size:.88rem;margin-bottom:12px;display:flex;'
        'justify-content:space-between;align-items:center;gap:8px;">'
        "<span>受检设备档案</span>"
        + _state("known" if cov_status in {"good", "scanned"} else "partial", "ADB 只读")
        + "</div>"
        f'<div class="meta-grid">{meta_html}</div>'
        "</div></section>"
    )


def _space_section(story: dict[str, Any]) -> str:
    if not story:
        return ""
    used = _num(story.get("device_used"))
    known = _num(story.get("known_disjoint_bytes"))
    residual_raw = story.get("unexplained_bytes")
    residual = _num(residual_raw) if residual_raw is not None else 0
    items = list(story.get("items") or [])

    segments: list[str] = []
    legend_cards: list[str] = []
    palette = ["known", "other-known", "known", "other-known"]
    seg_index = 0
    for item in items:
        if not str(item.get("relation") or "").startswith("disjoint-with-"):
            continue
        size = _num(item.get("bytes"))
        if size <= 0:
            continue
        share = _ratio(size, used)
        if share < 0.6:
            # A sub-1% slice is unreadable as a segment; keep it in the cards.
            continue
        css = palette[seg_index % len(palette)]
        seg_index += 1
        if share >= 16:
            label = f"{item.get('label')} {item.get('human')}"
        elif share >= 8:
            label = str(item.get("human"))
        else:
            label = ""
        segments.append(
            f'<div class="meter-seg {css}" style="flex:{share:.4f} 1 0" '
            f'title="{_e(str(item.get("label")))} {_e(str(item.get("human")))}">{_e(label)}</div>'
        )
        legend_cards.append(
            '<div class="meter-legend-card">'
            '<div class="legend-card-header">'
            f'<span class="legend-card-name">{_e(item.get("label"))}</span>'
            + _state("known")
            + "</div>"
            f'<div class="legend-card-val">{_e(item.get("human"))}</div>'
            f'<div class="legend-card-note">{_e(_short(item.get("scope"), 46))}</div>'
            "</div>"
        )

    if used and residual > 0:
        share = _ratio(residual, used)
        if share >= 16:
            seg_text = f"无法解释 {human_bytes(residual)}"
        elif share >= 8:
            seg_text = human_bytes(residual)
        else:
            seg_text = ""
        segments.append(
            f'<div class="meter-seg unexplained" style="flex:{share:.4f} 1 0" '
            f'title="无法解释 {human_bytes(residual)}">{_e(seg_text)}</div>'
        )
        legend_cards.append(
            '<div class="meter-legend-card highlight">'
            '<div class="legend-card-header">'
            '<span class="legend-card-name">无法解释空间</span>'
            + _state("unknown")
            + "</div>"
            f'<div class="legend-card-val">{human_bytes(residual)}</div>'
            '<div class="legend-card-note">系统分区、其他用户、无权限目录与文件系统开销</div>'
            "</div>"
        )

    body: list[str] = []
    if segments:
        cap_left = f"有可信证据：<strong>{human_bytes(known)}（{_pct(known, used)}）</strong>"
        cap_right = (
            f'<span style="font-weight:700;color:var(--state-unknown-text);">'
            f"无法解释：{human_bytes(residual)}（{_pct(residual, used)}）</span>"
            if residual
            else ""
        )
        body.append(
            f'<div class="meter-caption"><span>{cap_left}</span>{cap_right}</div>'
            f'<div class="meter-bar">{"".join(segments)}</div>'
            f'<div class="meter-legend-cards">{"".join(legend_cards)}</div>'
        )
    else:
        body.append(
            '<div class="note warn"><span class="note-title">缺少设备容量数据</span>'
            "本轮未能读取 /data 的可用空间，因此无法给出「已用空间构成」比例。"
            "下方仍列出各来源的绝对大小与口径。</div>"
        )

    rows: list[list[Any]] = []
    for item in items:
        relation = str(item.get("relation") or "")
        rel_label = _RELATION_LABEL.get(relation, "-")
        if relation == "total":
            continue
        if relation.startswith("disjoint-with-"):
            state = "known"
            if item.get("key") == "app_internal":
                state = "partial"
        elif relation in {"subset-of-file-scan", "overlaps-file-scan"}:
            state = "known"
        else:
            state = "unknown"
        rows.append(
            [
                f'<strong>{_e(item.get("label"))}</strong>',
                _c(_e(item.get("human")), "r mono"),
                _state(state),
                _e(item.get("measure")),
                _c(_e(item.get("scope")), "path"),
                f'<span class="overlap-tag">{_e(rel_label)}</span>',
            ]
        )
    body.append(
        '<div style="margin-top:18px">'
        + _table(
            ["统计来源", "展示大小", "状态", "口径算法", "覆盖范围", "与其他项关系"],
            rows,
        )
        + "</div>"
    )

    caveats = list(story.get("caveats") or [])
    if caveats:
        lis = "".join(f"<li>{_e(text)}</li>" for text in caveats)
        body.append(
            '<div class="math-warning"><strong>为什么这些数字不能直接相加？</strong>'
            f"<ol>{lis}</ol></div>"
        )

    note = f"设备已用 {human_bytes(used)}" if used else ""
    return _section("space", "空间去向速览", _ICON_SPACE, "".join(body), note)


def _coverage_section(coverage: dict[str, Any]) -> str:
    if not coverage:
        return ""
    logical = _num(coverage.get("logical_bytes"))
    unique = coverage.get("unique_bytes")
    dirs = _num(coverage.get("dir_count"))
    files = _num(coverage.get("files"))
    status = str(coverage.get("status") or "")
    label, state = _COVERAGE_LABEL.get(status, (status or "-", "unknown"))

    body: list[str] = [
        '<p class="section-lead">'
        f"本次遍历 {files:,} 个文件、{dirs:,} 个目录。逻辑大小 {human_bytes(logical)}"
        + (f"，按 (设备, inode) 去重后 {_size(unique)}" if unique is not None else "")
        + "。覆盖率决定下面的结论能信到什么程度。</p>"
    ]

    tiles = [
        ("覆盖率", _state(state, label), ""),
        ("文件数", f"{files:,}", ""),
        ("逻辑大小", human_bytes(logical), ""),
        ("去重后大小", _size(unique) if unique is not None else "未知", ""),
    ]
    if coverage.get("hardlink_saved_bytes"):
        tiles.append(("硬链接节省", human_bytes(_num(coverage.get("hardlink_saved_bytes"))), ""))
    body.append(_stat_tiles(tiles))

    flags = []
    if coverage.get("truncated"):
        flags.append("本轮扫描达到条目上限，已截断；未扫到的部分不在统计内。")
    if coverage.get("depth_limited"):
        flags.append(
            f"部分目录超过最大深度 {_num(coverage.get('max_depth'))} 层，其子目录未展开。"
        )
    if coverage.get("max_files"):
        flags.append(f"单目录文件上限为 {_num(coverage.get('max_files')):,} 个。")

    roots = list(coverage.get("root_status") or [])
    if roots:
        rows = []
        for item in roots:
            r_label, r_state = _COVERAGE_LABEL.get(
                str(item.get("state") or ""), (str(item.get("state") or "-"), "unknown")
            )
            rows.append(
                [
                    _c(_e(item.get("path")), "path"),
                    _state(r_state, r_label),
                    _e(item.get("reason") or "-"),
                ]
            )
        body.append(
            '<div style="margin-top:6px">'
            + _table(["扫描根目录", "状态", "说明"], rows)
            + "</div>"
        )

    largest = list(coverage.get("largest_dirs") or [])[:MAX_DIR_ROWS]
    if largest:
        top = max(_num(row.get("bytes")) for row in largest) or 1
        rows = []
        for item in largest:
            width = _ratio(item.get("bytes"), top)
            rows.append(
                [
                    _c(_e(item.get("path")), "path"),
                    _c(_size(item.get("bytes")), "r mono"),
                    _c(f"{_num(item.get('files')):,}", "r mono"),
                    f'<div class="app-bar-vis"><div class="app-bar-inner" '
                    f'style="width:{width:.1f}%"></div></div>',
                ]
            )
        body.append(
            '<h3 style="font-size:.84rem;font-weight:700;margin:18px 0 8px;'
            'color:var(--text-secondary);">占用最大的目录</h3>'
            + _table(["目录", "逻辑大小", "文件数", "相对比例"], rows)
        )

    reasons = list(coverage.get("reasons") or [])
    skipped = list(coverage.get("skipped_paths") or [])
    if reasons or skipped or flags:
        lis = "".join(f"<li>{_e(text)}</li>" for text in flags)
        lis += "".join(f"<li>{_e(text)}</li>" for text in reasons)
        if skipped:
            shown = "、".join(str(p) for p in skipped[:20])
            more = f"（共 {len(skipped)} 条）" if len(skipped) > 20 else ""
            lis += f"<li>跳过路径{more}：<code>{_e(shown)}</code></li>"
        body.append(
            f'<div class="note warn"><span class="note-title">覆盖限制</span><ul>{lis}</ul></div>'
        )

    return _section("coverage", "扫描覆盖", _ICON_SCAN, "".join(body), f"状态：{label}")


def _media_section(media: dict[str, Any]) -> str:
    if not media:
        return ""
    status = str(media.get("status") or "not_collected")
    index = media.get("index") or {}
    cross = media.get("crosscheck") or {}
    collections = list(media.get("collections") or [])

    body: list[str] = [
        '<p class="section-lead">相册由系统媒体数据库驱动：数据库里有的，物理文件不一定还在；'
        "物理文件在，数据库也不一定刷得出来。下面是逐集合的真实对账。</p>"
    ]
    if collections:
        # Global scale so a small collection never looks large.
        peak = max(
            [max(_num(c.get("rows")), _num(c.get("scan_files"))) for c in collections] or [1]
        ) or 1
        cards = []
        for item in collections:
            name = str(item.get("name") or "")
            c_status = str(item.get("status") or "")
            c_label, c_state = _MEDIA_COLLECTION.get(c_status, (c_status or "-", "unknown"))
            rows_n = _num(item.get("rows"))
            scan_n = _num(item.get("scan_files"))
            idx_w = max(_ratio(rows_n, peak), 2.0) if rows_n else 0.0
            scan_w = max(_ratio(scan_n, peak), 2.0) if scan_n else 0.0
            cls = "media-card"
            if c_status == "zero_unconfirmed":
                cls += " discrepancy"
            elif c_status in {"zero_mismatch", "failed"}:
                cls += " broken"

            if c_status == "failed":
                err = str(item.get("error") or "无法读取该集合")
                if len(err) > 110:
                    err = err[:110].rstrip() + "…"
                foot = (
                    f'<div class="media-card-foot warn"><strong>查询失败：</strong>{_e(err)}'
                    '<br><span style="color:var(--text-tertiary)">完整命令与错误见 '
                    "sources.json</span></div>"
                )
            elif c_status == "zero_mismatch":
                foot = (
                    '<div class="media-card-foot warn"><strong>对不上：</strong>'
                    f"索引 0 条，但扫描看到 {scan_n:,} 个同类文件。可能是权限、Provider 或索引延迟，"
                    "不能据此认为媒体库为空。</div>"
                )
            elif c_status == "zero_unconfirmed":
                foot = (
                    '<div class="media-card-foot warn"><strong>无法确认：</strong>'
                    f"索引 0 条，扫描只看到 {scan_n:,} 个同类文件（低于异常阈值）。"
                    "既不能认定为空，也不构成异常。</div>"
                )
            elif c_status == "empty":
                foot = '<div class="media-card-foot">索引 0 条，文件扫描也没有可参与核对的同类文件。</div>'
            elif c_status == "skipped":
                foot = '<div class="media-card-foot">本轮跳过该集合，未做核对。</div>'
            else:
                delta = scan_n - rows_n
                direction = "索引多" if delta < 0 else "扫描多"
                foot = (
                    f'<div class="media-card-foot">与文件扫描相差 {abs(delta):,} 条'
                    f"（{direction}）。该差异属于正常的索引延迟范围。</div>"
                    if delta
                    else '<div class="media-card-foot">与文件扫描完全一致。</div>'
                )

            cards.append(
                f'<div class="{cls}">'
                '<div class="media-card-top">'
                f'<span class="media-card-title">{_e(item.get("label") or _COLLECTION_NAME.get(name, name))}</span>'
                f"{_state(c_state, c_label)}</div>"
                '<div class="compare-row"><span class="compare-label">索引</span>'
                f'<span class="compare-bar-track"><span class="compare-bar-fill idx" '
                f'style="width:{idx_w:.1f}%"></span></span>'
                f'<span class="compare-val">{rows_n:,}</span></div>'
                '<div class="compare-row"><span class="compare-label">扫描</span>'
                f'<span class="compare-bar-track"><span class="compare-bar-fill scan" '
                f'style="width:{scan_w:.1f}%"></span></span>'
                f'<span class="compare-val">{scan_n:,}</span></div>'
                f"{foot}</div>"
            )
        body.append(f'<div class="media-grid">{"".join(cards)}</div>')
        body.append(
            '<div class="note" style="font-size:.72rem;">条形长度按全部集合的最大值归一化，'
            "所以视频、音频的条很短是真实比例，不是渲染错误。</div>"
        )

    collection_cross = media.get("collection_crosscheck") or {}
    for key, title, css, text in (
        (
            "zero_mismatch",
            "零记录异常",
            "danger",
            "索引返回 0 条，但文件扫描发现同类文件达到异常阈值。不能认定媒体库为空。",
        ),
        (
            "zero_unconfirmed",
            "零记录待确认",
            "unconfirmed",
            "索引返回 0 条，文件扫描只发现少量同类文件。既不能认定为空，也不判定为异常。",
        ),
    ):
        names = collection_cross.get(key) or []
        if not names:
            continue
        listed = "、".join(_COLLECTION_NAME.get(str(n), str(n)) for n in names)
        body.append(
            f'<div class="note {css}"><span class="note-title">{title}</span>'
            f"{_e(listed)}：{_e(text)}</div>"
        )

    if not media.get("available"):
        body.append(
            '<div class="note"><span class="note-title">媒体索引不可用</span>'
            "这不影响文件扫描结果，但本轮无法解释媒体库与文件系统之间的差异。</div>"
        )
        return _section(
            "media",
            "媒体索引核对",
            _ICON_MEDIA,
            "".join(body),
            str(media.get("reason") or _MEDIA_STATUS_LABEL.get(status, status)),
        )

    body.append(
        _stat_tiles(
            [
                ("索引条目", f"{_num(index.get('items')):,}", ""),
                ("声明大小", _e(index.get("bytes_human") or _size(index.get("bytes"))), ""),
                ("可解析路径", f"{_num(index.get('path_known')):,}", ""),
                (
                    "范围内匹配率",
                    f"{float(cross.get('match_ratio') or 0) * 100:.1f}%" if cross.get("match_ratio") is not None else "-",
                    "",
                ),
                ("范围内索引", f"{_num(cross.get('index_in_scope')):,}", ""),
                ("索引有·扫描无", f"{_num(cross.get('index_only_in_scope')):,}", ""),
                ("扫描有·索引无", f"{_num(cross.get('scan_only')):,}", ""),
                ("仅大小写不同", f"{_num(cross.get('case_mismatch')):,}", ""),
            ]
        )
    )
    body.append(
        '<div class="note" style="font-size:.72rem;">「范围内匹配率」只统计路径能解析、'
        "且落在扫描范围内的索引条目；「扫描有·索引无」包含范围外与隐藏路径，"
        "两者口径不同，所以匹配率 100% 时仍可能有未匹配的扫描文件。</div>"
    )

    samples = cross.get("samples") or {}
    for key, title in (
        ("index_only", "索引里有、文件扫描找不到（范围内）"),
        ("index_only_outside", "索引里有、文件扫描找不到（范围外）"),
        ("size_mismatch", "索引与文件大小不一致"),
        ("scan_only", "文件扫描有、媒体索引没有"),
    ):
        rows = list(samples.get(key) or [])[:MAX_MEDIA_SAMPLES]
        if not rows:
            continue
        trs = []
        for row in rows:
            names = "、".join(
                _COLLECTION_NAME.get(str(n), str(n)) for n in (row.get("collections") or [])
            ) or "-"
            trs.append(
                [
                    _c(_e(row.get("path")), "path"),
                    _c(_e(row.get("human_size") or "-"), "r mono"),
                    _e(names),
                    _e(row.get("reason") or "-"),
                ]
            )
        body.append(
            f'<h3 style="font-size:.84rem;font-weight:700;margin:18px 0 8px;'
            f'color:var(--text-secondary);">{_e(title)}</h3>'
            + _table(["路径", "索引大小", "集合", "说明"], trs)
        )

    limits = list(media.get("limitations") or [])
    if limits:
        lis = "".join(f"<li>{_e(_short(text, 220))}</li>" for text in limits)
        body.append(
            f'<div class="note"><span class="note-title">媒体索引限制</span><ul>{lis}</ul>'
            '<div style="margin-top:6px;color:var(--text-tertiary);font-size:.72rem;">'
            "完整查询命令与原始错误见 <code>scan/aux/sources.json</code>。</div></div>"
        )

    note = f"索引 {_num(index.get('items')):,} 条 · 声明 {index.get('bytes_human') or _size(index.get('bytes'))}"
    return _section("media", "媒体索引核对", _ICON_MEDIA, "".join(body), note)


def _app_section(app: dict[str, Any]) -> str:
    if not app:
        return ""
    known = app.get("known") or {}
    packages = app.get("packages") or {}
    unknown = app.get("unknown") or {}
    status = str(app.get("status") or "not_collected")

    body: list[str] = [
        '<p class="section-lead">应用占用由 APK 实体、外部 data/media/obb 与内部 data 组成。'
        "内部 data 需要 root/su 才能读取，读不到时标为未知，绝不写成 0。</p>"
    ]

    if not app.get("available"):
        body.append(
            '<div class="note warn"><span class="note-title">应用级存储本轮不可用</span>'
            f"{_e(app.get('reason') or '未采集到应用包信息。')}</div>"
        )
        return _section(
            "apps",
            "应用级存储",
            _ICON_APP,
            "".join(body),
            _MEDIA_STATUS_LABEL.get(status, status),
        )

    # A 0 B internal value while the reason says "unreadable" would read as
    # "this app has no data".  Keep the tile honest.
    internal_tile = known.get("internal_data_human") or "未知"
    if unknown.get("internal_data") and internal_tile in {"0 B", "0", ""}:
        internal_tile = "未知"
    cache_tile = known.get("internal_cache_human") or "未知"
    if unknown.get("internal_cache") and cache_tile in {"0 B", "0", ""}:
        cache_tile = "未知"

    body.append(
        _stat_tiles(
            [
                ("应用包", f"{_num(packages.get('total')):,}", ""),
                ("基础 APK", _e(known.get("apk_base_human") or known.get("apk_human") or "未知"), ""),
                ("Split APK", _e(known.get("apk_split_human") or "未知"), ""),
                ("APK 文件数", f"{_num(known.get('apk_file_count')):,}", ""),
                ("外部 data", _e(known.get("external_data_human") or "未知"), ""),
                ("外部 media", _e(known.get("external_media_human") or "未知"), ""),
                ("外部 obb", _e(known.get("external_obb_human") or "未知"), ""),
                ("内部 data", _e(internal_tile), ""),
            ]
        )
    )

    unknowns: list[str] = []
    if unknown.get("internal_data"):
        text = f"<strong>内部 data：</strong>{_e(unknown.get('internal_data'))}"
        cache_note = str(unknown.get("internal_cache") or "")
        if cache_note and cache_note not in {"已读取", "未采集"}:
            text += f"；内部 cache：{_e(cache_note)}"
        else:
            text += "；内部 cache 是 data 的子集。"
        unknowns.append(text)
    if unknown.get("apk_base"):
        unknowns.append(
            f"基础 APK：{_e(unknown.get('apk_base'))}；"
            "split APK 单独统计，不折算进基础 APK，因此基础 APK 合计是下限。"
        )
    if unknown.get("split_apk"):
        unknowns.append(f"<strong>Split APK：</strong>{_e(unknown.get('split_apk'))}")
    for text in unknowns:
        body.append(f'<div class="note">{text}</div>')

    top = list(app.get("top_external") or [])[:MAX_APP_ROWS]
    if top:
        peak = max(_num(row.get("external_total_bytes")) for row in top) or 1
        trs = []
        for row in top:
            pkg = str(row.get("package") or "")
            residue = bool(row.get("residue")) or str(row.get("label") or "").endswith("(已卸载)")
            inner_cls = "app-bar-inner caution" if residue else "app-bar-inner"
            internal = row.get("internal_data_human")
            internal_cell = (
                f'<span class="mono">{_e(internal)}</span>'
                if internal and internal != "0 B"
                else _state("unknown")
            )
            trs.append(
                [
                    f'<div class="app-name-block">'
                    f'<span class="app-cn-name">{_e(row.get("label") or pkg)}</span>'
                    f'<span class="app-pkg-name">{_e(pkg)}</span></div>',
                    _c(_e(row.get("external_total_human")), "mono"),
                    f'<div class="app-bar-vis"><div class="{inner_cls}" '
                    f'style="width:{_ratio(row.get("external_total_bytes"), peak):.1f}%"></div></div>',
                    _c(_e(row.get("data_human")), "mono"),
                    _c(_e(row.get("media_human")), "mono"),
                    _c(_e(row.get("obb_human")), "mono"),
                    _c(_e(row.get("apk_human")), "mono"),
                    internal_cell,
                ]
            )
        body.append(
            f'<h3 style="font-size:.84rem;font-weight:700;margin:18px 0 8px;'
            f'color:var(--text-secondary);">外部存储占用 Top {len(top)}</h3>'
            + _table(
                ["应用 / 包名", "外部合计", "相对比例", "data", "media", "obb", "APK", "内部 data"],
                trs,
            )
            + '<div class="note" style="font-size:.73rem;">表格中的「未知」表示该应用内部 data '
            "读不到，不是 0 字节。带橙色比例条的行是已卸载但仍保留外部目录的包。</div>"
        )

    residue = list(app.get("residue") or [])[:MAX_APP_ROWS]
    if residue:
        trs = [
            [
                _c(_e(row.get("package")), "path"),
                _c(_e(row.get("external_total_human")), "r mono"),
                '<span class="overlap-tag">仅线索</span>',
            ]
            for row in residue
        ]
        body.append(
            '<h3 style="font-size:.84rem;font-weight:700;margin:18px 0 8px;'
            'color:var(--text-secondary);">已卸载但目录仍在的包</h3>'
            + _table(["包名", "外部合计", "性质"], trs)
        )

    limits = list(app.get("limitations") or [])
    if limits:
        lis = "".join(f"<li>{_e(text)}</li>" for text in limits)
        body.append(
            f'<div class="note"><span class="note-title">应用级存储限制</span><ul>{lis}</ul></div>'
        )

    note = f"{_num(packages.get('total'))} 个应用 · 已知合计 {known.get('total_human') or '-'}"
    return _section("apps", "应用级存储", _ICON_APP, "".join(body), note)


def _diff_section(diff: dict[str, Any]) -> str:
    if not diff:
        return ""
    summary = diff.get("summary") or {}
    body: list[str] = []
    if diff.get("baseline"):
        body.append(
            '<div class="note"><span class="note-title">本轮已建立目录基线</span>'
            "下一次分析会显示新增、增长、缩小、消失与复活，并与本轮对比。</div>"
        )
        return _section("diff", "跨轮目录变化", _ICON_DIFF, "".join(body), "基线已建立")

    body.append(
        _stat_tiles(
            [
                ("新增目录", f"{_num(summary.get('new')):,}", ""),
                ("增长明显", f"{_num(summary.get('grew')):,}", ""),
                ("缩小", f"{_num(summary.get('shrunk')):,}", ""),
                ("未再出现", f"{_num(summary.get('vanished')):,}", ""),
                ("删后复活", f"{_num(summary.get('resurrected')):,}", ""),
                ("疑似残留", f"{_num(summary.get('orphan_suspect')):,}", ""),
            ]
        )
    )

    for key, title in (
        ("grew", "增长明显"),
        ("new", "新增目录"),
        ("resurrected", "删后复活"),
        ("orphan_suspect", "疑似卸载残留"),
        ("app_updated", "应用版本变化"),
    ):
        rows = list(diff.get(key) or [])[:MAX_DIFF_ROWS]
        if not rows:
            continue
        trs = []
        for row in rows:
            if row.get("reason"):
                detail = str(row.get("reason"))
            elif key == "grew":
                detail = f"+{row.get('delta_human') or human_bytes(_num(row.get('delta_bytes')))}"
            else:
                detail = f"{_num(row.get('files'))} 个文件"
            trs.append(
                [
                    _c(_e(row.get("path")), "path"),
                    _c(_e(row.get("human_size") or _size(row.get("bytes"))), "r mono"),
                    _e(detail),
                ]
            )
        body.append(
            f'<h3 style="font-size:.84rem;font-weight:700;margin:18px 0 8px;'
            f'color:var(--text-secondary);">{_e(title)}</h3>'
            + _table(["路径", "当前大小", "线索"], trs)
        )

    body.append(
        '<div class="note">目录档案只提供线索，不改变风险等级，也不构成删除授权。</div>'
    )
    return _section("diff", "跨轮目录变化", _ICON_DIFF, "".join(body))


def _focus_cards(items: list[dict[str, Any]]) -> tuple[str, set[str]]:
    """Three cards that answer "which groups actually deserve my attention".

    Returns the markup and the ids of the picked groups so the detail list can
    keep exactly those expanded.
    """
    if not items:
        return "", set()
    pool = [item for item in items if _num(item.get("total_bytes")) > 0] or list(items)
    picks: list[tuple[str, dict[str, Any]]] = []

    largest = max(pool, key=lambda x: _num(x.get("total_bytes")))
    picks.append(("largest", largest))

    rest = [item for item in pool if item is not largest]
    if rest:
        riskiest = max(
            rest,
            key=lambda x: (RISK_ORDER.get(str(x.get("risk") or "").upper(), 0), _num(x.get("total_bytes"))),
        )
        if RISK_ORDER.get(str(riskiest.get("risk") or "").upper(), 0) >= RISK_ORDER.get(
            str(largest.get("risk") or "").upper(), 0
        ):
            picks.append(("riskiest", riskiest))
            rest = [item for item in rest if item is not riskiest]

    reviewable = [item for item in rest if item.get("ai_review_required")]
    if reviewable:
        # Prefer a third card from a different category: repeating the same
        # category three times tells the reader nothing new.
        used_cats = {str(item.get("category") or "") for _, item in picks}
        fresh = [item for item in reviewable if str(item.get("category") or "") not in used_cats]
        pool3 = fresh or reviewable
        picks.append(("review", max(pool3, key=lambda x: _num(x.get("total_bytes")))))

    if not picks:
        return "", set()

    tags = {
        "largest": ("体积最大", "#0284c7"),
        "riskiest": ("风险最高", "var(--risk-caution-text)"),
        "review": ("需人工复核", "var(--state-unconfirmed-text)"),
    }
    cards = []
    picked: set[str] = set()
    for kind, item in picks:
        tag, color = tags[kind]
        if item.get("id"):
            picked.add(str(item.get("id")))
        size_text = str(item.get("human_size") or "未知")
        head, _, unit = size_text.rpartition(" ")
        if not head:
            head, unit = size_text, ""
        reason = (item.get("explain") or {}).get("why") or (item.get("reasons") or [""])[0]
        cards.append(
            f'<div class="focus-card {kind}">'
            f'<span class="focus-tag">{_e(tag)}</span>'
            f'<div class="focus-cat">{_e(item.get("category_title") or "-")} · '
            f'{_e(item.get("file_count") or 0)} 个文件</div>'
            f'<div class="focus-path">{_e(_short(item.get("primary_path") or item.get("title"), 64))}</div>'
            f'<div class="focus-size" style="color:{color}">{_e(head)}'
            f'<small>{_e(unit)}</small></div>'
            f'<div class="focus-desc">{_e(_short(reason, 88))}</div>'
            "</div>"
        )
    html = (
        '<div class="focus-title">'
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
        "10 秒聚焦：最值得先看的三组</div>"
        f'<div class="top-focus-grid">{"".join(cards)}</div>'
    )
    return html, picked


def _candidate_section(items: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    groups = _num(summary.get("candidate_groups"))
    files = _num(summary.get("candidate_files"))
    total_bytes = _num(summary.get("candidate_bytes"))
    review = _num(summary.get("review_required"))
    categories = list(summary.get("categories") or [])

    body: list[str] = [
        '<p class="section-lead">每组候选都附「这是什么 / 为什么列出 / 删除影响」三段解释。'
        "候选不是删除指令，必须经过复核、选择与确认令牌才会执行。</p>"
    ]

    top_cat = ""
    if categories:
        biggest = max(categories, key=lambda row: _num(row.get("bytes")))
        top_cat = str(biggest.get("title") or "")

    body.append(
        '<div class="candidates-summary">'
        '<div class="candidate-stat-card"><div class="c-stat-label">候选总体积</div>'
        f'<div class="c-stat-val" style="color:var(--accent)">{_e(human_bytes(total_bytes))}</div></div>'
        '<div class="candidate-stat-card"><div class="c-stat-label">候选组 / 文件</div>'
        f'<div class="c-stat-val">{groups}<small>组</small> '
        f'<small style="color:var(--text-tertiary)">/ {files:,} 个</small></div></div>'
        f'<div class="candidate-stat-card"><div class="c-stat-label">需要复核</div>'
        f'<div class="c-stat-val" style="color:var(--state-partial-text)">{review}'
        '<small>组</small></div></div>'
        f'<div class="candidate-stat-card"><div class="c-stat-label">最大分类</div>'
        f'<div class="c-stat-val" style="font-size:.98rem">{_e(top_cat or "-")}</div></div>'
        "</div>"
    )

    focus_html, picked = _focus_cards(items)
    body.append(focus_html)

    if categories:
        peak = max(_num(row.get("bytes")) for row in categories) or 1
        trs = []
        for row in categories:
            risk = str(row.get("risk") or "SAFE").upper()
            trs.append(
                [
                    f'<strong>{_e(row.get("title"))}</strong>',
                    _c(_num(row.get("groups")), "r mono"),
                    _c(f"{_num(row.get('files')):,}", "r mono"),
                    _c(_size(row.get("bytes")), "r mono"),
                    f'<div class="app-bar-vis"><div class="app-bar-inner" '
                    f'style="width:{_ratio(row.get("bytes"), peak):.1f}%"></div></div>',
                    _risk_badge(risk),
                ]
            )
        body.append(
            '<h3 style="font-size:.84rem;font-weight:700;margin:6px 0 8px;'
            'color:var(--text-secondary);">分类分布</h3>'
            + _table(["分类", "组数", "文件", "逻辑大小", "相对比例", "最高风险"], trs)
        )

    # Largest first: a reader scrolling a 45-row list should meet the big
    # items before the 3 KB leftovers.
    ordered = sorted(items, key=lambda x: -_num(x.get("total_bytes")))
    shown = ordered[:MAX_ITEMS]
    body.append(
        '<h3 style="font-size:.84rem;font-weight:700;margin:22px 0 8px;'
        'color:var(--text-secondary);">逐组候选明细</h3>'
    )
    if not shown:
        body.append(
            '<div class="note"><span class="note-title">未发现候选</span>'
            "本轮没有命中任何清理规则。如果这与预期不符，请先检查覆盖率。</div>"
        )

    for item in shown:
        risk = str(item.get("risk") or "CAUTION").upper()
        explain = item.get("explain") or {}
        confidence = float(item.get("confidence") or 0)
        meta_bits = [
            f'<span>ID {_e(item.get("id"))}</span>' if item.get("id") else "",
            f'<span>{_e(item.get("category_title"))}</span>' if item.get("category_title") else "",
            f'<span>动作 {_e(item.get("action"))}</span>' if item.get("action") else "",
            f'<span>{_num(item.get("file_count")):,} 文件 / {_num(item.get("dir_count")):,} 目录</span>',
            f'<span>{"可选" if item.get("selectable") else "不可选"}</span>',
        ]
        meta_html = "".join(bit for bit in meta_bits if bit)

        stages = []
        for label, icon, key in (
            ("这是什么", _ICON_INFO, "what"),
            ("为什么列出", _ICON_CHECK, "why"),
            ("删除影响", _ICON_WARN, "impact"),
        ):
            text = str(explain.get(key) or "").strip()
            if not text:
                continue
            stages.append(
                '<div class="stage-item">'
                f'<div class="stage-label">{icon}{_e(label)}</div>'
                f'<div class="stage-content">{_e(text)}</div></div>'
            )

        badges = [_risk_badge(risk)]
        badges.append(_state("known" if confidence >= 0.7 else "partial", f"置信度 {confidence:.2f}"))
        if item.get("ai_review_required"):
            badges.append(_state("partial", "需复核"))

        detail: list[str] = []
        paths = [str(p) for p in (item.get("sample_paths") or [])[:6]]
        evidence = list(item.get("evidence") or [])[:8]
        reasons = [str(r) for r in (item.get("reasons") or [])[:5]]
        if paths:
            lis = "".join(f"<li>{_e(p)}</li>" for p in paths)
            detail.append(
                '<div class="evidence-box"><div class="evidence-box-title">'
                f"路径示例（共 {_num(item.get('file_count')):,} 个文件）</div>"
                f"<ul>{lis}</ul></div>"
            )
        if evidence:
            lis = "".join(
                f'<li class="plain">{_e(entry.get("text") if isinstance(entry, dict) else entry)}</li>'
                for entry in evidence
            )
            detail.append(
                '<div class="evidence-box"><div class="evidence-box-title">证据</div>'
                f"<ul>{lis}</ul></div>"
            )
        if reasons:
            lis = "".join(f'<li class="plain">{_e(text)}</li>' for text in reasons)
            detail.append(
                '<div class="evidence-box"><div class="evidence-box-title">判断理由</div>'
                f"<ul>{lis}</ul></div>"
            )
        if item.get("keep_suggestion"):
            detail.append(
                f'<div class="note"><span class="note-title">保留建议</span>'
                f'{_e(item.get("keep_suggestion"))}</div>'
            )

        is_open = " open" if str(item.get("id")) in picked else ""
        body.append(
            f'<details class="candidate-item"{is_open}>'
            '<summary class="candidate-header"><div class="candidate-meta-left">'
            f'{"".join(badges)}'
            f'<span class="candidate-title">{_e(item.get("title"))}</span>'
            "</div>"
            f'<div class="candidate-size">{_e(item.get("human_size"))}</div></summary>'
            '<div class="candidate-body">'
            + (f'<div class="three-stage">{"".join(stages)}</div>' if stages else "")
            + f'<div class="candidate-meta">{meta_html}</div>'
            + "".join(detail)
            + "</div></details>"
        )

    if len(items) > len(shown):
        body.append(
            f'<div class="note">本页只渲染前 {len(shown)} 组；'
            f"其余 {len(items) - len(shown)} 组请在 <code>analysis.db</code> 或 "
            "<code>analysis.json</code> 中查看，字段与本页一致。</div>"
        )

    protected = list(summary.get("protected_items") or [])
    if protected:
        trs = [
            [
                _c(_e(row.get("path")), "path"),
                _c(_size(row.get("size")), "r mono"),
                _e(row.get("reason")),
            ]
            for row in protected
        ]
        body.append(
            '<h3 style="font-size:.84rem;font-weight:700;margin:22px 0 8px;'
            'color:var(--text-secondary);">受保护内容（不可选，仅展示）</h3>'
            + _table(["路径", "大小", "原因"], trs)
        )

    note = f"{groups} 组 / {files:,} 个文件 / {human_bytes(total_bytes)}"
    return _section(
        "candidates",
        "清理候选",
        _ICON_BROOM,
        "".join(body),
        note,
        extra=_state("partial", f"{review} 组需复核") if review else "",
    )


def _limitations_section(summary: dict[str, Any], analysis: dict[str, Any]) -> str:
    limitations = list(summary.get("limitations") or analysis.get("limitations") or [])
    if limitations:
        lis = "".join(f"<li>{_e(text)}</li>" for text in limitations)
        body = f'<ul class="bullet-list">{lis}</ul>'

    else:
        body = '<p class="section-lead">本轮没有额外的数据限制。</p>'

    body += (
        '<div class="note" style="margin-top:16px">'
        '<span class="note-title">安全边界</span><ul>'
        "<li>扫描与分析全程只读，本报告不代表已经删除、移动或隔离任何文件。</li>"
        "<li>候选必须经过 AI 复核、用户选择、确认令牌与执行前文件校验。</li>"
        "<li>媒体索引、应用级存储与目录档案只作为证据，不授权删除。</li>"
        "<li>本报告包含设备序列号与文件路径，对外分享前请自行脱敏。</li>"
        "</ul></div>"
    )
    return (
        '<section class="security-footer-box" id="limits">'
        '<div class="security-title">'
        + _ICON_SHIELD
        + "限制与缺口 · 安全边界（不可协商）</div>"
        + body
        + "</section>"
    )


def render_analysis_html(analysis: dict[str, Any], config: dict[str, Any] | None = None) -> str:
    summary = analysis.get("summary") or {}
    items = list(analysis.get("items") or [])
    device = summary.get("device") or analysis.get("device") or {}
    identity = analysis.get("device_identity") or summary.get("device_identity") or {}
    stats = summary.get("stats") or {}
    storage = summary.get("storage") or {}
    coverage = summary.get("coverage") or {}
    story = summary.get("space_story") or {}
    media = summary.get("media_index") or {}
    app = summary.get("app_storage") or {}
    diff = summary.get("directory_changes") or {}

    platform = str(analysis.get("platform") or "android")
    title = "HarmonyOS 存储深度分析报告" if platform.startswith("harmonyos") else "Android 存储深度分析报告"

    nav_items = [("overview", "总览")]
    if story:
        nav_items.append(("space", "空间去向"))
    if coverage:
        nav_items.append(("coverage", "扫描覆盖"))
    if media:
        nav_items.append(("media", "媒体索引"))
    if app:
        nav_items.append(("apps", "应用存储"))
    if diff:
        nav_items.append(("diff", "目录变化"))
    nav_items.append(("candidates", "清理候选"))
    nav_items.append(("limits", "限制"))

    body = "".join(
        [
            _hero(summary, device, identity, stats, coverage, story),
            _space_section(story),
            _coverage_section(coverage),
            _media_section(media),
            _app_section(app),
            _diff_section(diff),
            _candidate_section(items, summary),
            _limitations_section(summary, analysis),
        ]
    )

    return (
        "<!DOCTYPE html>"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{_e(title)} - {_e(_device_label(device, identity, summary))}</title>"
        f"<style>{_CSS}</style></head><body>"
        + _nav(nav_items)
        + '<main class="container">'
        + _legend()
        + body
        + '<footer class="foot">本报告由 android-deep-clean 生成，全程只读。'
        "「可释放空间」是逻辑大小估算，隔离不会立即释放空间。<br>"
        "报告包含设备序列号与内部路径，分享前请自行脱敏。</footer>"
        "</main></body></html>"
    )


def write_analysis_html(
    analysis: dict[str, Any], path: Any, config: dict[str, Any] | None = None
) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_analysis_html(analysis, config), encoding="utf-8")
    return path


# ================================================================ cleanup
_CLEAN_STATUS = {
    "completed": ("已完成", "safe"),
    "partial": ("部分完成", "partial"),
    "aborted": ("已中止", "danger"),
    "purged": ("已永久释放", "safe"),
}
_ITEM_STATUS = {
    "success": ("成功", "known"),
    "partial": ("部分成功", "partial"),
    "failed": ("失败", "failed"),
    "aborted": ("已中止", "failed"),
}
_MODE_LABEL = {"quarantine": "隔离开档（可恢复）", "delete": "永久删除"}


def _signed_size(value: Any) -> str:
    number = _num(value)
    # ASCII signs keep the value copy-pasteable into a shell or a bug report.
    return ("+" if number >= 0 else "-") + human_bytes(abs(number))


def _cleanup_status_stamp(status: str) -> str:
    label, css = _CLEAN_STATUS.get(status, (status or "-", "partial"))
    return f'<div class="receipt-status-stamp {css}">{_e(label)}</div>'


def _cleanup_steps(
    selection: dict[str, Any],
    execution: dict[str, Any],
    verification: dict[str, Any],
    purge: dict[str, Any],
    restore: dict[str, Any],
    mode: str,
) -> str:
    has_selection = bool(selection.get("items"))
    has_execution = bool(execution.get("items"))
    verify = verification.get("summary") or {}
    verify_failed = _num(verify.get("failed"))
    verify_ok = bool(verify) and not verify_failed
    qstate = execution.get("quarantine_state") or {}
    remaining = qstate.get("remaining_files")
    finish_label = "隔离区待释放" if (mode == "quarantine" and not purge and _num(remaining) > 0) else "已留档"
    steps = [
        ("用户范围选择", "done" if has_selection else "todo", "✓" if has_selection else "○"),
        ("执行前文件校验", "done" if has_execution else "todo", "✓" if has_execution else "○"),
        (
            "迁移 / 删除",
            "done" if has_execution else "todo",
            "✓" if has_execution else "○",
        ),
        (
            "落地验证",
            ("alert" if verify_failed else "done") if verify else "todo",
            ("▲" if verify_failed else "✓") if verify else "○",
        ),
        (finish_label, "done" if (purge or restore or qstate) else "todo", "✓"),
    ]
    if not verify:
        steps[3] = ("落地验证（未运行）", "todo", "○")

    nodes = []
    for index, (label, css, mark) in enumerate(steps):
        if index:
            nodes.append('<span class="step-arrow">→</span>')
        nodes.append(
            f'<div class="step-node {css}"><span class="step-indicator">{mark}</span>'
            f"<span>{_e(label)}</span></div>"
        )
    return f'<div class="stepper-flow"><div class="stepper-inner">{"".join(nodes)}</div></div>'


def _cleanup_failure_callout(execution: dict[str, Any], verification: dict[str, Any]) -> str:
    ex = execution.get("summary") or {}
    failed = _num(ex.get("failed_files"))
    vfail = _num((verification.get("summary") or {}).get("failed"))
    if not failed and not vfail:
        return ""
    if failed:
        title = f"{failed} 个文件未被触碰，仍留在原始位置"
        desc = (
            "工具严格遵守「绝不静默假装成功」：失败的文件不会被计入已处理，"
            "也不会从记录里消失。它们保持原样，不会因为本次操作损坏。"
        )
    else:
        title = f"验证发现 {vfail} 项异常"
        desc = (
            "执行已结束，但落地验证没有全部通过。请先查看下方验证异常明细，"
            "确认无问题后再决定是否释放隔离区。"
        )
    return (
        '<div class="failed-callout"><div class="failed-callout-title">'
        + _ICON_WARN
        + f"{_e(title)}</div>"
        f'<div class="failed-callout-desc">{_e(desc)}</div></div>'
    )


def _cleanup_selection(selection: dict[str, Any], mode: str) -> str:
    summary = selection.get("summary") or {}
    items = list(selection.get("items") or [])
    body = [
        '<p class="section-lead">'
        f"共选择了 {_num(summary.get('groups'))} 组 / {_num(summary.get('files')):,} 个文件 / "
        f"{_e(summary.get('human_bytes') or human_bytes(_num(summary.get('bytes'))))}，"
        f"模式为{_e(_MODE_LABEL.get(mode, mode or '-'))}。</p>"
    ]
    if items:
        trs = []
        for item in items:
            risk = str(item.get("risk") or "CAUTION").upper()
            trs.append(
                [
                    _e(item.get("title") or item.get("id")),
                    _e(item.get("category_title") or item.get("category")),
                    _risk_badge(risk),
                    f'<span class="overlap-tag">{_e(item.get("action"))}</span>',
                    _c(f"{_num(item.get('file_count')):,}", "r mono"),
                    _c(_e(item.get("human_size") or human_bytes(_num(item.get("total_bytes")))), "r mono"),
                ]
            )
        body.append(
            _table(["候选组", "分类", "风险", "动作", "文件数", "逻辑大小"], trs)
        )
    else:
        body.append('<div class="note">没有找到选择清单，本次报告只依据执行记录。</div>')
    return _section("selection", "用户选择了什么", _ICON_CHECK, "".join(body))


def _cleanup_execution(execution: dict[str, Any], selection: dict[str, Any]) -> str:
    titles = {
        item.get("id"): item.get("title") or item.get("id")
        for item in selection.get("items") or []
    }
    cat_titles = {
        str(item.get("category") or ""): str(item.get("category_title") or item.get("category") or "-")
        for item in selection.get("items") or []
    }
    results = list(execution.get("items") or [])
    failures = list(execution.get("failures") or [])
    ex = execution.get("summary") or {}
    handled = _num(ex.get("success_files")) + _num(ex.get("failed_files"))
    body: list[str] = [
        '<p class="section-lead">'
        f"共处理 {handled:,} 个文件，成功 {_num(ex.get('success_files')):,} 个，"
        f"失败 {_num(ex.get('failed_files')):,} 个，"
        f"处理逻辑大小 {human_bytes(_num(ex.get('processed_bytes')))}。</p>"
    ]

    if execution.get("abort_reason"):
        body.append(
            '<div class="note danger"><span class="note-title">执行中止</span>'
            f'{_e(execution.get("abort_reason"))}。未执行的项目保持原状，没有被跳过式删除。</div>'
        )

    if results:
        trs = []
        for item in results:
            label, css = _ITEM_STATUS.get(str(item.get("status") or ""), (str(item.get("status") or "-"), "unknown"))
            trs.append(
                [
                    _e(titles.get(item.get("id")) or item.get("id")),
                    _e(cat_titles.get(str(item.get("category") or ""), item.get("category"))),
                    _state(css, label),
                    _c(_num(item.get("success_count")), "r mono"),
                    _c(_num(item.get("failed_count")), "r mono"),
                    _c(_e(human_bytes(_num(item.get("processed_bytes")))), "r mono"),
                    _e(item.get("error") or "-"),
                ]
            )
        body.append(
            _table(["候选组", "分类", "结果", "成功", "失败", "处理大小", "说明"], trs)
        )
    else:
        body.append('<div class="note">没有记录到逐组执行结果。</div>')

    if failures:
        trs = [
            [
                _c(_e(row.get("path")), "path"),
                f'<span style="color:var(--state-failed-text);font-weight:600;">'
                f'{_e(row.get("reason"))}</span>',
                _e(titles.get(row.get("item")) or row.get("item") or "-"),
            ]
            for row in failures[:30]
        ]
        more = (
            f'<div class="note">仅列出前 30 条，共 {len(failures)} 条失败记录。</div>'
            if len(failures) > 30
            else ""
        )
        body.append(
            '<h3 style="font-size:.84rem;font-weight:700;margin:18px 0 8px;'
            'color:var(--state-failed-text);">失败明细（文件仍在原位置）</h3>'
            + _table(["路径", "原因", "所属候选"], trs)
            + more
        )

    return _section("execution", "实际执行了什么", _ICON_BROSEL, "".join(body))


_ICON_BROSEL = _icon(
    '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
    '<polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/>'
    '<line x1="16" y1="17" x2="8" y2="17"/>'
)


def _cleanup_verification(verification: dict[str, Any]) -> str:
    summary = verification.get("summary") or {}
    body: list[str] = []
    if not summary:
        body.append(
            '<div class="note warn"><span class="note-title">尚未运行 verify</span>'
            "本轮没有可验证项目，或用户尚未执行验证命令。没有验证记录不代表隔离文件"
            "一定完好，释放隔离区之前应先运行 <code>verify</code>。</div>"
        )
        return _section("verify", "验证结果", _ICON_CHECK, "".join(body), "未运行")

    checked = _num(summary.get("checked"))
    passed = _num(summary.get("passed"))
    failed = _num(summary.get("failed"))
    skipped = _num(summary.get("skipped"))
    if checked == 0:
        body.append(
            '<div class="note warn"><span class="note-title">没有可验证项目</span>'
            "验证命令已经运行，但检查项为 0。这不等于验证通过，隔离文件的实际状态仍然未知。</div>"
        )
    body.append(
        _stat_tiles(
            [
                ("检查文件", f"{checked:,}", ""),
                ("通过", f"{passed:,}", ""),
                ("失败", f"{failed:,}", ""),
                ("跳过", f"{skipped:,}", ""),
                ("哈希已验", f"{_num(summary.get('hash_verified')):,}", ""),
                ("仅大小校验", f"{_num(summary.get('hash_unavailable')):,}", ""),
                ("哈希不完整", f"{_num(summary.get('hash_incomplete')):,}", ""),
                ("可用空间变化", _signed_size(summary.get("free_delta")), ""),
            ]
        )
    )

    if checked and not failed:
        body.append(
            '<div class="note" style="border-left:3px solid var(--state-known-border);">'
            "<strong>所有检查项通过。</strong>隔离文件与原始记录一致，可以继续下一步。</div>"
        )

    if checked and not failed:
        body.append(
            '<div class="note" style="border-left:3px solid var(--state-known-border);">'
            "<strong>所有检查项通过。</strong>隔离文件与原始记录一致，可以继续下一步。</div>"
        )

    failures = list(verification.get("failures") or [])
    if failures:
        trs = [[_c(_e(row.get("path")), "path"), _e(row.get("reason"))] for row in failures[:30]]
        body.append(
            '<h3 style="font-size:.84rem;font-weight:700;margin:18px 0 8px;'
            'color:var(--state-failed-text);">验证异常</h3>'
            + _table(["路径", "原因"], trs)
        )

    hash_unavailable = _num(summary.get("hash_unavailable"))
    hash_incomplete = _num(summary.get("hash_incomplete"))
    if hash_unavailable:
        body.append(
            '<div class="note warn"><span class="note-title">未做内容校验</span>'
            f"有 {hash_unavailable:,} 个文件只比较了大小，没有对比哈希。"
            "大小一致不代表内容一致，这类文件不能算作已确认完好。</div>"
        )
    if hash_incomplete:
        body.append(
            '<div class="note warn"><span class="note-title">校验不完整</span>'
            f"有 {hash_incomplete:,} 个文件只完成了部分校验（例如只读到一侧的哈希）。"
            "结论按未通过处理。</div>"
        )

    hash_unavailable = _num(summary.get("hash_unavailable"))
    hash_incomplete = _num(summary.get("hash_incomplete"))
    if hash_unavailable:
        body.append(
            '<div class="note warn"><span class="note-title">未做内容校验</span>'
            f"有 {hash_unavailable:,} 个文件只比较了大小，没有对比哈希。"
            "大小一致不代表内容一致，这类文件不能算作已确认完好。</div>"
        )
    if hash_incomplete:
        body.append(
            '<div class="note warn"><span class="note-title">校验不完整</span>'
            f"有 {hash_incomplete:,} 个文件只完成了部分校验（例如只读到一侧的哈希）。"
            "结论按未通过处理。</div>"
        )

    if verification.get("note"):
        body.append(f'<div class="note">{_e(verification.get("note"))}</div>')
    if verification.get("realpath_baseline") == "current":
        body.append(
            '<div class="note">验证使用当前 realpath 作为基线，路径解析发生过变化时'
            "结论可能偏宽松。</div>"
        )

    note = f"{passed:,}/{checked:,} 通过" if checked else "无记录"
    return _section("verify", "验证结果", _ICON_CHECK, "".join(body), note)


def _cleanup_release(execution: dict[str, Any], verification: dict[str, Any]) -> str:
    mode = str(execution.get("mode") or "")
    purge = execution.get("purge") or {}
    restore = execution.get("restore") or {}
    ex = execution.get("summary") or {}
    qstate = execution.get("quarantine_state") or {}
    remaining_files = qstate.get("remaining_files", ex.get("quarantine_files"))
    remaining_bytes = qstate.get("remaining_bytes", ex.get("quarantine_occupied_bytes"))

    body: list[str] = []
    body.append(
        _stat_tiles(
            [
                ("隔离区位置", _e(execution.get("quarantine_root") or "-"), ""),
                ("剩余文件", f"{_num(remaining_files):,}", ""),
                ("剩余占用", _size(remaining_bytes) if remaining_bytes is not None else "未知", ""),
                ("累计永久删除", f"{_num(purge.get('purged_files')):,}", ""),
                ("累计恢复", f"{_num(restore.get('restored_files')):,}", ""),
            ]
        )
    )

    if mode == "quarantine" and not purge and _num(remaining_files) > 0:
        body.append(
            '<div class="note warn"><span class="note-title">隔离区仍占用设备空间</span>'
            "隔离只是把文件移到另一个目录，不会立即释放空间。尚未 purge，确认无需恢复后再执行 purge。</div>"
        )

    if purge:
        trs = []
        purge_last = purge.get("last_run") if isinstance(purge.get("last_run"), dict) else purge
        body.append(
            '<h3 style="font-size:.84rem;font-weight:700;margin:18px 0 8px;'
            'color:var(--text-secondary);">永久释放（purge）</h3>'
            + _stat_tiles(
                [
                    ("删除文件", f"{_num(purge.get('purged_files')):,}", ""),
                    ("删除逻辑大小", human_bytes(_num(purge.get("purged_bytes"))), ""),
                    ("失败", f"{_num(purge.get('failed')):,}", ""),
                    ("可用空间变化", _signed_size(purge_last.get("free_delta")), ""),
                ]
            )
        )
        runs = [item for item in purge.get("runs") or [] if isinstance(item, dict)]
        if runs:
            for run in runs[-5:]:
                trs.append(
                    [
                        _e(run.get("at") or "-"),
                        _c(f"{_num(run.get('purged_files')):,}", "r mono"),
                        _c(_e(human_bytes(_num(run.get("purged_bytes")))), "r mono"),
                        _c(f"{_num(run.get('failed')):,}", "r mono"),
                    ]
                )
            body.append(
                '<div style="font-size:.74rem;font-weight:700;color:var(--text-tertiary);'
                'margin:10px 0 5px;">purge 执行记录</div>'
                + _table(["时间", "删除文件", "逻辑大小", "失败"], trs)
            )
        purge_failures = list(purge.get("failures") or [])
        if purge_failures:
            trs = [[_c(_e(row.get("path")), "path"), _e(row.get("reason"))] for row in purge_failures[:20]]
            body.append(
                '<div style="margin-top:8px">'
                + _table(["purge 失败路径", "原因"], trs)
                + "</div>"
            )
        history_blocks: list[str] = []
        for index, run in enumerate(runs, 1):
            run_failures = [row for row in (run.get("failures") or []) if isinstance(row, dict)]
            if not run_failures:
                continue
            rows = [
                [_c(_e(row.get("path")), "path"), _e(row.get("reason"))]
                for row in run_failures[:15]
            ]
            history_blocks.append(
                '<div style="font-size:.72rem;color:var(--text-tertiary);margin:8px 0 4px;">'
                f"第 {index} 次 · {_e(run.get('at') or '-')} · "
                f"{len(run_failures)} 条失败</div>"
                + _table(["路径", "原因"], rows)
            )
        if history_blocks:
            body.append(
                '<div style="font-size:.74rem;font-weight:700;color:var(--state-failed-text);'
                'margin:12px 0 2px;">purge 历史失败明细</div>'
                + "".join(history_blocks)
            )
        history_blocks: list[str] = []
        for index, run in enumerate(runs, 1):
            run_failures = [row for row in (run.get("failures") or []) if isinstance(row, dict)]
            if not run_failures:
                continue
            rows = [
                [_c(_e(row.get("path")), "path"), _e(row.get("reason"))]
                for row in run_failures[:15]
            ]
            history_blocks.append(
                '<div style="font-size:.72rem;color:var(--text-tertiary);margin:8px 0 4px;">'
                f"第 {index} 次 · {_e(run.get('at') or '-')} · "
                f"{len(run_failures)} 条失败</div>"
                + _table(["路径", "原因"], rows)
            )
        if history_blocks:
            body.append(
                '<div style="font-size:.74rem;font-weight:700;color:var(--state-failed-text);'
                'margin:12px 0 2px;">purge 历史失败明细</div>'
                + "".join(history_blocks)
            )
        if _num(purge.get("missing_files")):
            body.append(
                f'<div class="note">另有 {_num(purge.get("missing_files"))} 个隔离文件在 purge 前已不存在。</div>'
            )
        if _num(purge.get("hash_unavailable_files")):
            body.append(
                f'<div class="note">有 {_num(purge.get("hash_unavailable_files"))} 个隔离文件没有可用哈希，'
                "无法与原始文件比对。</div>"
            )

    if restore:
        body.append(
            '<h3 style="font-size:.84rem;font-weight:700;margin:18px 0 8px;'
            'color:var(--text-secondary);">恢复（restore）</h3>'
            + _stat_tiles(
                [
                    ("恢复文件", f"{_num(restore.get('restored_files')):,}", ""),
                    ("恢复逻辑大小", human_bytes(_num(restore.get("restored_bytes"))), ""),
                    ("失败", f"{_num(restore.get('failed')):,}", ""),
                ]
            )
        )
        runs = [item for item in restore.get("runs") or [] if isinstance(item, dict)]
        if runs:
            trs = [
                [
                    _e(run.get("at") or "-"),
                    _c(f"{_num(run.get('restored_files')):,}", "r mono"),
                    _c(_e(human_bytes(_num(run.get("restored_bytes")))), "r mono"),
                    _c(f"{_num(run.get('failed')):,}", "r mono"),
                ]
                for run in runs[-5:]
            ]
            body.append(
                '<div style="font-size:.74rem;font-weight:700;color:var(--text-tertiary);'
                'margin:10px 0 5px;">restore 执行记录</div>'
                + _table(["时间", "恢复文件", "逻辑大小", "失败"], trs)
            )
        restore_failures = list(restore.get("failures") or [])
        if restore_failures:
            trs = [[_c(_e(row.get("path")), "path"), _e(row.get("reason"))] for row in restore_failures[:20]]
            body.append(
                '<div style="margin-top:8px">'
                + _table(["restore 失败路径", "原因"], trs)
                + "</div>"
            )

    free_rows = [
        ("执行前可用空间", execution.get("free_before")),
        ("执行后可用空间", execution.get("free_after")),
        ("验证时可用空间", verification.get("free_now")),
    ]
    rows: list[list[Any]] = []
    for label, data in free_rows:
        if isinstance(data, dict) and data.get("available_bytes") is not None:
            rows.append(
                [_e(label), _c(_e(human_bytes(_num(data.get("available_bytes")))), "r mono")]
            )
    rows.append(["执行期间变化", _c(_e(_signed_size(ex.get("df_delta_bytes"))), "r mono")])
    if purge:
        purge_last = purge.get("last_run") if isinstance(purge.get("last_run"), dict) else purge
        rows.append(
            ["purge 后变化", _c(_e(_signed_size(purge_last.get("free_delta"))), "r mono")]
        )
    body.append(
        '<h3 style="font-size:.84rem;font-weight:700;margin:18px 0 8px;'
        'color:var(--text-secondary);">可用空间记录</h3>'
        + _table(["时点", "可用空间"], rows)
    )

    return _section("release", "隔离区、释放与恢复", _ICON_SHIELD, "".join(body))


def _cleanup_background(summary: dict[str, Any]) -> str:
    categories = list(summary.get("categories") or [])
    body = [
        '<p class="section-lead">'
        f"清理前共发现 {_num(summary.get('candidate_groups'))} 组 / "
        f"{_num(summary.get('candidate_files')):,} 个文件 / "
        f"{human_bytes(_num(summary.get('candidate_bytes')))}，"
        f"其中 {_num(summary.get('review_required'))} 组需要复核。</p>"
    ]
    if categories:
        peak = max(_num(row.get("bytes")) for row in categories) or 1
        trs = []
        for row in categories:
            risk = str(row.get("risk") or "SAFE").upper()
            trs.append(
                [
                    _e(row.get("title")),
                    _c(_num(row.get("groups")), "r mono"),
                    _c(f"{_num(row.get('files')):,}", "r mono"),
                    _c(_size(row.get("bytes")), "r mono"),
                    f'<div class="app-bar-vis"><div class="app-bar-inner" '
                    f'style="width:{_ratio(row.get("bytes"), peak):.1f}%"></div></div>',
                    _risk_badge(risk),
                ]
            )
        body.append(
            _table(["分类", "组数", "文件", "逻辑大小", "相对比例", "最高风险"], trs)
        )
    else:
        body.append(
            '<div class="note">没有找到清理前的分析摘要；本报告只依据执行记录。</div>'
        )
    body.append(
        '<div class="note">完整的清理前诊断见 <code>analysis/report-before.html</code>。</div>'
    )
    return _section("background", "清理前发现了什么", _ICON_SCAN, "".join(body))


def _cleanup_next(execution: dict[str, Any], verification: dict[str, Any]) -> str:
    ex = execution.get("summary") or {}
    verify = verification.get("summary") or {}
    mode = str(execution.get("mode") or "")
    purge = execution.get("purge") or {}
    restore = execution.get("restore") or {}
    qstate = execution.get("quarantine_state") or {}
    remaining = qstate.get("remaining_files")

    items: list[str] = []
    if _num(ex.get("failed_files")):
        items.append(
            f"有 {_num(ex.get('failed_files')):,} 个文件执行失败，仍留在原位置；请先查看失败原因。"
        )
    if _num(verify.get("failed")):
        items.append(
            f"验证有 {_num(verify.get('failed')):,} 项异常，请先确认再继续清理或释放。"
        )
    if execution.get("abort_reason"):
        items.append("执行曾经中止，未执行的项目保持原状。")
    if mode == "quarantine" and not purge and (remaining is None or _num(remaining) > 0):
        items.append("隔离区仍占用设备空间；确认无需恢复后再运行 quarantine purge。")
    if purge:
        items.append("已永久删除的隔离文件无法恢复；restore 只会恢复仍在隔离区的文件。")
    if restore:
        if remaining is not None and _num(remaining) == 0:
            items.append("隔离区已清空，本轮恢复已没有可操作对象。")
        else:
            items.append(f"仍有 {_num(remaining):,} 个文件留在隔离区，可继续恢复。")
    if not items:
        items.append("本轮没有遗留问题。")

    lis = "".join(f"<li>{_e(text)}</li>" for text in items)
    return _section(
        "next", "剩余问题与下一步", _ICON_INFO, f'<ul class="bullet-list">{lis}</ul>'
    )


def render_cleanup_html(
    analysis: dict[str, Any] | None,
    selection: dict[str, Any] | None,
    execution: dict[str, Any],
    verification: dict[str, Any] | None = None,
) -> str:
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
    mode = str(execution.get("mode") or selection.get("mode") or "")

    model = " ".join(
        value
        for value in (str(device.get("manufacturer") or ""), str(device.get("model") or ""))
        if value
    ).strip()

    meta_items = [
        ("受检设备", model or str(identity.get("serial") or "-")),
        ("执行模式", _MODE_LABEL.get(mode, mode or "-")),
        ("隔离区位置", str(execution.get("quarantine_root") or "-")),
        ("执行 ID", str(execution.get("execution_id") or "-")),
        ("开始时间", str(execution.get("started_at") or "-")),
        ("结束时间", str(execution.get("finished_at") or "-")),
        ("传输方式", str(execution.get("transport") or "-")),
        ("验证", "已运行" if verify else "未运行"),
    ]
    meta_html = "".join(
        f'<div class="tape-item"><label>{_e(label)}</label>'
        f'<div class="val">{_e(value)}</div></div>'
        for label, value in meta_items
    )

    metrics = [
        ("用户选择", f"{_num(sel.get('groups'))} 组<small>{_num(sel.get('files')):,} 个文件</small>", ""),
        ("成功文件", f"{_num(ex.get('success_files')):,}", ""),
        (
            "失败文件（原位未动）",
            f"{_num(ex.get('failed_files')):,}",
            "failed" if _num(ex.get("failed_files")) else "",
        ),
        ("处理逻辑大小", human_bytes(_num(ex.get("processed_bytes"))), ""),
        ("已释放", human_bytes(_num(ex.get("released_bytes"))), ""),
        (
            "隔离区占用",
            human_bytes(
                _num(
                    (execution.get("quarantine_state") or {}).get(
                        "remaining_bytes", ex.get("quarantine_occupied_bytes")
                    )
                )
            ),
            "",
        ),
        ("可用空间变化", _signed_size(ex.get("df_delta_bytes")), ""),
    ]
    if verify:
        metrics.append(
            (
                "验证通过",
                f"{_num(verify.get('passed')):,} / {_num(verify.get('checked')):,}<small>个检查项</small>",
                "",
            )
        )
    if purge:
        metrics.append(
            (
                "永久删除（累计）",
                f"{_num(purge.get('purged_files')):,}<small>{_e(human_bytes(_num(purge.get('purged_bytes'))))}</small>",
                "",
            )
        )
    if restore:
        metrics.append(
            (
                "已恢复（累计）",
                f"{_num(restore.get('restored_files')):,}<small>{_e(human_bytes(_num(restore.get('restored_bytes'))))}</small>",
                "",
            )
        )

    metric_html = "".join(
        f'<div class="metric-tile {css}"><div class="metric-label">{_e(label)}</div>'
        f'<div class="metric-num">{value}</div></div>'
        for label, value, css in metrics
    )

    body = (
        '<div class="section-header" id="process" style="margin:4px 0 8px;">'
        '<h2 class="section-title">' + _ICON_CHECK + "清理过程</h2></div>"
        + _cleanup_steps(selection, execution, verification, purge, restore, mode)
        + _cleanup_failure_callout(execution, verification)
        + _cleanup_selection(selection, mode)
        + _cleanup_execution(execution, selection)
        + _cleanup_verification(verification)
        + _cleanup_release(execution, verification)
        + _cleanup_background(summary)
        + _cleanup_next(execution, verification)
    )

    return (
        "<!DOCTYPE html>"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<meta name="color-scheme" content="light dark">'
        "<title>清理结果报告 - "
        f"{_e(model or identity.get('serial') or '-')}</title>"
        f"<style>{_CSS}</style></head><body>"
        '<div class="receipt-wrapper">'
        '<header class="receipt-header"><div class="receipt-title-box">'
        f'<div class="receipt-code-pill">AUDIT RECEIPT // {_e(execution.get("execution_id") or "未编号")}</div>'
        "<h1>Android 存储清理结果报告</h1></div>"
        + _cleanup_status_stamp(status)
        + "</header>"
        f'<div class="meta-tape">{meta_html}</div>'
        f'<section class="receipt-metrics">{metric_html}</section>'
        + body
        + '<footer class="receipt-footer"><div>'
        "<strong>说明：</strong>本报告记录已经发生的操作，不是建议清单。"
        "全程离线生成，未联网传输任何数据。"
        "</div>"
        '<span class="verify-pill">本报告含设备序列号与内部路径，分享前请自行脱敏</span>'
        "</footer></div></body></html>"
    )


def write_cleanup_html(
    analysis: dict[str, Any] | None,
    selection: dict[str, Any] | None,
    execution: dict[str, Any],
    verification: dict[str, Any] | None,
    path: Any,
) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        render_cleanup_html(analysis, selection, execution, verification), encoding="utf-8"
    )
    return path
