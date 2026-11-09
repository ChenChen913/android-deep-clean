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

from .device import transport_device_identity
from .html_report import write_cleanup_html
from .reports import execution_markdown, verification_markdown
from .platform import match_harmony_photo_root
from .rules import guard_path
from .scanner import load_packages
from .state import State
from .transport import Transport, TransportError
from .util import (
    RISK_ORDER,
    SafetyError,
    ValidationError,
    atomic_write_json,
    atomic_write_text,
    compute_files_digest,
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
    sha256_file,
    sha256_text,
)


class DeviceLostError(TransportError):
    """Raised when the USB/ADB device disappears mid-execution."""


def _recompute_plan_hash(plan: dict[str, Any]) -> str:
    return compute_plan_hash(plan)


def _recompute_selection_hash(selection: dict[str, Any]) -> str:
    return compute_selection_hash(selection)


class Executor:
    platform = "android"
    def __init__(self, transport: Transport, config: dict[str, Any], state: State, *, backup_dir: str | None = None) -> None:
        self.transport = transport
        self.config = config
        self.state = state
        self.packages = self._load_packages()
        self.execution_id = "exec-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + random_token("").replace("ADC-", "")
        self.exec_dir = state.dir("execution")
        self.journal_path = self.exec_dir / "journal.jsonl"
        self.quarantine_roots: set[str] = set()
        self.failures: list[dict[str, str]] = []
        self.backup_dir = Path(backup_dir).expanduser().resolve() if backup_dir else None
        self.backup_root = self.backup_dir / self.execution_id if self.backup_dir else None
        self.backup_manifest: list[dict[str, Any]] = []
        self.backup_bytes = 0
        self.platform = "android"

    def _load_packages(self):
        scan_path = self.state.require("scan", "扫描结果")
        conn = sqlite3.connect(str(scan_path))
        conn.row_factory = sqlite3.Row
        try:
            return load_packages(conn)
        finally:
            conn.close()

    # ------------------------------------------------------------- preflight
    def _consumed_path(self) -> Path:
        return self.exec_dir / "consumed-tokens.json"

    def _consumed_tokens(self) -> dict[str, dict[str, Any]]:
        data = read_json(self._consumed_path(), default=None)
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items() if isinstance(value, dict)}

    def _token_consumed(self, token: str) -> bool:
        if not token:
            return False
        return sha256_text(str(token)) in self._consumed_tokens()

    def _mark_consumed(self, selection: dict[str, Any], execution: dict[str, Any]) -> None:
        token = str(selection.get("token") or "")
        if not token:
            return
        consumed = self._consumed_tokens()
        consumed[sha256_text(token)] = {
            "selection_id": selection.get("selection_id", ""),
            "execution_id": execution.get("execution_id", ""),
            "status": execution.get("status", ""),
            "mode": execution.get("mode", ""),
            "consumed_at": execution.get("finished_at", ""),
        }
        atomic_write_json(self._consumed_path(), consumed)

    def _load_analysis_db(self, plan: dict[str, Any]) -> sqlite3.Connection:
        path = plan.get("analysis_db_path") or ""
        db_path = Path(path) if path else None
        if not db_path or not db_path.exists():
            raise ValidationError("analysis.db 不存在；请重新运行 analyze 和 plan")
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _guard(self, path: str, category: str, kind: str) -> dict[str, Any]:
        return guard_path(
            path, category, kind=kind, packages=self.packages, config=self.config, platform=self.platform
        )

    def _preflight(self, plan: dict[str, Any], selection: dict[str, Any], token: str, mode: str, allow_permanent: bool) -> tuple[sqlite3.Connection, dict[str, list[dict[str, Any]]]]:
        self.platform = str(plan.get("platform") or selection.get("platform") or "android")
        if not token or not hmac.compare_digest(str(token), str(selection.get("token") or "")):
            raise SafetyError("确认令牌不匹配。请使用 select 输出中的令牌，并确认用户已明确同意。")
        if self._token_consumed(token):
            raise SafetyError(
                "该确认令牌已执行过，不能重复使用。"
                "如果上一次执行被恢复或只完成了一部分，请重新 scan → analyze → plan → select，"
                "让用户重新确认后再执行。"
            )
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
        expected_device = plan.get("device_identity") or selection.get("device_identity") or {}
        expected_id = str((expected_device or {}).get("id") or "")
        if not expected_id:
            raise SafetyError("计划缺少设备身份，拒绝执行。请重新运行 doctor/scan/analyze/plan。")
        current_device = transport_device_identity(self.transport)
        if current_device.get("id") != expected_id:
            raise SafetyError(
                "当前连接的设备与计划设备不一致，拒绝执行。"
                f" 计划：{expected_device.get('serial') or expected_id}；"
                f" 当前：{current_device.get('serial') or current_device.get('id')}。"
                "请确认 USB 连接的是同一台手机，重新扫描并生成计划。"
            )
        sel_device_id = str(((selection.get("device_identity") or {}).get("id")) or "")
        if sel_device_id and sel_device_id != expected_id:
            raise SafetyError("selection 的设备身份与 plan 不一致，拒绝执行")
        if not self.transport.health_ok():
            raise SafetyError("设备当前不可用/已断开，未执行任何操作。")
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
            expected_digest = str(plan_item.get("files_digest") or "")
            if not expected_digest:
                raise SafetyError(f"项目 {item_id} 缺少文件清单摘要，拒绝执行；请重新 analyze 和 plan")
            if compute_files_digest(rows) != expected_digest:
                raise SafetyError(f"项目 {item_id} 的文件清单与计划不一致（analysis.db 可能被修改），拒绝执行")
            manifests[item_id] = [dict(row) for row in rows]
        return conn, manifests

    # ------------------------------------------------------------- execution
    def run(self, plan: dict[str, Any], selection: dict[str, Any], token: str, mode: str, allow_permanent: bool) -> dict[str, Any]:
        conn, manifests = self._preflight(plan, selection, token, mode, allow_permanent)
        current_identity = transport_device_identity(self.transport)
        expected_identity_id = str((plan.get("device_identity") or {}).get("id") or "")
        if expected_identity_id and current_identity.get("id") != expected_identity_id:
            conn.close()
            raise SafetyError("执行前设备身份再次校验失败，拒绝执行")
        started_at = now_iso()
        free_before = self._safe_df()
        self.journal_path.write_text("", encoding="utf-8")
        if self.backup_root is not None:
            try:
                self.backup_root.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                conn.close()
                raise SafetyError(f"无法创建电脑备份目录 {self.backup_root}：{exc}") from exc
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
        abort_reason = ""
        selected_items = list(selection.get("items") or [])
        for index, selection_item in enumerate(selected_items):
            item_id = selection_item["id"]
            plan_item = next((item for item in plan.get("items") or [] if item.get("id") == item_id), None)
            if not plan_item:
                continue
            if not self.transport.health_ok():
                abort_reason = "设备连接已断开，后续项目已停止执行"
                item_results.append(self._aborted_item(plan_item, abort_reason))
                total_fail += 1
                for rest in selected_items[index + 1:]:
                    rest_plan = next((entry for entry in plan.get("items") or [] if entry.get("id") == rest["id"]), None)
                    if rest_plan:
                        item_results.append(self._aborted_item(rest_plan, abort_reason))
                        total_fail += 1
                break
            try:
                result = self._execute_item(conn, plan_item, manifests.get(item_id) or [], mode, quarantine_root)
            except DeviceLostError as exc:
                abort_reason = str(exc)
                item_results.append(self._aborted_item(plan_item, abort_reason))
                total_fail += 1
                for rest in selected_items[index + 1:]:
                    rest_plan = next((entry for entry in plan.get("items") or [] if entry.get("id") == rest["id"]), None)
                    if rest_plan:
                        item_results.append(self._aborted_item(rest_plan, abort_reason))
                        total_fail += 1
                self.failures.append({"path": "", "reason": abort_reason, "item": item_id})
                break
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
        discovered_bytes = int((plan.get("summary") or {}).get("bytes") or 0)
        selected_bytes = int((selection.get("summary") or {}).get("bytes") or 0)
        execution = {
            "schema": 1,
            "execution_id": self.execution_id,
            "plan_id": plan.get("plan_id"),
            "plan_hash": plan.get("plan_hash"),
            "selection_id": selection.get("selection_id"),
            "selection_hash": selection.get("selection_hash"),
            "analysis_id": plan.get("analysis_id"),
            "device_identity": current_identity,
            "platform": self.platform,
            "transport": self.transport.name,
            "started_at": started_at,
            "finished_at": finished_at,
            "mode": mode,
            "status": "aborted" if abort_reason else ("completed" if total_fail == 0 else "partial"),
            "abort_reason": abort_reason,
            "quarantine_root": str(quarantine_root) if quarantine_root else "",
            "quarantine_roots": sorted(self.quarantine_roots),
            "quarantine_files": quarantine_files,
            "quarantine_bytes": quarantine_bytes,
            "backup_dir": str(self.backup_dir) if self.backup_dir else "",
            "backup_root": str(self.backup_root) if self.backup_root else "",
            "backup_files": len(self.backup_manifest),
            "backup_bytes": self.backup_bytes,
            "purge_token": purge_token,
            "free_before": free_before,
            "free_after": free_after,
            "items": item_results,
            "failures": self.failures,
            "summary": {
                "groups": len(item_results),
                "failed_groups": sum(1 for item in item_results if item.get("status") == "failed"),
                "aborted_groups": sum(1 for item in item_results if item.get("status") == "aborted"),
                "success_files": total_success,
                "failed_files": total_fail,
                "discovered_bytes": discovered_bytes,
                "selected_bytes": selected_bytes,
                "processed_bytes": total_bytes,
                "unprocessed_bytes": max(0, selected_bytes - total_bytes),
                "released_bytes": total_bytes if mode == "delete" else 0,
                "quarantine_files": quarantine_files,
                "quarantine_bytes": quarantine_bytes,
                "quarantine_occupied_bytes": quarantine_bytes,
                "backup_files": len(self.backup_manifest),
                "backup_bytes": self.backup_bytes,
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
        self._mark_consumed(selection, execution)
        if self.backup_root is not None:
            atomic_write_json(self.exec_dir / "backup-manifest.json", {
                "execution_id": self.execution_id,
                "backup_root": str(self.backup_root),
                "files": self.backup_manifest,
            })
        try:
            (self.exec_dir / "report-after.md").write_text(execution_markdown(execution), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - never hide a completed execution report
            (self.exec_dir / "report-after.md").write_text(f"# 报告渲染失败\n\n{exc}\n", encoding="utf-8")
        self.state.set_file("execution", self.exec_dir / "execution.json")
        self.state.set_file("execution_report", self.exec_dir / "report-after.md")
        _write_cleanup_html(self.state, execution)
        return execution

    def _safe_df(self) -> dict[str, Any]:
        path = "/storage/media/100/local/files" if self.platform == "harmonyos_next" else "/sdcard"
        try:
            result = self.transport.df(path)
            return result if isinstance(result, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _quarantine_root(self) -> str:
        if self.platform == "harmonyos_next":
            external = str(self.config["quarantine"].get("harmony_external_root")
                           or "/storage/media/100/local/files/Docs/.android-deep-clean/quarantine").rstrip("/")
        else:
            external = str(self.config["quarantine"]["external_root"]).rstrip("/")
        return normalize_device_path(external + "/" + self.execution_id)

    def _quarantine_target(self, path: str, quarantine_root: str) -> str:
        display = display_path(path)
        if is_under(display, "/sdcard"):
            rel = display[len("/sdcard"):].lstrip("/") or "root"
            return normalize_device_path(quarantine_root + "/files/sdcard/" + rel)
        if self.platform == "harmonyos_next":
            photo_root = match_harmony_photo_root(display)
            for root, tag in (("/storage/media/100/local/files", "public"), (photo_root, "photo")):
                if root and is_under(display, root):
                    rel = display[len(root):].lstrip("/") or "root"
                    return normalize_device_path(quarantine_root + "/files/" + tag + "/" + rel)
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
            backup_info: dict[str, Any] = {}
            if self.backup_root is not None:
                try:
                    backup_info = self._backup_file(path, row, item_id)
                except Exception as exc:  # noqa: BLE001
                    if not self.transport.health_ok():
                        raise DeviceLostError(f"设备在备份过程中断开：{exc}") from exc
                    failed += 1
                    message = f"备份到电脑失败，已跳过清理：{exc}"
                    errors.append((message, path))
                    self.failures.append({"path": path, "reason": message, "item": item_id})
                    self._journal({"event": "skipped", "path": path, "reason": message, "item": item_id})
                    continue
            try:
                if mode == "delete":
                    self.transport.delete_file(path)
                    if self.transport.stat(path) is not None:
                        raise TransportError("删除后文件仍然存在")
                    self._journal({"event": "deleted", "path": path, "size": int(row.get("size") or 0), "item": item_id, **backup_info})
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
                        "size": int(row.get("size") or 0), "item": item_id, **backup_info,
                    })
                    qfiles += 1
                    qbytes += int(row.get("size") or 0)
                success += 1
                processed_bytes += int(row.get("size") or 0)
            except Exception as exc:  # noqa: BLE001
                if not self.transport.health_ok():
                    raise DeviceLostError(f"设备在清理过程中断开：{exc}") from exc
                failed += 1
                message = str(exc)
                errors.append((message, path))
                self.failures.append({"path": path, "reason": message, "item": item_id})
                self._journal({"event": "failed", "path": path, "reason": message, "item": item_id})

        # Remove now-empty directories, only via rmdir; never recursive.
        dirs_to_try = list(expected_dirs)
        if action == "clean_dir_children" and target_dir:
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
                if not self.transport.health_ok():
                    raise DeviceLostError(f"设备在清理过程中断开：{exc}") from exc
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

    def _backup_file(self, path: str, row: dict[str, Any], item_id: str) -> dict[str, Any]:
        """Copy one device file to the PC backup directory and verify it.

        This runs before quarantine/delete.  If backup or verification fails,
        the caller skips cleanup for that file (source stays on the phone).
        """
        if self.backup_root is None:
            return {}
        shown = display_path(path)
        if is_under(shown, "/sdcard"):
            namespace = "sdcard"
            rel = shown[len("/sdcard"):].lstrip("/")
        elif is_under(path, "/data"):
            namespace = "data"
            rel = path[len("/data"):].lstrip("/")
        else:
            raise SafetyError(f"不支持的备份源路径：{shown}")
        if not rel or ".." in Path(rel).parts:
            raise SafetyError(f"备份相对路径非法：{shown}")
        dest = (self.backup_root / namespace / Path(rel)).resolve()
        try:
            dest.relative_to(self.backup_root.resolve())
        except ValueError as exc:
            raise SafetyError(f"备份目标逃逸：{dest}") from exc
        if dest.exists():
            raise SafetyError(f"电脑备份目标已存在：{dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        expected_size = int(row.get("size") or 0)
        try:
            written = self.transport.pull(path, str(dest))
        except Exception:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        if int(written) != expected_size:
            try:
                dest.unlink()
            except OSError:
                pass
            raise SafetyError(f"备份大小不一致：手机 {expected_size}B，电脑 {written}B")
        local_hash = sha256_file(dest)
        device_hash: str | None = None
        try:
            hashes = self.transport.hash_files([path])
            device_hash = hashes.get(path)
        except Exception:  # noqa: BLE001
            device_hash = None
        if device_hash and device_hash != local_hash:
            try:
                dest.unlink()
            except OSError:
                pass
            raise SafetyError("备份 SHA-256 与手机端不一致，已删除不完整备份并跳过清理")
        entry = {
            "path": path,
            "backup_path": str(dest),
            "sha256": local_hash,
            "size": expected_size,
            "item": item_id,
            "verified_by": "sha256" if device_hash else "size",
        }
        self.backup_manifest.append(entry)
        self.backup_bytes += expected_size
        return {"backup_path": str(dest), "backup_sha256": local_hash, "backup_verified_by": entry["verified_by"]}

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

    def _aborted_item(self, plan_item: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            "id": plan_item.get("id", ""),
            "category": plan_item.get("category", ""),
            "status": "aborted",
            "success_count": 0,
            "failed_count": 0,
            "processed_bytes": 0,
            "quarantine_files": 0,
            "quarantine_bytes": 0,
            "removed_dirs": 0,
            "error": reason,
        }

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


def _load_optional_json(path_value: str) -> dict[str, Any]:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    data = read_json(path, default=None)
    return data if isinstance(data, dict) else {}


def _journal_totals(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Aggregate durable outcomes from the journal.

    ``purge`` and ``restore`` can run several times against the same
    execution.  Totals must therefore come from the journal, not from the
    counters of the latest invocation, otherwise a second (idempotent) run
    would rewrite history as "0 files deleted" / "0 files restored".
    """
    totals = {
        "purged_files": 0,
        "purged_bytes": 0,
        "missing_files": 0,
        "restored_files": 0,
        "restored_bytes": 0,
    }
    for entry in entries:
        if entry.get("event") != "quarantined":
            continue
        size = int(entry.get("size") or 0)
        if entry.get("purge_status") == "purged":
            totals["purged_files"] += 1
            totals["purged_bytes"] += size
        elif entry.get("purge_status") == "missing":
            totals["missing_files"] += 1
        if entry.get("restore_status") == "restored":
            totals["restored_files"] += 1
            totals["restored_bytes"] += size
    return totals


def _quarantine_remaining(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Files that are still stored in the quarantine area."""
    files = 0
    size = 0
    for entry in entries:
        if entry.get("event") != "quarantined":
            continue
        if entry.get("purge_status") in {"purged", "missing"}:
            continue
        if entry.get("restore_status") == "restored":
            continue
        files += 1
        size += int(entry.get("size") or 0)
    return {"remaining_files": files, "remaining_bytes": size}


def _quarantine_roots(execution: dict[str, Any]) -> list[str]:
    qroot = str(execution.get("quarantine_root") or "")
    roots = [str(root) for root in (execution.get("quarantine_roots") or []) if root]
    if qroot and qroot not in roots:
        roots.append(qroot)
    if not roots:
        raise SafetyError("execution 缺少 quarantine_root")
    return roots


def _load_restore_packages(state: State) -> Any:
    """Load the scan package index for restore path guards.

    Missing or unreadable scan state is not fatal: ``guard_path`` still
    applies every other rule and fails closed for paths that need the index.
    """
    scan_path = state.get_file("scan")
    if not scan_path:
        return None
    path = Path(scan_path)
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            return load_packages(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - restore keeps the remaining guard rules
        return None


def _hash_quarantine_files(
    transport: Transport,
    paths: list[str],
) -> tuple[str, dict[str, str | None], str]:
    """Return ``(tool, hashes, error)`` for a batch of quarantine files.

    ``tool == ""`` means the device has no hash tool: this is a deliberate
    degradation, not a verification failure.  A non-empty ``error`` means the
    tool exists but hashing could not be completed.
    """
    if not paths:
        return "", {}, ""
    try:
        tool = transport.hash_tool()
    except Exception as exc:  # noqa: BLE001 - must be reported, never hidden
        return "", {}, str(exc)
    if not tool:
        return "", {}, ""
    try:
        return tool, transport.hash_files(paths), ""
    except Exception as exc:  # noqa: BLE001 - must be reported, never hidden
        return tool, {}, str(exc)


def _quarantine_integrity(
    entry: dict[str, Any],
    current: Any,
    actual_hash: str | None,
    *,
    hash_tool: str,
    hash_error: str = "",
) -> tuple[str, str]:
    """Classify the quarantine file before purge/restore.

    Returns ``(status, reason)``.  ``ok_hash`` / ``ok_size`` / ``size_only``
    may proceed; ``size_changed`` / ``hash_mismatch`` / ``incomplete`` must be
    recorded as failures.  ``size_only`` is a deliberate degradation for
    devices without a comparable hash tool and must be surfaced in reports.
    """
    expected_size = int(entry.get("size") or 0)
    actual_size = int(getattr(current, "size", 0) or 0)
    if actual_size != expected_size:
        return (
            "size_changed",
            f"隔离文件大小已变化（隔离时 {expected_size}B，当前 {actual_size}B），"
            "拒绝永久删除或恢复",
        )
    expected_hash = str(entry.get("backup_sha256") or "")
    if not expected_hash:
        return "ok_size", ""
    if hash_error:
        return "incomplete", f"完整性校验未完成：{hash_error}"
    if not hash_tool:
        return "size_only", "设备没有 sha256sum/md5sum，仅校验大小"
    if not actual_hash:
        return "incomplete", "完整性校验未完成：无法计算隔离文件哈希"
    if len(actual_hash) != len(expected_hash):
        return "size_only", "设备仅提供 MD5，无法与 SHA-256 基准比较，仅校验大小"
    if actual_hash != expected_hash:
        return (
            "hash_mismatch",
            "隔离文件 SHA-256 与备份记录不一致（内容可能已被替换），拒绝永久删除或恢复",
        )
    return "ok_hash", ""


def _previous_runs(
    record: Any,
    *,
    files_key: str,
    bytes_key: str,
    fallback_at: str = "",
) -> list[dict[str, Any]]:
    """Return the recorded run history, upgrading an older single-run record."""
    previous = record if isinstance(record, dict) else {}
    runs = [item for item in previous.get("runs") or [] if isinstance(item, dict)]
    if runs or not previous:
        return runs
    if int(previous.get(files_key) or 0) <= 0 and int(previous.get("failed") or 0) <= 0:
        return runs
    # execution.json written before run history existed only kept the last run.
    # Keep it even when it only contains failures (0 successful files).
    return [{
        "at": previous.get("at") or fallback_at,
        files_key: int(previous.get(files_key) or 0),
        bytes_key: int(previous.get(bytes_key) or 0),
        "failed": int(previous.get("failed") or 0),
        "failures": previous.get("failures") or [],
        "free_delta": int(previous.get("free_delta") or 0),
    }]


def _write_cleanup_html(
    state: State,
    execution: dict[str, Any],
    verification: dict[str, Any] | None = None,
) -> Path:
    """Write the visual cleanup archive without ever failing the real operation."""
    out_dir = state.dir("execution")
    path = out_dir / "report-cleanup.html"
    analysis = _load_optional_json(state.get_file("analysis_summary"))
    selection = _load_optional_json(state.get_file("selection"))
    if verification is None:
        verification = _load_optional_json(state.get_file("verification"))
    try:
        write_cleanup_html(analysis, selection, execution, verification, path)
    except Exception as exc:  # noqa: BLE001 - a rendering bug must not fail a completed cleanup
        import html as html_module

        path.write_text(
            "<!DOCTYPE html>\n<html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<title>清理结果报告</title></head><body><h1>报告渲染失败</h1><p>"
            + html_module.escape(str(exc))
            + "</p><p>请查看 execution/report-after.md、execution.json 与 journal.jsonl。</p></body></html>\n",
            encoding="utf-8",
        )
    state.set_file("cleanup_report_html", path)
    return path


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
    roots = _quarantine_roots(execution) if any(
        entry.get("event") == "quarantined" for entry in entries
    ) else []
    hash_targets: list[str] = []
    for entry in entries:
        if entry.get("event") != "quarantined":
            continue
        if entry.get("restore_status") == "restored" or entry.get("purge_status") == "purged":
            continue
        if not entry.get("backup_sha256"):
            continue
        try:
            qpath = normalize_device_path(str(entry.get("quarantine_path") or ""))
        except Exception:  # noqa: BLE001 - reported by the verify pass below
            continue
        if qpath and any(is_under(qpath, root) for root in roots):
            hash_targets.append(qpath)
    hash_tool, verification_hashes, hash_error = _hash_quarantine_files(transport, hash_targets)
    hash_verified = 0
    hash_unavailable = 0
    hash_incomplete = 0
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
            if qpath and roots and not any(is_under(qpath, root) for root in roots):
                failures.append({"path": qpath, "reason": "隔离路径不在本次隔离区内"})
                continue
            qstat = transport.stat(qpath) if qpath else None
            if not qstat or qstat.kind != "file":
                failures.append({"path": qpath, "reason": "隔离文件缺失"})
                quarantine_missing += 1
                continue
            status, reason = _quarantine_integrity(
                entry,
                qstat,
                verification_hashes.get(qpath),
                hash_tool=hash_tool,
                hash_error=hash_error,
            )
            if status in {"size_changed", "hash_mismatch", "incomplete"}:
                failures.append({"path": qpath, "reason": reason})
                if status == "incomplete":
                    hash_incomplete += 1
                continue
            if status == "ok_hash":
                hash_verified += 1
            elif status == "size_only":
                hash_unavailable += 1
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
            "hash_verified": hash_verified,
            "hash_unavailable": hash_unavailable,
            "hash_incomplete": hash_incomplete,
            "free_delta": free_delta,
        },
        "failures": failures,
        "free_before": execution.get("free_before"),
        "free_after_execution": execution.get("free_after"),
        "free_now": free_now,
        "note": (
            "purge 已执行；可用空间变化受系统写入与日志影响，负值不一定是清理失败。"
            if execution.get("purge")
            else "隔离模式在 purge 前不会释放空间；负的 free_delta 可能是系统写入/日志造成。"
        ),
    }
    out_dir = state.dir("execution")
    atomic_write_json(out_dir / "verification.json", verification)
    (out_dir / "report-verify.md").write_text(verification_markdown(verification), encoding="utf-8")
    state.set_file("verification", out_dir / "verification.json")
    state.set_file("verification_report", out_dir / "report-verify.md")
    execution["verification_path"] = str(out_dir / "verification.json")
    execution["verification_summary"] = verification["summary"]
    atomic_write_json(execution_path, execution)
    _write_cleanup_html(state, execution, verification)
    return verification


def purge(state: State, transport: Transport, token: str) -> dict[str, Any]:
    execution, execution_path = load_execution(state)
    if execution.get("mode") != "quarantine":
        raise SafetyError("只有隔离模式可以 purge")
    expected = str(execution.get("purge_token") or "")
    if not token or not expected or not hmac.compare_digest(str(token), expected):
        raise SafetyError("purge 令牌不正确")
    journal_path = execution.get("journal_path") or ""
    if not journal_path or not Path(journal_path).exists():
        raise ValidationError("journal.jsonl 缺失，无法 purge")
    entries: list[dict[str, Any]] = []
    with open(journal_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    all_roots = _quarantine_roots(execution)
    hash_targets: list[str] = []
    for entry in entries:
        if entry.get("event") != "quarantined":
            continue
        if entry.get("purge_status") == "purged" or entry.get("restore_status") == "restored":
            continue
        if not entry.get("backup_sha256"):
            continue
        try:
            qpath = normalize_device_path(str(entry.get("quarantine_path") or ""))
        except Exception:  # noqa: BLE001 - rejected by the purge pass below
            continue
        if any(is_under(qpath, root) for root in all_roots):
            hash_targets.append(qpath)
    hash_tool, quarantine_hashes, hash_error = _hash_quarantine_files(transport, hash_targets)
    hash_verified = 0
    hash_unavailable = 0
    hash_incomplete = 0
    purged = 0
    purged_bytes = 0
    missing = 0
    failures: list[dict[str, str]] = []
    updated: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("event") != "quarantined":
            updated.append(entry)
            continue
        if entry.get("purge_status") == "purged":
            # Already permanently deleted by an earlier purge.  Keep the
            # original record untouched so the history stays truthful.
            updated.append(entry)
            continue
        if entry.get("restore_status") == "restored":
            # The file was moved back to its original path; it is no longer
            # in quarantine and must never be re-labelled as "missing".
            updated.append(entry)
            continue
        qpath = str(entry.get("quarantine_path") or "")
        try:
            qpath = normalize_device_path(qpath)
        except Exception as exc:  # noqa: BLE001
            failures.append({"path": str(entry.get("quarantine_path") or ""), "reason": f"记录中的路径非法：{exc}"})
            updated.append(entry)
            continue
        if not any(is_under(qpath, root) for root in all_roots):
            failures.append({"path": qpath, "reason": "隔离路径不在本次隔离区内，拒绝"})
            updated.append(entry)
            continue
        current = transport.stat(qpath)
        if current is None:
            entry["purge_status"] = "missing"
            missing += 1
            updated.append(entry)
            continue
        if current.kind != "file":
            failures.append({"path": qpath, "reason": "隔离目标不是普通文件"})
            updated.append(entry)
            continue
        status, reason = _quarantine_integrity(
            entry,
            current,
            quarantine_hashes.get(qpath),
            hash_tool=hash_tool,
            hash_error=hash_error,
        )
        if status in {"size_changed", "hash_mismatch", "incomplete"}:
            failures.append({"path": qpath, "reason": reason})
            if status == "incomplete":
                hash_incomplete += 1
            updated.append(entry)
            continue
        if status == "ok_hash":
            hash_verified += 1
        elif status == "size_only":
            hash_unavailable += 1
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
    atomic_write_text(
        journal_path,
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in updated),
    )
    free_after = {}
    try:
        free_after = transport.df("/sdcard")
    except Exception:  # noqa: BLE001
        pass
    before = execution.get("free_after") or {}
    free_delta = 0
    if isinstance(free_after, dict) and isinstance(before, dict):
        free_delta = int(free_after.get("available_bytes") or 0) - int(before.get("available_bytes") or 0)
    totals = _journal_totals(updated)
    remaining = _quarantine_remaining(updated)
    run_at = now_iso()
    run = {
        "at": run_at,
        "purged_files": purged,
        "purged_bytes": purged_bytes,
        "missing_files": missing,
        "failed": len(failures),
        "failures": failures,
        "free_delta": free_delta,
        "free_after": free_after,
        "hash_verified_files": hash_verified,
        "hash_unavailable_files": hash_unavailable,
        "hash_incomplete_files": hash_incomplete,
    }
    runs = _previous_runs(
        execution.get("purge"),
        files_key="purged_files",
        bytes_key="purged_bytes",
        fallback_at=str(execution.get("purged_at") or ""),
    )
    runs.append(run)
    hash_verified_total = sum(int(item.get("hash_verified_files") or 0) for item in runs)
    hash_unavailable_total = sum(int(item.get("hash_unavailable_files") or 0) for item in runs)
    hash_incomplete_total = sum(int(item.get("hash_incomplete_files") or 0) for item in runs)
    execution["purged_at"] = run_at
    execution["purge"] = {
        # Cumulative truth from the journal: repeating purge must not erase
        # what earlier runs actually deleted.
        "purged_files": totals["purged_files"],
        "purged_bytes": totals["purged_bytes"],
        "missing_files": totals["missing_files"],
        "failed": run["failed"],
        "failures": run["failures"],
        "free_delta": run["free_delta"],
        "free_after": run["free_after"],
        "hash_verified_files": hash_verified_total,
        "hash_unavailable_files": hash_unavailable_total,
        "hash_incomplete_files": hash_incomplete_total,
        "last_run": run,
        "runs": runs,
    }
    execution["quarantine_state"] = {**remaining, "updated_at": run_at}
    execution["status"] = "purged" if not failures else "partial"
    atomic_write_json(execution_path, execution)
    out_dir = state.dir("execution")
    lines = [
        "# 隔离区清理报告（purge）",
        "",
        f"- 本次永久删除：{purged} 个 / {human_bytes(purged_bytes)}",
        f"- 累计永久删除：{totals['purged_files']} 个 / {human_bytes(totals['purged_bytes'])}",
        f"- 隔离区剩余：{remaining['remaining_files']} 个 / {human_bytes(remaining['remaining_bytes'])}",
        f"- 本次失败：{len(failures)}",
        f"- 可用空间变化：{human_bytes(free_delta)}",
    ]
    if hash_verified or hash_unavailable or hash_incomplete:
        lines.append(
            f"- 内容校验：{hash_verified} 个通过；{hash_unavailable} 个仅校验大小；"
            f"{hash_incomplete} 个未完成"
        )
    lines.append("")
    if missing:
        lines.append(f"- 本次有 {missing} 个隔离文件在执行前已不存在，标记为 missing。")
        lines.append("")
    for failure in failures[:100]:
        lines.append(f"- {md_code(failure.get('path', ''))}：{failure.get('reason', '')}")
    (out_dir / "report-purge.md").write_text("\n".join(lines), encoding="utf-8")
    _write_cleanup_html(state, execution)
    return {
        **run,
        "cumulative": {
            "purged_files": totals["purged_files"],
            "purged_bytes": totals["purged_bytes"],
            "missing_files": totals["missing_files"],
            "hash_verified_files": hash_verified_total,
            "hash_unavailable_files": hash_unavailable_total,
            "hash_incomplete_files": hash_incomplete_total,
        },
        "quarantine_remaining": remaining,
        "runs": len(runs),
    }


def restore(
    state: State,
    transport: Transport,
    ids: list[str] | None,
    restore_all: bool,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    execution, execution_path = load_execution(state)
    if execution.get("mode") != "quarantine":
        raise SafetyError("只有隔离模式可以 restore")
    if config is None:
        from .config import load_config

        config = load_config(None)
    journal_path = execution.get("journal_path") or ""
    if not journal_path or not Path(journal_path).exists():
        raise ValidationError("journal.jsonl 缺失，无法恢复")
    roots = _quarantine_roots(execution)
    item_categories = {
        str(item.get("id")): str(item.get("category") or "")
        for item in execution.get("items") or []
        if item.get("id")
    }
    platform = str(execution.get("platform") or "android")
    packages = _load_restore_packages(state)
    selected = set(ids or [])
    entries: list[dict[str, Any]] = []
    restored = 0
    restored_bytes = 0
    failures: list[dict[str, str]] = []
    hash_targets: list[str] = []
    with open(journal_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("event") != "quarantined":
                continue
            if entry.get("purge_status") == "purged" or entry.get("restore_status") == "restored":
                continue
            if not restore_all and entry.get("item") not in selected:
                continue
            if not entry.get("backup_sha256"):
                continue
            try:
                qpath = normalize_device_path(str(entry.get("quarantine_path") or ""))
            except Exception:  # noqa: BLE001 - rejected by the restore pass below
                continue
            if any(is_under(qpath, root) for root in roots):
                hash_targets.append(qpath)
    hash_tool, quarantine_hashes, hash_error = _hash_quarantine_files(transport, hash_targets)
    hash_verified = 0
    hash_unavailable = 0
    hash_incomplete = 0
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
            raw_source = entry.get("path", "")
            raw_qpath = entry.get("quarantine_path", "")
            if not raw_source or not raw_qpath:
                failures.append({"path": str(raw_source), "reason": "记录缺失"})
                entries.append(entry)
                continue
            try:
                source = normalize_device_path(str(raw_source))
                qpath = normalize_device_path(str(raw_qpath))
            except Exception as exc:  # noqa: BLE001
                failures.append({"path": str(raw_source), "reason": f"记录中的路径非法：{exc}"})
                entries.append(entry)
                continue
            if not any(is_under(qpath, root) for root in roots):
                failures.append({"path": qpath, "reason": "隔离路径不在本次隔离区内，拒绝恢复"})
                entries.append(entry)
                continue
            category = item_categories.get(str(item_id), "")
            if not category:
                failures.append({"path": source, "reason": "无法从执行记录确定候选类别，拒绝恢复"})
                entries.append(entry)
                continue
            try:
                guard = guard_path(
                    source,
                    category,
                    kind="file",
                    packages=packages,
                    config=config,
                    platform=platform,
                )
            except Exception as exc:  # noqa: BLE001 - guard_path must fail closed
                failures.append({"path": source, "reason": f"恢复目标路径校验失败：{exc}"})
                entries.append(entry)
                continue
            if not guard.get("allowed"):
                failures.append({
                    "path": source,
                    "reason": f"恢复目标不符合安全路径规则：{guard.get('reason') or '被拒绝'}",
                })
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
            status, reason = _quarantine_integrity(
                entry,
                qstat,
                quarantine_hashes.get(qpath),
                hash_tool=hash_tool,
                hash_error=hash_error,
            )
            if status in {"size_changed", "hash_mismatch", "incomplete"}:
                failures.append({"path": qpath, "reason": reason})
                if status == "incomplete":
                    hash_incomplete += 1
                entries.append(entry)
                continue
            if status == "ok_hash":
                hash_verified += 1
            elif status == "size_only":
                hash_unavailable += 1
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
    atomic_write_text(
        journal_path,
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
    )
    totals = _journal_totals(entries)
    remaining = _quarantine_remaining(entries)
    run_at = now_iso()
    run = {
        "at": run_at,
        "restored_files": restored,
        "restored_bytes": restored_bytes,
        "failed": len(failures),
        "failures": failures,
        "hash_verified_files": hash_verified,
        "hash_unavailable_files": hash_unavailable,
        "hash_incomplete_files": hash_incomplete,
    }
    runs = _previous_runs(
        execution.get("restore"),
        files_key="restored_files",
        bytes_key="restored_bytes",
        fallback_at=str(execution.get("restored_at") or ""),
    )
    runs.append(run)
    hash_verified_total = sum(int(item.get("hash_verified_files") or 0) for item in runs)
    hash_unavailable_total = sum(int(item.get("hash_unavailable_files") or 0) for item in runs)
    hash_incomplete_total = sum(int(item.get("hash_incomplete_files") or 0) for item in runs)
    execution["restored_at"] = run_at
    execution["restore"] = {
        # Cumulative truth from the journal: restoring in several batches must
        # not make the report forget the earlier batches.
        "restored_files": totals["restored_files"],
        "restored_bytes": totals["restored_bytes"],
        "failed": run["failed"],
        "failures": run["failures"],
        "hash_verified_files": hash_verified_total,
        "hash_unavailable_files": hash_unavailable_total,
        "hash_incomplete_files": hash_incomplete_total,
        "last_run": run,
        "runs": runs,
    }
    execution["quarantine_state"] = {**remaining, "updated_at": run_at}
    atomic_write_json(execution_path, execution)
    out_dir = state.dir("execution")
    lines = [
        "# 隔离区恢复报告",
        "",
        f"- 本次恢复：{restored} 个 / {human_bytes(restored_bytes)}",
        f"- 累计恢复：{totals['restored_files']} 个 / {human_bytes(totals['restored_bytes'])}",
        f"- 隔离区剩余：{remaining['remaining_files']} 个 / {human_bytes(remaining['remaining_bytes'])}",
        f"- 本次失败：{len(failures)}",
    ]
    if hash_verified or hash_unavailable or hash_incomplete:
        lines.append(
            f"- 内容校验：{hash_verified} 个通过；{hash_unavailable} 个仅校验大小；"
            f"{hash_incomplete} 个未完成"
        )
    lines.append("")
    for failure in failures[:100]:
        lines.append(f"- {md_code(failure.get('path', ''))}：{failure.get('reason', '')}")
    (out_dir / "report-restore.md").write_text("\n".join(lines), encoding="utf-8")
    _write_cleanup_html(state, execution)
    return {
        **run,
        "cumulative": {
            "restored_files": totals["restored_files"],
            "restored_bytes": totals["restored_bytes"],
            "hash_verified_files": hash_verified_total,
            "hash_unavailable_files": hash_unavailable_total,
            "hash_incomplete_files": hash_incomplete_total,
        },
        "quarantine_remaining": remaining,
        "runs": len(runs),
    }
