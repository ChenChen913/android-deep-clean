"""Plan, selection and confirmation-token logic.

Safety contract:
- plan never executes;
- selection never executes and only contains explicitly chosen items;
- execute requires the exact token from selection and revalidates paths.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from .reports import plan_markdown, selection_markdown
from .state import State
from .util import (
    RISK_ORDER,
    SafetyError,
    UsageError,
    ValidationError,
    atomic_write_json,
    canonical_json,
    compute_plan_hash,
    compute_selection_hash,
    human_bytes,
    now_iso,
    random_token,
    read_json,
    sha256_text,
    split_csv,
    stable_id,
)

_VALID_VERDICTS = {"trash", "keep", "uncertain"}


def load_analysis(state: State) -> tuple[dict[str, Any], str]:
    path = state.require("analysis_json", "分析结果")
    analysis = read_json(path, default=None)
    if not isinstance(analysis, dict) or not analysis.get("analysis_id"):
        raise ValidationError(f"分析文件格式不正确：{path}")
    return analysis, str(path)


def load_review(path: str | None, analysis: dict[str, Any]) -> dict[str, Any]:
    if not path:
        return {"decisions": [], "present": False}
    raw = read_json(path, default=None)
    if raw is None:
        raise ValidationError(f"review 文件不存在：{path}")
    if not isinstance(raw, dict):
        raise ValidationError("review 根节点必须是 JSON object")
    if raw.get("analysis_id") and raw.get("analysis_id") != analysis.get("analysis_id"):
        raise ValidationError("review.analysis_id 与分析结果不一致，拒绝使用")
    decisions = raw.get("decisions")
    if not isinstance(decisions, list):
        raise ValidationError("review.decisions 必须是数组")
    by_id = {item.get("id"): item for item in analysis.get("items") or []}
    seen: set[str] = set()
    cleaned: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValidationError("review.decisions 中存在非对象项")
        item_id = decision.get("id")
        if not item_id or item_id not in by_id:
            raise ValidationError(f"review 引用了不存在的候选：{item_id}")
        if item_id in seen:
            raise ValidationError(f"review 中重复的候选：{item_id}")
        seen.add(item_id)
        verdict = str(decision.get("verdict") or "").strip().lower()
        if verdict not in _VALID_VERDICTS:
            raise ValidationError(f"候选 {item_id} 的 verdict 必须是 trash/keep/uncertain")
        item = by_id[item_id]
        base_risk = str(item.get("risk") or "CAUTION")
        review_risk = str(decision.get("risk") or base_risk).upper()
        if review_risk not in RISK_ORDER:
            raise ValidationError(f"候选 {item_id} 的 risk 非法：{review_risk}")
        if RISK_ORDER[review_risk] < RISK_ORDER.get(base_risk, 99):
            raise ValidationError(f"候选 {item_id} 试图降低风险等级（{base_risk} -> {review_risk}），拒绝")
        if base_risk == "DANGEROUS" or review_risk == "DANGEROUS":
            if verdict == "trash":
                raise ValidationError(f"候选 {item_id} 是 DANGEROUS，禁止标记为 trash")
        reason = str(decision.get("reason") or "").strip()
        if verdict == "trash" and len(reason) < 4:
            raise ValidationError(f"候选 {item_id} 标记 trash 时必须提供实质性 reason")
        cleaned[item_id] = {
            "id": item_id,
            "verdict": verdict,
            "risk": review_risk,
            "reason": reason,
            "evidence_checked": decision.get("evidence_checked") or [],
        }
    return {
        "present": True,
        "path": path,
        "analysis_id": analysis.get("analysis_id"),
        "decisions": cleaned,
        "raw_count": len(decisions),
    }


def build_plan(analysis: dict[str, Any], review: dict[str, Any], config: dict[str, Any], analysis_path: str) -> dict[str, Any]:
    decisions: dict[str, dict[str, Any]] = review.get("decisions") or {}
    items: list[dict[str, Any]] = []
    for item in analysis.get("items") or []:
        base_risk = str(item.get("risk") or "CAUTION")
        decision = decisions.get(item.get("id"))
        effective_risk = base_risk
        ai_verdict = "not_reviewed"
        ai_reason = ""
        review_status = "not_required"
        if item.get("ai_review_required"):
            review_status = "missing"
        if decision:
            effective_risk = decision.get("risk") or base_risk
            ai_verdict = decision.get("verdict")
            ai_reason = decision.get("reason", "")
            review_status = "reviewed"
        selectable = bool(item.get("selectable", True))
        selectable_reason = ""
        if effective_risk == "DANGEROUS":
            selectable = False
            selectable_reason = "DANGEROUS 项目默认禁止删除"
        elif item.get("ai_review_required") and not decision:
            selectable = False
            selectable_reason = "需要 AI/人工复核（review.json 中缺少 verdict）"
        elif decision and decision.get("verdict") in {"keep", "uncertain"}:
            selectable = False
            selectable_reason = f"复核结论为 {ai_verdict}，不可选"
        elif base_risk == "CAUTION" and not decision:
            selectable = False
            selectable_reason = "CAUTION 项目必须复核后才能选择"
        plan_item = {
            "id": item.get("id"),
            "category": item.get("category"),
            "category_title": item.get("category_title"),
            "title": item.get("title"),
            "risk": effective_risk,
            "base_risk": base_risk,
            "action": item.get("action"),
            "primary_path": item.get("primary_path"),
            "sample_paths": item.get("sample_paths") or [],
            "total_bytes": int(item.get("total_bytes") or 0),
            "human_size": human_bytes(item.get("total_bytes") or 0),
            "file_count": int(item.get("file_count") or 0),
            "dir_count": int(item.get("dir_count") or 0),
            "files_digest": item.get("files_digest") or "",
            "confidence": float(item.get("confidence") or 0.5),
            "app_pkg": item.get("app_pkg") or "",
            "app_kind": item.get("app_kind") or "",
            "evidence": item.get("evidence") or [],
            "reasons": item.get("reasons") or [],
            "keep_suggestion": item.get("keep_suggestion") or "",
            "ai_review_required": bool(item.get("ai_review_required")),
            "review_status": review_status,
            "ai_verdict": ai_verdict,
            "ai_reason": ai_reason,
            "selectable": selectable,
            "selectable_reason": selectable_reason,
        }
        items.append(plan_item)
    selectable_items = [item for item in items if item["selectable"]]
    risk_totals: dict[str, dict[str, int]] = {}
    for item in items:
        risk_totals.setdefault(item["risk"], {"groups": 0, "files": 0, "bytes": 0})
        risk_totals[item["risk"]]["groups"] += 1
        risk_totals[item["risk"]]["files"] += item["file_count"]
        risk_totals[item["risk"]]["bytes"] += int(item["total_bytes"])
    device_identity = analysis.get("device_identity") or (analysis.get("summary") or {}).get("device_identity") or {}
    plan_hash = compute_plan_hash({
        "analysis_id": analysis.get("analysis_id"),
        "device_identity": device_identity,
        "platform": analysis.get("platform") or "android",
        "items": items,
    })
    plan = {
        "schema": 1,
        "plan_id": stable_id("plan", analysis.get("analysis_id", ""), plan_hash),
        "plan_hash": plan_hash,
        "analysis_id": analysis.get("analysis_id"),
        "device_identity": device_identity,
        "platform": analysis.get("platform") or "android",
        "analysis_path": analysis_path,
        "analysis_db_path": analysis.get("analysis_db_path"),
        "generated_at": now_iso(),
        "review_present": bool(review.get("present")),
        "review_path": review.get("path", ""),
        "items": items,
        "summary": {
            "groups": len(items),
            "files": sum(item["file_count"] for item in items),
            "bytes": sum(int(item["total_bytes"]) for item in items),
            "selectable_groups": len(selectable_items),
            "selectable_files": sum(item["file_count"] for item in selectable_items),
            "selectable_bytes": sum(int(item["total_bytes"]) for item in selectable_items),
            "review_required": sum(1 for item in items if item["ai_review_required"] and item["review_status"] == "missing"),
            "risk_totals": risk_totals,
        },
        "limitations": analysis.get("limitations") or [],
        "safety": {
            "deletion_requires_token": True,
            "default_mode": "quarantine",
            "dangerous_selectable": False,
        },
    }
    return plan


def run_plan(state: State, config: dict[str, Any], review_path: str | None = None) -> dict[str, Any]:
    analysis, analysis_path = load_analysis(state)
    if review_path:
        state.set_file("review", review_path)
    review_path = review_path or state.get_file("review") or ""
    review = load_review(review_path or None, analysis)
    plan = build_plan(analysis, review, config, analysis_path)
    out_dir = state.dir("plan")
    plan_path = out_dir / "plan.json"
    atomic_write_json(plan_path, plan)
    (out_dir / "report-plan.md").write_text(plan_markdown(plan), encoding="utf-8")
    if review.get("present"):
        atomic_write_json(out_dir / "review.validated.json", {
            "analysis_id": analysis.get("analysis_id"),
            "decisions": list((review.get("decisions") or {}).values()),
        })
    state.set_file("plan", plan_path)
    state.set_file("plan_report", out_dir / "report-plan.md")
    return plan


def _select_ids(items: list[dict[str, Any]], ids: Iterable[str]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in items}
    selected = []
    missing = []
    for item_id in ids:
        item = by_id.get(item_id)
        if not item:
            missing.append(item_id)
        else:
            selected.append(item)
    if missing:
        raise UsageError(f"未知的候选 ID：{', '.join(missing)}")
    return selected


def build_selection(plan: dict[str, Any], config: dict[str, Any], args: Any) -> dict[str, Any]:
    items = plan.get("items") or []
    selected: list[dict[str, Any]] = []
    ids = split_csv(getattr(args, "ids", None))
    categories = [c.strip() for c in split_csv(getattr(args, "categories", None))]
    app_kinds = [k.strip() for k in split_csv(getattr(args, "app_kinds", None))]
    risks = [r.strip().upper() for r in split_csv(getattr(args, "risk", None))]
    exclude_ids = set(split_csv(getattr(args, "exclude_ids", None)))
    exclude_categories = set(split_csv(getattr(args, "exclude_categories", None)))
    all_safe = bool(getattr(args, "all_safe", False))
    if not (ids or categories or app_kinds or risks or all_safe):
        raise UsageError("必须显式指定选择范围：--ids / --categories / --app-kinds / --risk / --all-safe 之一")
    if ids:
        selected = _select_ids(items, ids)
        bad = [item for item in selected if not item.get("selectable") or item.get("risk") == "DANGEROUS"]
        if bad:
            details = "; ".join(f"{item['id']}（{item.get('selectable_reason') or '不可选'}）" for item in bad[:10])
            raise SafetyError(f"选中的项目中包含不可执行项：{details}")
    else:
        selected = [item for item in items if item.get("selectable")]
        if categories:
            selected = [item for item in selected if item.get("category") in categories]
        if app_kinds:
            selected = [item for item in selected if item.get("app_kind") in app_kinds]
        if risks:
            selected = [item for item in selected if item.get("risk") in risks]
        if all_safe:
            selected = [item for item in selected if item.get("risk") == "SAFE"]
    selected = [item for item in selected if item["id"] not in exclude_ids and item.get("category") not in exclude_categories]
    if not selected:
        raise SafetyError("没有可选择的项目。请检查筛选条件，或先完成 review（CAUTION 必须复核）。")
    # Defense in depth: never allow a DANGEROUS item into a confirmation page,
    # even if a tampered plan marked it selectable.
    dangerous = [item for item in selected if item.get("risk") == "DANGEROUS"]
    if dangerous:
        raise SafetyError(f"selection 中出现 DANGEROUS 项，拒绝生成：{[item.get('id') for item in dangerous[:5]]}")
    invalid = [item for item in selected if item.get("risk") not in RISK_ORDER]
    if invalid:
        raise SafetyError(f"selection 中存在未知风险等级：{[item.get('id') for item in invalid[:5]]}")
    mode = str(getattr(args, "mode", "quarantine") or "quarantine").lower()
    if mode not in {"quarantine", "delete"}:
        raise UsageError("--mode 只能是 quarantine 或 delete")
    if mode == "delete":
        risky = [item for item in selected if item.get("risk") != "SAFE"]
        if risky:
            raise SafetyError("永久删除模式只允许选择 SAFE 项目；CAUTION/DANGEROUS 请使用隔离模式。")
    token = random_token("ADC")
    total_files = sum(int(item.get("file_count") or 0) for item in selected)
    total_bytes = sum(int(item.get("total_bytes") or 0) for item in selected)
    selection = {
        "schema": 1,
        "selection_id": "",
        "selection_hash": "",
        "plan_id": plan.get("plan_id"),
        "plan_hash": plan.get("plan_hash"),
        "analysis_id": plan.get("analysis_id"),
        "device_identity": plan.get("device_identity") or {},
        "platform": plan.get("platform") or "android",
        "created_at": now_iso(),
        "mode": mode,
        "token": token,
        "items": selected,
        "summary": {
            "groups": len(selected),
            "files": total_files,
            "bytes": total_bytes,
            "human_bytes": human_bytes(total_bytes),
            "risk_totals": _count_risks(selected),
        },
        "user_confirmation": {
            "required": True,
            "confirmed": False,
            "instructions": "必须由用户在对话中明确确认后才能执行；AI 不得自行确认。",
        },
    }
    selection["selection_hash"] = compute_selection_hash(selection)
    selection["selection_id"] = stable_id("selection", plan.get("plan_id", ""), selection["selection_hash"])
    return selection


def _count_risks(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.get("risk", "?")] = counts.get(item.get("risk", "?"), 0) + 1
    return counts


def run_select(state: State, config: dict[str, Any], args: Any) -> dict[str, Any]:
    plan_path = state.require("plan", "清理计划")
    plan = read_json(plan_path, default=None)
    if not isinstance(plan, dict):
        raise ValidationError("plan.json 无效")
    if not plan.get("plan_hash") or compute_plan_hash(plan) != plan.get("plan_hash"):
        raise SafetyError("plan.json 已被修改（哈希不一致），拒绝生成 selection。请重新 plan。")
    selection = build_selection(plan, config, args)
    out_dir = state.dir("selection")
    selection_path = out_dir / "selection.json"
    atomic_write_json(selection_path, selection)
    (out_dir / "report-selection.md").write_text(selection_markdown(selection), encoding="utf-8")
    state.set_file("selection", selection_path)
    state.set_file("selection_report", out_dir / "report-selection.md")
    return selection
