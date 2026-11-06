"""Safe execution, manifests, quarantine, verification and restore.

Hard safety rules enforced here (not merely documented):
- plan hash and selection hash are recomputed;
- the selection token must match exactly;
- every path is re-guarded and stat-revalidated (size/mtime/inode);
- symbolic links, special files, path traversal and changed files are refused;
- no recursive delete is ever used; only rm -f single files / rmdir empty dirs;
- quarantine is the default; permanent delete requires an explicit flag and SAFE-only selection.
"""

from __future__ import annotations

import hmac
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .reports import execution_markdown, verification_markdown
from .rules import guard_path
from .scanner import load_packages
from .state import State
from .transport import Transport, TransportError
from .util import (
    RISK_ORDER,
    SafetyError,
    ValidationError,
    atomic_write_json,
    compute_plan_hash,
    compute_selection_hash,
    display_path,
    human_bytes,
    is_under,
    md_code,
    now_iso,
    normalize_device_path,
    random_token,
    read_json,
)


def _recompute_plan_hash(plan: dict[str, Any]) -> str:
    return compute_plan_hash(plan)


def _recompute_selection_hash(selection: dict[str, Any]) -> str:
    return compute_selection_hash(selection)


class Executor:
    def __init__(self, transport: Transport, config: dict[str, Any], state: State) -> None:
        self.transport = transport
        self.config = config
        self.state = state
        self.packages = self._load_packages()
        self.execution_id = "exec-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + random_token("").replace("ADC-", "")
        self.exec_dir = state.dir("execution")
        self.journal_path = self.exec_dir / "journal.jsonl"
        self.quarantine_roots: set[str] = set()
        self.failures: list[dict[str, str]] = []

    def _load_packages(self):
        scan_path = self.state.require("scan", "扫描结果")
        conn = sqlite3.connect(str(scan_path))
        conn.row_factory = sqlite3.Row
        try:
            return load_packages(conn)
        finally:
            conn.close()

    # ------------------------------------------------------------- preflight
    def _load_analysis_db(self, plan: dict[str, Any]) -> sqlite3.Connection:
        path = plan.get("analysis_db_path") or ""
        db_path = Path(path) if path else None
        if not db_path or not db_path.exists():
            raise ValidationError("analysis.db 不存在；请重新运行 analyze 和 plan")
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _guard(self, path: str, category: str, kind: str) -> dict[str, Any]:
        return guard_path(path, category, kind=kind, packages=self.packages, config=self.config)

    def _preflight(self, plan: dict[str, Any], selection: dict[str, Any], token: str, mode: str, allow_permanent: bool) -> tuple[sqlite3.Connection, dict[str, list[dict[str, Any]]]]:
        if not token or not hmac.compare_digest(str(token), str(selection.get("token") or "")):
            raise SafetyError("确认令牌不匹配。请使用 select 输出中的令牌，并确认用户已明确同意。")
        if plan.get("plan_hash") != selection.get("plan_hash"):
            raise SafetyError("selection 与 plan 的 plan_hash 不一致，拒绝执行")
        if _recompute_plan_hash(plan) != plan.get("plan_hash"):
            raise SafetyError("plan.json 已被修改（哈希不一致），拒绝执行")
        if _recompute_selection_hash(selection) != selection.get("selection_hash"):
            raise SafetyError("selection.json 已被修改（哈希不一致），拒绝执行")
        if mode != selection.get("mode"):
            raise SafetyError(f"执行模式 {mode} 与 selection.mode={selection.get('mode')} 不一致")
        if mode == "delete" and not allow_permanent:
            raise SafetyError("永久删除需要显式 --allow-permanent-delete；默认请使用隔离模式")
        plan_items = {item["id"]: item for item in plan.get("items") or []}
        selected_ids = [item["id"] for item in selection.get("items") or []]
        if not selected_ids:
            raise SafetyError("selection 为空")
        missing = [item_id for item_id in selected_ids if item_id not in plan_items]
        if missing:
            raise SafetyError(f"selection 中存在 plan 中没有的项目：{missing[:5]}")
        conn = self._load_analysis_db(plan)
        manifests: dict[str, list[dict[str, Any]]] = {}
        for item_id in selected_ids:
            plan_item = plan_items[item_id]
            if not plan_item.get("selectable"):
                raise SafetyError(f"项目 {item_id} 在 plan 中不可选")
            if str(plan_item.get("risk")) == "DANGEROUS":
                raise SafetyError(f"项目 {item_id} 是 DANGEROUS，禁止执行")
            if mode == "delete" and str(plan_item.get("risk")) != "SAFE":
                raise SafetyError(f"永久删除模式只允许 SAFE 项目；{item_id} 为 {plan_item.get('risk')}")
            candidate = conn.execute("SELECT * FROM candidates WHERE id=?", (item_id,)).fetchone()
            if not candidate:
                raise ValidationError(f"analysis.db 中找不到项目 {item_id}")
            if candidate["category"] != plan_item.get("category") or candidate["action"] != plan_item.get("action"):
                raise SafetyError(f"项目 {item_id} 的 plan 与 analysis 不一致")
            rows = conn.execute("SELECT * FROM candidate_files WHERE candidate_id=? ORDER BY path", (item_id,)).fetchall()
            manifests[item_id] = [dict(row) for row in rows]
        return conn, manifests

    # ------------------------------------------------------------- execution
    def run(self, plan: dict[str, Any], selection: dict[str, Any], token: str, mode: str, allow_permanent: bool) -> dict[str, Any]:
        conn, manifests = self._preflight(plan, selection, token, mode, allow_permanent)
        started_at = now_iso()
        free_before = self._safe_df()
        self.journal_path.write_text("", encoding="utf-8")
        quarantine_root = ""
        if mode == "quarantine":
            quarantine_root = self._quarantine_root()
            try:
                self.transport.mkdirs(quarantine_root + "/files")
                self.quarantine_roots.add(quarantine_root)
            except Exception as exc:  # noqa: BLE001
                conn.close()
                raise SafetyError(f"无法创建隔离区 {quarantine_root}：{exc}") from exc
        item_results: list[dict[str, Any]] = []
        total_success = 0
        total_fail = 0
        total_bytes = 0
        quarantine_files = 0
        quarantine_bytes = 0
        processed_dirs = 0
        for selection_item in selection.get("items") or []:
            item_id = selection_item["id"]
            plan_item = next((item for item in plan.get("items") or [] if item.get("id") == item_id), None)
            if not plan_item:
                continue
            result = self._execute_item(conn, plan_item, manifests.get(item_id) or [], mode, quarantine_root)
            item_results.append(result)
            total_success += result["success_count"]
            total_fail += result["failed_count"]
            total_bytes += result["processed_bytes"]
            quarantine_files += result.get("quarantine_files", 0)
            quarantine_bytes += result.get("quarantine_bytes", 0)
            processed_dirs += result.get("removed_dirs", 0)
        conn.close()
        free_after = self._safe_df()
        purge_token = random_token("PURGE")
        finished_at = now_iso()
        execution = {
            "schema": 1,
            "execution_id": self.execution_id,
            "plan_id": plan.get("plan_id"),
            "plan_hash": plan.get("plan_hash"),
            "selection_id": selection.get("selection_id"),
            "selection_hash": selection.get("selection_hash"),
            "analysis_id": plan.get("analysis_id"),
            "started_at": started_at,
            "finished_at": finished_at,
            "mode": mode,
            "status": "completed" if total_fail == 0 else "partial",
            "quarantine_root": str(quarantine_root) if quarantine_root else "",
            "quarantine_roots": sorted(self.quarantine_roots),
            "quarantine_files": quarantine_files,
            "quarantine_bytes": quarantine_bytes,
            "purge_token": purge_token,
            "free_before": free_before,
            "free_after": free_after,
            "items": item_results,
            "failures": self.failures,
            "summary": {
                "groups": len(item_results),
                "failed_groups": sum(1 for item in item_results if item.get("status") == "failed"),
                "success_files": total_success,
                "failed_files": total_fail,
                "processed_bytes": total_bytes,
                "released_bytes": total_bytes if mode == "delete" else 0,
                "quarantine_files": quarantine_files,
                "quarantine_bytes": quarantine_bytes,
                "removed_dirs": processed_dirs,
                "df_delta_bytes": (free_after.get("available_bytes", 0) - free_before.get("available_bytes", 0))
                if isinstance(free_after, dict) and isinstance(free_before, dict) else 0,
            },
            "journal_path": str(self.journal_path),
            "warnings": [
                "隔离模式的 released_bytes=0，因为隔离文件仍占用设备空间；purge 后再计算实际释放。",
                "文件在计划之后发生变化（大小/时间/inode 不同）会被跳过，需要重新扫描/计划。",
            ],
            "next_actions": [
                "运行 verify 验证清理结果；",
                "确认无误后可运行 quarantine purge --token <purge_token> 释放空间；",
                "如需恢复，运行 restore。",
            ],
        }
        atomic_write_json(self.exec_dir / "execution.json", execution)
        try:
            (self.exec_dir / "report-after.md").write_text(execution_markdown(execution), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - never hide a completed execution report
            (self.exec_dir / "report-after.md").write_text(f"# 报告渲染失败\n\n{exc}\n", encoding="utf-8")
        self.state.set_file("execution", self.exec_dir / "execution.json")
        self.state.set_file("execution_report", self.exec_dir / "report-after.md")
        return execution

    def _safe_df(self) -> dict[str, Any]:
        try:
            result = self.transport.df("/sdcard")
            return result if isinstance(result, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _quarantine_root(self) -> str:
        external = str(self.config["quarantine"]["external_root"]).rstrip("/")
        return normalize_device_path(external + "/" + self.execution_id)

    def _quarantine_target(self, path: str, quarantine_root: str) -> str:
        display = display_path(path)
        if is_under(display, "/sdcard"):
            rel = display[len("/sdcard"):].lstrip("/") or "root"
            return normalize_device_path(quarantine_root + "/files/sdcard/" + rel)
        if is_under(path, "/data"):
            rel = path[len("/data"):].lstrip("/") or "root"
            internal = str(self.config["quarantine"]["internal_root"]).rstrip("/")
            target = normalize_device_path(internal + "/" + self.execution_id + "/files/data/" + rel)
            self.quarantine_roots.add(normalize_device_path(internal + "/" + self.execution_id))
            return target
        raise SafetyError(f"不支持的隔离路径：{display}")

    def _execute_item(self, conn: sqlite3.Connection, plan_item: dict[str, Any], manifest: list[dict[str, Any]], mode: str, quarantine_root: str) -> dict[str, Any]:
        item_id = plan_item["id"]
        category = plan_item["category"]
        action = plan_item["action"]
        success = 0
        failed = 0
        processed_bytes = 0
        qfiles = 0
        qbytes = 0
        removed_dirs = 0
        errors: list[str] = []
        if not manifest:
            return self._fail_item(item_id, category, "无文件清单")
        target_dir = ""
        expected_files: list[dict[str, Any]] = []
        expected_dirs: list[dict[str, Any]] = []
        if action == "clean_dir_children":
            target = next((row for row in manifest if row.get("role") == "target_dir"), None)
            if not target:
                return self._fail_item(item_id, category, "缺少目标目录")
            target_dir = target["path"]
            guard = self._guard(target_dir, category, "dir")
            if not guard.get("allowed"):
                return self._fail_item(item_id, category, guard.get("reason") or "目标目录受保护", target_dir)
            current = self.transport.stat(target_dir)
            if not current or current.kind != "dir":
                return self._fail_item(item_id, category, "目标目录不存在或不是目录", target_dir)
            if not self._realpath_ok(target_dir):
                return self._fail_item(item_id, category, "目标目录 realpath 异常（可能是符号链接/重定向）", target_dir)
            expected_files = [row for row in manifest if row.get("role") == "delete" and row.get("kind") == "file"]
            expected_dirs = [row for row in manifest if row.get("kind") == "dir" and row.get("role") != "target_dir"]
        elif action == "delete_files":
            expected_files = [row for row in manifest if row.get("kind") == "file"]
        elif action == "rmdir":
            expected_dirs = [row for row in manifest if row.get("kind") == "dir"]
        else:
            return self._fail_item(item_id, category, f"未知 action：{action}")

        for row in expected_files:
            path = row["path"]
            reason = self._validate_file(path, row, category, target_dir)
            if reason:
                failed += 1
                errors.append((reason, path))
                self.failures.append({"path": path, "reason": reason, "item": item_id})
                self._journal({"event": "skipped", "path": path, "reason": reason, "item": item_id})
                continue
            try:
                if mode == "delete":
                    self.transport.delete_file(path)
                    if self.transport.stat(path) is not None:
                        raise TransportError("删除后文件仍然存在")
                    self._journal({"event": "deleted", "path": path, "size": int(row.get("size") or 0), "item": item_id})
                else:
                    dest = self._quarantine_target(path, quarantine_root)
                    if self.transport.stat(dest) is not None:
                        raise SafetyError(f"隔离目标已存在：{dest}")
                    self.transport.mkdirs(os.path.dirname(dest).replace("\\", "/"))
                    self.transport.move(path, dest)
                    moved = self.transport.stat(dest)
                    if not moved or moved.kind != "file" or int(moved.size) != int(row.get("size") or 0):
                        raise TransportError("隔离后校验失败（目标缺失或大小不一致）")
                    self._journal({
                        "event": "quarantined", "path": path, "quarantine_path": dest,
                        "size": int(row.get("size") or 0), "item": item_id,
                    })
                    qfiles += 1
                    qbytes += int(row.get("size") or 0)
                success += 1
                processed_bytes += int(row.get("size") or 0)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                message = str(exc)
                errors.append((message, path))
                self.failures.append({"path": path, "reason": message, "item": item_id})
                self._journal({"event": "failed", "path": path, "reason": message, "item": item_id})

        # Remove now-empty directories, only via rmdir; never recursive.
        dirs_to_try = list(expected_dirs)
        if action == "clean_dir_children" and target_dir and category == "residue":
            dirs_to_try.append({"path": target_dir, "kind": "dir"})
        if action == "clean_dir_children" and target_dir and category != "residue":
            dirs_to_try.append({"path": target_dir, "kind": "dir"})
        dirs_to_try.sort(key=lambda row: row.get("path", "").count("/"), reverse=True)
        for row in dirs_to_try:
            path = row.get("path")
            if not path:
                continue
            if action == "clean_dir_children" and target_dir and path == target_dir and category != "residue":
                continue  # keep cache root
            guard = self._guard(path, category, "dir")
            if not guard.get("allowed"):
                continue
            try:
                current = self.transport.stat(path)
                if not current or current.kind != "dir":
                    continue
                self.transport.rmdir(path)
                remaining = self.transport.stat(path)
                if remaining is None:
                    removed_dirs += 1
                    if action == "rmdir":
                        success += 1
                        processed_bytes += int(row.get("size") or 0)
                    self._journal({"event": "rmdir", "path": path, "item": item_id})
            except Exception as exc:  # noqa: BLE001
                if action == "rmdir":
                    failed += 1
                    message = f"rmdir 失败（目录可能非空/被占用）：{exc}"
                    errors.append((message, path))
                    self.failures.append({"path": path, "reason": message, "item": item_id})
                # For clean_dir_children, non-empty directories are expected and harmless.
                continue
        status = "success" if failed == 0 else ("partial" if success else "failed")
        if action == "rmdir" and success == 0 and failed == 0:
            status = "success"
        return self._item_result(item_id, category, status, success, failed, processed_bytes, errors=errors,
                                 quarantine_files=qfiles, quarantine_bytes=qbytes, removed_dirs=removed_dirs)

    def _validate_file(self, path: str, row: dict[str, Any], category: str, target_dir: str) -> str:
        try:
            path = normalize_device_path(path)
        except Exception as exc:  # noqa: BLE001
            return f"非法路径：{exc}"
        guard = self._guard(path, category, "file")
        if not guard.get("allowed"):
            return f"路径受保护/不符合类别：{guard.get('reason')}"
        if target_dir and not is_under(path, target_dir):
            return f"文件不在目标目录内：{target_dir}"
        current = self.transport.stat(path)
        if current is None:
            return "文件已不存在"
        if current.kind != "file":
            return f"目标类型不是普通文件：{current.kind}"
        expected_size = int(row.get("size") or 0)
        if int(current.size) != expected_size:
            return f"文件大小已变化（计划 {expected_size}，当前 {current.size}）"
        expected_mtime = row.get("mtime")
        if expected_mtime is not None and current.mtime is not None and int(current.mtime) != int(expected_mtime):
            return "文件修改时间已变化"
        expected_inode = row.get("inode")
        if expected_inode is not None and current.inode is not None and int(current.inode) != int(expected_inode):
            return "文件 inode 已变化（可能被替换）"
        if not self._realpath_ok(path):
            return "文件路径 realpath 异常（可能是符号链接/重定向）"
        return ""

    def _realpath_ok(self, path: str) -> bool:
        real = self.transport.realpath(path)
        if not real:
            return False
        if display_path(real) == display_path(path):
            return True
        # Mock/local may return an equivalent path with different mount alias.
        return os.path.normpath(real) == os.path.normpath(path)

    def _fail_item(self, item_id: str, category: str, message: str, target: str = "") -> dict[str, Any]:
        """Record an item-level failure that happens before any per-file loop."""
        self.failures.append({"path": target or "", "reason": message, "item": item_id})
        return self._item_result(item_id, category, "failed", 0, 1, 0, errors=[(message, target)])

    def _item_result(self, item_id: str, category: str, status: str, success: int, failed: int,
                     processed: int, *, errors: list[tuple[str, str]] | None = None,
                     quarantine_files: int = 0, quarantine_bytes: int = 0, removed_dirs: int = 0) -> dict[str, Any]:
        error = ""
        if errors:
            error = "; ".join(f"{reason}：{path}" for reason, path in errors[:3])
            if len(errors) > 3:
                error += f" 等 {len(errors)} 项"
        return {
            "id": item_id,
            "category": category,
            "status": status,
            "success_count": success,
            "failed_count": failed,
            "processed_bytes": processed,
            "quarantine_files": quarantine_files,
            "quarantine_bytes": quarantine_bytes,
            "removed_dirs": removed_dirs,
            "error": error,
        }

    def _journal(self, record: dict[str, Any]) -> None:
        with open(self.journal_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- post actions
def load_execution(state: State) -> tuple[dict[str, Any], str]:
    path = state.require("execution", "执行结果")
    execution = read_json(path, default=None)
    if not isinstance(execution, dict) or not execution.get("execution_id"):
        raise ValidationError("execution.json 无效")
    return execution, str(path)


def verify(state: State, transport: Transport, config: dict[str, Any]) -> dict[str, Any]:
    execution, execution_path = load_execution(state)
    journal_path = execution.get("journal_path") or ""
    entries: list[dict[str, Any]] = []
    if journal_path and Path(journal_path).exists():
        with open(journal_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    failures: list[dict[str, str]] = []
    checked = passed = 0
    source_present = 0
    quarantine_missing = 0
    restored_items = {
        entry.get("item") for entry in entries
        if entry.get("restore_status") == "restored" and entry.get("item")
    }
    for entry in entries:
        if entry.get("event") in {"skipped", "failed", "restored"}:
            continue
        if entry.get("restore_status") == "restored" or entry.get("purge_status") == "purged":
            continue
        if entry.get("event") == "rmdir" and entry.get("item") in restored_items:
            continue
        checked += 1
        path = entry.get("path", "")
        if entry.get("event") == "quarantined":
            current = transport.stat(path) if path else None
            if current is not None:
                failures.append({"path": path, "reason": "源文件仍然存在（可能被应用重新创建，或移动失败）"})
                source_present += 1
                continue
            qpath = entry.get("quarantine_path", "")
            qstat = transport.stat(qpath) if qpath else None
            if not qstat or qstat.kind != "file":
                failures.append({"path": qpath, "reason": "隔离文件缺失"})
                quarantine_missing += 1
                continue
            if int(qstat.size) != int(entry.get("size") or 0):
                failures.append({"path": qpath, "reason": "隔离文件大小与记录不一致"})
                continue
            passed += 1
        elif entry.get("event") == "deleted":
            current = transport.stat(path) if path else None
            if current is not None:
                failures.append({"path": path, "reason": "文件仍然存在"})
                source_present += 1
                continue
            passed += 1
        elif entry.get("event") == "rmdir":
            current = transport.stat(path) if path else None
            if current is not None:
                failures.append({"path": path, "reason": "目录仍然存在（可能非空）"})
                continue
            passed += 1
    free_now = {}
    try:
        free_now = transport.df("/sdcard")
    except Exception:  # noqa: BLE001
        free_now = {}
    before = execution.get("free_after") or {}
    free_delta = 0
    if isinstance(free_now, dict) and isinstance(before, dict):
        free_delta = int(free_now.get("available_bytes") or 0) - int(before.get("available_bytes") or 0)
    verification = {
        "schema": 1,
        "verification_id": "verify-" + datetime.now().strftime("%Y%m%d-%H%M%S"),
        "execution_id": execution.get("execution_id"),
        "verified_at": now_iso(),
        "summary": {
            "checked": checked,
            "passed": passed,
            "failed": len(failures),
            "source_present": source_present,
            "quarantine_missing": quarantine_missing,
            "free_delta": free_delta,
        },
        "failures": failures,
        "free_before": execution.get("free_before"),
        "free_after_execution": execution.get("free_after"),
        "free_now": free_now,
        "note": "隔离模式在 purge 前不会释放空间；负的 free_delta 可能是系统写入/日志造成。",
    }
    out_dir = state.dir("execution")
    atomic_write_json(out_dir / "verification.json", verification)
    (out_dir / "report-verify.md").write_text(verification_markdown(verification), encoding="utf-8")
    state.set_file("verification", out_dir / "verification.json")
    state.set_file("verification_report", out_dir / "report-verify.md")
    execution["verification_path"] = str(out_dir / "verification.json")
    execution["verification_summary"] = verification["summary"]
    atomic_write_json(execution_path, execution)
    return verification


def purge(state: State, transport: Transport, token: str) -> dict[str, Any]:
    execution, execution_path = load_execution(state)
    if execution.get("mode") != "quarantine":
        raise SafetyError("只有隔离模式可以 purge")
    expected = str(execution.get("purge_token") or "")
    if not token or not expected or not hmac.compare_digest(str(token), expected):
        raise SafetyError("purge 令牌不正确")
    journal_path = execution.get("journal_path") or ""
    entries: list[dict[str, Any]] = []
    if journal_path and Path(journal_path).exists():
        with open(journal_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    qroot = str(execution.get("quarantine_root") or "")
    all_roots = [str(root) for root in (execution.get("quarantine_roots") or []) if root]
    if qroot and qroot not in all_roots:
        all_roots.append(qroot)
    if not all_roots:
        raise SafetyError("execution 缺少 quarantine_root")
    purged = 0
    purged_bytes = 0
    failures: list[dict[str, str]] = []
    updated: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("event") != "quarantined":
            updated.append(entry)
            continue
        qpath = str(entry.get("quarantine_path") or "")
        if not qpath or not any(is_under(qpath, root) for root in all_roots):
            failures.append({"path": qpath, "reason": "隔离路径不在本次隔离区内，拒绝"})
            updated.append(entry)
            continue
        current = transport.stat(qpath)
        if current is None:
            entry["purge_status"] = "missing"
            updated.append(entry)
            continue
        if current.kind != "file":
            failures.append({"path": qpath, "reason": "隔离目标不是普通文件"})
            updated.append(entry)
            continue
        try:
            transport.delete_file(qpath)
            if transport.stat(qpath) is not None:
                raise TransportError("purge 后文件仍然存在")
            entry["purge_status"] = "purged"
            purged += 1
            purged_bytes += int(entry.get("size") or 0)
        except Exception as exc:  # noqa: BLE001
            failures.append({"path": qpath, "reason": str(exc)})
        updated.append(entry)
    with open(journal_path, "w", encoding="utf-8") as fh:
        for entry in updated:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    free_after = {}
    try:
        free_after = transport.df("/sdcard")
    except Exception:  # noqa: BLE001
        pass
    before = execution.get("free_after") or {}
    free_delta = 0
    if isinstance(free_after, dict) and isinstance(before, dict):
        free_delta = int(free_after.get("available_bytes") or 0) - int(before.get("available_bytes") or 0)
    execution["purged_at"] = now_iso()
    execution["purge"] = {
        "purged_files": purged,
        "purged_bytes": purged_bytes,
        "failed": len(failures),
        "failures": failures,
        "free_delta": free_delta,
        "free_after": free_after,
    }
    execution["status"] = "purged" if not failures else "partial"
    atomic_write_json(execution_path, execution)
    out_dir = state.dir("execution")
    lines = [
        "# 隔离区清理报告（purge）",
        "",
        f"- 已永久删除隔离文件：{purged} 个 / {human_bytes(purged_bytes)}",
        f"- 失败：{len(failures)}",
        f"- 可用空间变化：{human_bytes(free_delta)}",
        "",
    ]
    for failure in failures[:100]:
        lines.append(f"- {md_code(failure.get('path', ''))}：{failure.get('reason', '')}")
    (out_dir / "report-purge.md").write_text("\n".join(lines), encoding="utf-8")
    return execution["purge"]


def restore(state: State, transport: Transport, ids: list[str] | None, restore_all: bool) -> dict[str, Any]:
    execution, execution_path = load_execution(state)
    if execution.get("mode") != "quarantine":
        raise SafetyError("只有隔离模式可以 restore")
    journal_path = execution.get("journal_path") or ""
    if not journal_path or not Path(journal_path).exists():
        raise ValidationError("journal.jsonl 缺失，无法恢复")
    selected = set(ids or [])
    entries: list[dict[str, Any]] = []
    restored = 0
    restored_bytes = 0
    failures: list[dict[str, str]] = []
    with open(journal_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            item_id = entry.get("item", "")
            if entry.get("event") != "quarantined" or entry.get("purge_status") == "purged" or entry.get("restore_status") == "restored":
                entries.append(entry)
                continue
            if not restore_all and item_id not in selected:
                entries.append(entry)
                continue
            source = entry.get("path", "")
            qpath = entry.get("quarantine_path", "")
            if not source or not qpath:
                failures.append({"path": str(source), "reason": "记录缺失"})
                entries.append(entry)
                continue
            if transport.stat(source) is not None:
                failures.append({"path": source, "reason": "原路径已存在，拒绝覆盖"})
                entries.append(entry)
                continue
            qstat = transport.stat(qpath)
            if not qstat or qstat.kind != "file":
                failures.append({"path": qpath, "reason": "隔离文件缺失"})
                entries.append(entry)
                continue
            try:
                transport.mkdirs(os.path.dirname(source).replace("\\", "/"))
                transport.move(qpath, source)
                if transport.stat(source) is None:
                    raise TransportError("恢复后源文件不存在")
                entry["restore_status"] = "restored"
                restored += 1
                restored_bytes += int(entry.get("size") or 0)
            except Exception as exc:  # noqa: BLE001
                failures.append({"path": source, "reason": str(exc)})
            entries.append(entry)
    with open(journal_path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    execution["restored_at"] = now_iso()
    execution["restore"] = {"restored_files": restored, "restored_bytes": restored_bytes, "failed": len(failures), "failures": failures}
    atomic_write_json(execution_path, execution)
    out_dir = state.dir("execution")
    lines = ["# 隔离区恢复报告", "", f"- 已恢复：{restored} 个 / {human_bytes(restored_bytes)}", f"- 失败：{len(failures)}", ""]
    for failure in failures[:100]:
        lines.append(f"- {md_code(failure.get('path', ''))}：{failure.get('reason', '')}")
    (out_dir / "report-restore.md").write_text("\n".join(lines), encoding="utf-8")
    return execution["restore"]
