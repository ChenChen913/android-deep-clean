"""Read-only app-level storage auxiliary source (partial).

Collects the base APK size of installed packages with ``stat``.  It does not
read app-private data/cache: on the tested Android 10 shell those require
StorageStatsManager permissions or root.  ``dumpsys diskstats`` is deliberately
not used because it writes a system cache file, which would break the read-only
scan contract.

External app data/media/obb sizes are computed on the host from the existing
filesystem snapshot by ``app_storage_index``.
"""

from __future__ import annotations

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
        discovered, discovery_errors = self._discover_apk_paths(selected)
        entries: list[dict[str, Any]] = []
        paths: list[str] = []
        for package in selected:
            apk_path = str(self.packages.apk_paths.get(package) or discovered.get(package) or "")
            if not apk_path:
                entries.append({
                    "package": package, "apk_path": "", "apk_bytes": None,
                    "status": "no_path",
                })
                continue
            entries.append({"package": package, "apk_path": apk_path, "apk_bytes": None, "status": "missing"})
            paths.append(apk_path)
        sizes, errors = self._stat_paths(paths)
        errors = discovery_errors + errors
        for entry in entries:
            path = entry.get("apk_path") or ""
            if path and path in sizes:
                entry["apk_bytes"] = sizes[path]
                entry["status"] = "ok"
        known = sum(int(entry.get("apk_bytes") or 0) for entry in entries)
        captured = sum(1 for entry in entries if entry.get("apk_bytes") is not None)
        missing = sum(1 for entry in entries if entry.get("status") != "ok")
        limitations = [
            "仅统计已安装应用的基础 APK；split APK 未计入，APK 大小不是应用总占用。",
            "应用内部 data/cache 未采集：shell 缺少 StorageStatsManager 权限；本工具不使用会写系统缓存的 dumpsys diskstats。",
            "外部 data/media/obb 由文件扫描快照统计，见 analyze 的 app_storage 区块。",
        ]
        if missing:
            limitations.append(f"{missing} 个应用缺少可用 APK 大小（路径缺失或 stat 失败），其应用自身大小未知。")
        if truncated:
            limitations.append(f"应用数量超过上限 {self.max_packages}，仅采集前 {len(selected)} 个。")
        if errors:
            limitations.append("部分 APK stat 失败：" + "；".join(errors[-2:]))
        if not captured:
            return self._write("unavailable", "所有 APK 大小均无法读取（可能被系统权限限制）", entries,
                              limitations=limitations, truncated=truncated)
        status = "partial" if missing or truncated or errors else "collected"
        reason = "部分应用 APK 大小不可用" if status == "partial" else ""
        return self._write(status, reason, entries, limitations=limitations, truncated=truncated)

    def _discover_apk_paths(self, packages: list[str]) -> tuple[dict[str, str], list[str]]:
        missing = [package for package in packages if not self.packages.apk_paths.get(package)]
        if not missing:
            return {}, []
        try:
            rc, output, error = self.transport.shell_rc(
                "pm list packages -f", timeout=max(10, self.timeout)
            )
        except TransportError as exc:
            return {}, [f"无法列出 APK 路径：{exc}"[:200]]
        if rc != 0:
            message = (error or output).strip().splitlines()
            return {}, [(message[0] if message else f"pm list packages -f 退出码 {rc}")[:200]]
        discovered: dict[str, str] = {}
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            path, separator, package = line[len("package:"):].rpartition("=")
            if separator and path and package:
                discovered[package] = path
        return discovered, []

    def _stat_paths(self, paths: list[str]) -> tuple[dict[str, int], list[str]]:
        sizes: dict[str, int] = {}
        errors: list[str] = []
        chunks = [paths[index:index + CHUNK_SIZE] for index in range(0, len(paths), CHUNK_SIZE)]
        for chunk in chunks:
            command = "stat -c '%n|%s' " + " ".join(shell_quote(path) for path in chunk)
            try:
                rc, output, error = self.transport.shell_rc(command, timeout=self.timeout)
            except TransportError as exc:
                errors.append(str(exc)[:200])
                continue
            for line in output.splitlines():
                line = line.strip()
                if "|" not in line:
                    continue
                name, _, size = line.rpartition("|")
                try:
                    sizes[name] = int(size)
                except ValueError:
                    continue
            if rc != 0:
                message = (error or output).strip().splitlines()
                errors.append((message[0] if message else f"stat 退出码 {rc}")[:200])
        return sizes, errors

    def _write(
        self,
        status: str,
        reason: str,
        entries: list[dict[str, Any]],
        *,
        limitations: list[str] | None = None,
        truncated: bool = False,
    ) -> dict[str, Any]:
        captured = [entry for entry in entries if entry.get("apk_bytes") is not None]
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
            "truncated": bool(truncated),
            "limitations": list(limitations or []),
            "manifest_path": str(self.manifest_path),
        }
        atomic_write_json(self.manifest_path, manifest)
        return manifest
