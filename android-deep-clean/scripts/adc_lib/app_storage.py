"""Read-only app-level storage auxiliary source (partial).

Collects installed-package APK metadata and, when the transport actually has
root/su, a bounded read-only ``du`` summary of ``/data/data``.  It never uses
``pm clear``, ``dumpsys diskstats`` or any command that writes device state.
External app data/media/obb sizes are computed on the host from the existing
filesystem snapshot by ``app_storage_index``.

Capability boundaries are explicit: missing permissions, missing ``pm``/``du``
tools, split-APK query budgets and stat failures are reported as
``unavailable`` / ``partial`` / ``permission_denied``, never silently 0 MB.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .models import PackageIndex
from .platform import HARMONY_NEXT
from .state import State
from .transport import Transport, TransportError
from .util import atomic_write_json, now_iso, shell_quote

CHUNK_SIZE = 60


class AppStorageCollector:
    def __init__(
        self,
        transport: Transport,
        config: dict[str, Any],
        state: State,
        doctor: dict[str, Any],
        packages: PackageIndex | None,
    ) -> None:
        self.transport = transport
        self.config = config
        self.state = state
        self.doctor = doctor
        self.packages = packages
        self.settings = config.get("app_storage") or {}
        self.platform = str((doctor.get("capabilities") or {}).get("platform") or "android")
        try:
            self.sdk = int((doctor.get("device") or {}).get("sdk") or 0)
        except (TypeError, ValueError):
            self.sdk = 0
        self.timeout = max(5, int(self.settings.get("timeout_seconds") or 30))
        self.max_packages = max(0, int(self.settings.get("max_packages") or 1000))
        self.split_max_packages = max(0, int(self.settings.get("split_apk_max_packages") or 120))
        self.internal_max_packages = max(0, int(self.settings.get("internal_max_packages") or 60))
        self.aux_dir = state.dir("scan") / "aux"
        self.manifest_path = self.aux_dir / "app-storage.json"

    def collect(self, *, enabled: bool = True) -> dict[str, Any]:
        self.aux_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.manifest_path.unlink()
        except FileNotFoundError:
            pass
        if not enabled or not bool(self.settings.get("enabled", True)):
            return self._write("disabled", "本轮扫描已关闭应用级存储采集（--no-app-storage 或配置关闭）", [])
        if self.platform == HARMONY_NEXT:
            return self._write("unsupported", "HarmonyOS NEXT 不使用 Android pm/APK 路径；未采集应用级存储", [])
        if self.packages is None or not self.packages.available:
            return self._write("unavailable", "包管理器不可用，无法可靠列出应用 APK", [])
        installed = sorted(self.packages.installed)
        if not installed:
            return self._write("unavailable", "未获取到已安装应用列表", [])
        selected = installed[: self.max_packages] if self.max_packages else []
        truncated = len(installed) > len(selected)

        apk_paths, discovery_errors, split_status = self._discover_apk_paths(selected)
        all_paths: list[str] = []
        for paths in apk_paths.values():
            for path in paths:
                if path not in all_paths:
                    all_paths.append(path)
        stat_results, stat_errors = self._stat_paths(all_paths)
        entries: list[dict[str, Any]] = []
        for package in selected:
            paths = apk_paths.get(package) or []
            files = [
                {"path": path, **(stat_results.get(path) or {"size": None, "status": "missing", "error": ""})}
                for path in paths
            ]
            entries.append(self._apk_entry(package, files))

        internal, capability = self._collect_internal(selected)
        internal_by_package = {
            str(item.get("package")): item for item in internal.get("packages") or []
        }
        for entry in entries:
            item = internal_by_package.get(str(entry.get("package"))) or {}
            entry.update({
                "internal_data_bytes": item.get("internal_data_bytes"),
                "internal_cache_bytes": item.get("internal_cache_bytes"),
                "internal_code_cache_bytes": item.get("internal_code_cache_bytes"),
                "internal_status": item.get("internal_status") or "unavailable",
                "internal_error": item.get("error") or "",
            })

        apk_captured = [entry for entry in entries if entry.get("apk_bytes") is not None]
        apk_status = self._apk_status(entries, split_status, truncated, discovery_errors + stat_errors)
        internal_status = str(internal.get("status") or "unavailable")
        if not apk_captured and internal_status == "unavailable":
            status = "unavailable"
            reason = "APK 与内部 data/cache 均不可用"
        elif apk_status != "collected" or internal_status == "partial" or truncated:
            status = "partial"
            reason = "部分应用存储数据不可用"
        else:
            status = "collected"
            reason = ""

        limitations = self._limitations(
            entries, internal, capability, split_status, truncated,
            discovery_errors + stat_errors,
        )
        return self._write(
            status, reason, entries,
            limitations=limitations,
            truncated=truncated,
            apk_status=apk_status,
            split_status=split_status,
            internal=internal,
            capability=capability,
        )

    # ---------------------------------------------------------------- APK
    def _discover_apk_paths(self, packages: list[str]) -> tuple[dict[str, list[str]], list[str], str]:
        """Discover base and split APK paths without unbounded ``pm path`` calls."""
        paths: dict[str, list[str]] = {}
        for package in packages:
            known = self.packages.apk_path_list.get(package) if self.packages else []
            if known:
                paths[package] = list(known)
            elif self.packages and self.packages.apk_paths.get(package):
                paths[package] = [self.packages.apk_paths[package]]
        errors: list[str] = []
        missing = [package for package in packages if not paths.get(package)]
        if missing:
            try:
                rc, output, error = self.transport.shell_rc(
                    "pm list packages -f", timeout=max(10, self.timeout)
                )
            except TransportError as exc:
                errors.append(f"无法列出 APK 路径：{exc}"[:200])
            else:
                if rc == 0:
                    for line in output.splitlines():
                        line = line.strip()
                        if not line.startswith("package:"):
                            continue
                        path, separator, package = line[len("package:"):].rpartition("=")
                        if separator and path and package in missing:
                            paths[package] = [path]
                else:
                    message = (error or output).strip().splitlines()
                    errors.append((message[0] if message else f"pm list packages -f 退出码 {rc}")[:200])

        if self.split_max_packages <= 0:
            return paths, errors, "disabled"
        # Packages without a path first, then the rest up to the budget.  The
        # budget keeps a 1000-package device from issuing 1000 pm calls.
        candidates = [package for package in packages if not paths.get(package)]
        candidates += [package for package in packages if paths.get(package)]
        checked = 0
        split_status = "collected"
        for package in candidates:
            if checked >= self.split_max_packages:
                split_status = "partial"
                break
            checked += 1
            try:
                rc, output, error = self.transport.shell_rc(
                    f"pm path {shell_quote(package)}", timeout=max(10, self.timeout)
                )
            except TransportError as exc:
                errors.append(f"{package}: pm path 不可用：{exc}"[:200])
                continue
            if rc != 0:
                message = (error or output).strip().splitlines()
                errors.append(f"{package}: {(message[0] if message else f'pm path 退出码 {rc}')}"[:200])
                continue
            found: list[str] = []
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("package:"):
                    path = line[len("package:"):].strip()
                    if path and path not in found:
                        found.append(path)
            if found:
                paths[package] = found
        if split_status == "partial":
            errors.append(f"split APK 查询达到上限 {self.split_max_packages} 个应用，其余应用只统计基础 APK")
        return paths, errors, split_status

    def _stat_paths(self, paths: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
        results: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        chunks = [paths[index:index + CHUNK_SIZE] for index in range(0, len(paths), CHUNK_SIZE)]
        for chunk in chunks:
            command = "stat -c '%n|%s' " + " ".join(shell_quote(path) for path in chunk)
            try:
                rc, output, error = self.transport.shell_rc(command, timeout=self.timeout)
            except TransportError as exc:
                for path in chunk:
                    results[path] = {"size": None, "status": "stat_failed", "error": str(exc)[:200]}
                errors.append(str(exc)[:200])
                continue
            for line in output.splitlines():
                line = line.strip()
                if "|" not in line:
                    continue
                name, _, size = line.rpartition("|")
                try:
                    results[name] = {"size": int(size), "status": "ok", "error": ""}
                except ValueError:
                    continue
            stderr = (error or "").lower()
            for path in chunk:
                if path in results:
                    continue
                if "permission denied" in stderr:
                    status = "permission_denied"
                elif "no such file" in stderr or "not found" in stderr:
                    status = "missing"
                elif rc != 0:
                    status = "stat_failed"
                else:
                    status = "missing"
                results[path] = {"size": None, "status": status, "error": (error or "").strip()[:200]}
            if rc != 0:
                message = (error or output).strip().splitlines()
                errors.append((message[0] if message else f"stat 退出码 {rc}")[:200])
        return results, errors

    @staticmethod
    def _apk_entry(package: str, files: list[dict[str, Any]]) -> dict[str, Any]:
        ok = [item for item in files if item.get("status") == "ok"]
        total = sum(int(item.get("size") or 0) for item in ok) if ok else None
        base = next(
            (int(item.get("size") or 0) for item in ok if str(item.get("path", "")).lower().endswith("/base.apk")),
            None,
        )
        if base is None and ok:
            base = int(ok[0].get("size") or 0)
        split = (total - base) if total is not None and base is not None else None
        if not files:
            status = "no_path"
        elif not ok:
            statuses = {str(item.get("status") or "") for item in files}
            if "permission_denied" in statuses:
                status = "permission_denied"
            elif "missing" in statuses:
                status = "missing"
            else:
                status = "stat_failed"
        elif len(ok) < len(files):
            status = "partial"
        else:
            status = "ok"
        return {
            "package": package,
            "apk_path": str(files[0].get("path") or "") if files else "",
            "apk_paths": [str(item.get("path") or "") for item in files if item.get("path")],
            "apk_files": files,
            "apk_bytes": total,
            "apk_base_bytes": base,
            "apk_split_bytes": split,
            "apk_file_count": len(ok),
            "status": status,
        }

    @staticmethod
    def _apk_status(
        entries: list[dict[str, Any]],
        split_status: str,
        truncated: bool,
        errors: list[str],
    ) -> str:
        if not any(entry.get("apk_bytes") is not None for entry in entries):
            return "unavailable"
        if (
            split_status == "partial"
            or truncated
            or errors
            or any(entry.get("status") != "ok" for entry in entries)
        ):
            return "partial"
        return "collected"

    # ---------------------------------------------------------------- internal
    def _collect_internal(self, packages: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
        privilege = str(getattr(self.transport, "privilege", "shell") or "shell")
        root_ok = privilege in {"root", "su"}
        du_ok = self._command_exists("du")
        capability = {
            "privilege": privilege,
            "root": root_ok,
            "du": du_ok,
            "storage_stats": "unavailable",
            "storage_stats_reason": (
                "StorageStatsManager 需要应用进程权限；shell 不能直接调用。"
                "未使用 dumpsys diskstats（会写系统缓存，违反只读扫描）。"
            ),
        }
        if not root_ok:
            return {
                "status": "unavailable",
                "reason": "需要 root/su；普通 shell 无 StorageStatsManager 权限",
                "packages": [],
                "selected": 0,
            }, capability
        if not du_ok:
            return {
                "status": "unavailable",
                "reason": "设备缺少 du，无法只读统计 /data/data",
                "packages": [],
                "selected": 0,
            }, capability
        selected = self._prioritize_internal(packages, self.internal_max_packages)
        results: list[dict[str, Any]] = []
        for package in selected:
            base = f"/data/data/{package}"
            command = (
                f"du -sk {shell_quote(base)} {shell_quote(base + '/cache')} "
                f"{shell_quote(base + '/code_cache')} 2>&1"
            )
            try:
                _, output, _ = self.transport.shell_rc(command, timeout=self.timeout)
            except TransportError as exc:
                results.append({
                    "package": package,
                    "internal_data_bytes": None,
                    "internal_cache_bytes": None,
                    "internal_code_cache_bytes": None,
                    "internal_status": "unavailable",
                    "error": str(exc)[:200],
                })
                continue
            parsed: dict[str, int] = {}
            permission = False
            for line in output.splitlines():
                text = line.strip()
                if "permission denied" in text.lower():
                    permission = True
                    continue
                parts = text.split(None, 1)
                if len(parts) == 2 and parts[0].isdigit():
                    parsed[parts[1]] = int(parts[0]) * 1024
            total = parsed.get(base)
            if total is None:
                results.append({
                    "package": package,
                    "internal_data_bytes": None,
                    "internal_cache_bytes": None,
                    "internal_code_cache_bytes": None,
                    "internal_status": "permission_denied" if permission else "unavailable",
                    "error": "Permission denied" if permission else "du 未返回该应用的数据",
                })
                continue
            results.append({
                "package": package,
                "internal_data_bytes": total,
                "internal_cache_bytes": parsed.get(base + "/cache") or 0,
                "internal_code_cache_bytes": parsed.get(base + "/code_cache") or 0,
                "internal_status": "collected",
                "error": "",
            })
        collected = sum(1 for item in results if item.get("internal_status") == "collected")
        if not results:
            status = "unavailable"
            reason = "没有可统计的内部数据应用"
        elif collected == len(results):
            status = "collected"
            reason = ""
        elif collected:
            status = "partial"
            reason = "部分应用内部数据不可读"
        elif any(item.get("internal_status") == "permission_denied" for item in results):
            status = "permission_denied"
            reason = "root/su 下仍无法读取 /data/data"
        else:
            status = "unavailable"
            reason = "内部数据统计不可用"
        return {
            "status": status,
            "reason": reason,
            "packages": results,
            "selected": len(selected),
        }, capability

    def _prioritize_internal(self, installed: list[str], limit: int) -> list[str]:
        if limit <= 0:
            return []
        ranked: list[str] = []
        db_path = self.state.dir("scan") / "snapshot.db"
        if db_path.exists():
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    for row in conn.execute(
                        "SELECT path FROM dir_stats WHERE parent='/sdcard/Android/data' "
                        "ORDER BY subtree_bytes DESC"
                    ):
                        package = str(row[0] or "").rsplit("/", 1)[-1]
                        if package in installed and package not in ranked:
                            ranked.append(package)
                finally:
                    conn.close()
            except sqlite3.Error:
                pass
        for package in installed:
            if package not in ranked:
                ranked.append(package)
        return ranked[:limit]

    def _command_exists(self, name: str) -> bool:
        try:
            rc, output, _ = self.transport.shell_rc(
                f"command -v {name} >/dev/null 2>&1; echo $?", timeout=10
            )
        except TransportError:
            return False
        return rc == 0 and output.strip() == "0"

    # ---------------------------------------------------------------- output
    def _limitations(
        self,
        entries: list[dict[str, Any]],
        internal: dict[str, Any],
        capability: dict[str, Any],
        split_status: str,
        truncated: bool,
        errors: list[str],
    ) -> list[str]:
        limitations = [
            "APK 大小是安装包体积，不是应用总占用；split APK 查询受应用数量上限和系统权限限制。",
            "外部 data/media/obb 由文件扫描快照统计，已经包含在文件扫描里。",
            "StorageStatsManager 未使用：需要应用进程权限；本工具不使用会写系统缓存的 dumpsys diskstats。",
        ]
        internal_status = str(internal.get("status") or "")
        if internal_status == "unavailable":
            limitations.append("应用内部 data/cache 不可读；普通 shell 没有 StorageStatsManager 权限或 Root。")
        elif internal_status in {"collected", "partial", "permission_denied"}:
            limitations.append(
                "内部 data 由只读 du 统计，包含 cache/code_cache；它们是子集，不能与外部缓存重复相加。"
            )
        if split_status == "partial":
            limitations.append(f"split APK 只查询了前 {self.split_max_packages} 个应用，其余只统计基础 APK。")
        elif split_status == "disabled":
            limitations.append("split APK 查询已关闭，只统计基础 APK。")
        if truncated:
            limitations.append(f"应用数量超过上限 {self.max_packages}，仅采集前 {len(entries)} 个。")
        missing = sum(1 for entry in entries if entry.get("status") != "ok")
        if missing:
            limitations.append(f"{missing} 个应用缺少完整 APK 大小（路径缺失、权限或 stat 失败），其应用自身大小未知。")
        if errors:
            limitations.append("部分 APK 查询/stat 失败：" + "；".join(errors[-2:]))
        return limitations

    def _write(
        self,
        status: str,
        reason: str,
        entries: list[dict[str, Any]],
        *,
        limitations: list[str] | None = None,
        truncated: bool = False,
        apk_status: str = "",
        split_status: str = "",
        internal: dict[str, Any] | None = None,
        capability: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        captured = [entry for entry in entries if entry.get("apk_bytes") is not None]
        internal = internal or {}
        apk_base = sum(int(entry.get("apk_base_bytes") or 0) for entry in captured)
        apk_split = sum(int(entry.get("apk_split_bytes") or 0) for entry in captured)
        status_counts: dict[str, int] = {}
        for entry in entries:
            key = str(entry.get("status") or "unknown")
            status_counts[key] = status_counts.get(key, 0) + 1
        manifest = {
            "schema": 1,
            "generated_at": now_iso(),
            "status": status,
            "available": status in {"collected", "partial"},
            "reason": reason,
            "platform": self.platform,
            "sdk": self.sdk,
            "transport": self.transport.name,
            "installed": len(self.packages.installed) if self.packages and self.packages.available else 0,
            "packages": entries,
            "items": len(captured),
            "bytes": sum(int(entry.get("apk_bytes") or 0) for entry in captured),
            "apk": {
                "status": apk_status or status,
                "base_bytes": apk_base,
                "split_bytes": apk_split,
                "file_count": sum(int(entry.get("apk_file_count") or 0) for entry in captured),
                "split_status": split_status,
                "status_counts": status_counts,
            },
            "internal": {
                "status": str(internal.get("status") or "unavailable"),
                "reason": str(internal.get("reason") or ""),
                "selected": int(internal.get("selected") or 0),
                "packages": internal.get("packages") or [],
                "data_bytes": sum(int(item.get("internal_data_bytes") or 0) for item in internal.get("packages") or []),
                "cache_bytes": sum(int(item.get("internal_cache_bytes") or 0) for item in internal.get("packages") or []),
            },
            "capabilities": capability or {},
            "truncated": bool(truncated),
            "limitations": list(limitations or []),
            "manifest_path": str(self.manifest_path),
        }
        atomic_write_json(self.manifest_path, manifest)
        return manifest
