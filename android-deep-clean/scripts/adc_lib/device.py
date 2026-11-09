"""Device/environment probing (read-only)."""

from __future__ import annotations

import re
from typing import Any

from .models import AccessProbe, DeviceInfo, PackageIndex
from .platform import (
    ANDROID,
    HARMONY_COMPAT,
    HARMONY_NEXT,
    HARMONY_PHOTO_ROOT,
    HARMONY_PUBLIC_ROOT,
    HARMONY_TMP_ROOT,
    detect_platform,
    harmony_photo_root,
    platform_identity_extra,
    profile_for,
)
from .transport import Transport, TransportError
from .util import human_bytes, now_iso, sha256_text, stable_id

PROBE_PATHS = [
    "/sdcard",
    "/sdcard/Android/data",
    "/sdcard/Android/obb",
    "/sdcard/Android/media",
    "/sdcard/DCIM",
    "/sdcard/Download",
    "/sdcard/Documents",
    "/sdcard/.android-deep-clean",
    "/data/data",
    "/data/local/tmp",
]

PROBE_COMMANDS = ["find", "stat", "xargs", "sha256sum", "md5sum", "df", "pm", "realpath", "rm", "mv", "rmdir", "su"]


def device_identity(info: DeviceInfo) -> dict[str, Any]:
    """Stable identity used to bind scan/plan/selection/execution to one device.

    Serial is the primary key when available; model/fingerprint are included as
    secondary evidence.  The short id is safe to display in reports.
    """
    serial = str(info.serial or "").strip() or "unknown"
    fingerprint = str(info.fingerprint or "").strip()
    platform = str(getattr(info, "platform", "") or ANDROID)
    material_parts = [serial, fingerprint, str(info.manufacturer or ""), str(info.model or ""), str(info.sdk or 0)]
    if platform != ANDROID:
        material_parts += [platform, str(getattr(info, "os_version", "") or ""), str(getattr(info, "api_version", "") or "")]
    material = "|".join(material_parts)
    return {
        "id": stable_id("device", material),
        "serial": serial,
        "manufacturer": info.manufacturer,
        "model": info.model,
        "sdk": info.sdk,
        "release": info.release,
        "fingerprint": fingerprint,
        "android": info.android,
        "platform": platform,
        "os_version": getattr(info, "os_version", ""),
        "api_version": getattr(info, "api_version", ""),
        "distribution": getattr(info, "distribution", ""),
        "hash_material_sha256": sha256_text(material),
    }


def transport_device_identity(transport: Transport) -> dict[str, Any]:
    """Identity of the connected device with platform detection applied.

    doctor() sets platform before hashing; later stages must normalize the same
    way, otherwise the same phone gets two different ids (HarmonyOS compat).
    """
    info = transport.device_info()
    info.platform = detect_platform(info)
    return device_identity(info)


class Doctor:
    def __init__(self, transport: Transport, config: dict[str, Any], *, root_mode: str = "auto", try_adb_root: bool = False) -> None:
        self.transport = transport
        self.config = config
        self.root_mode = root_mode
        self.try_adb_root = try_adb_root
        self.warnings: list[str] = []

    def _ensure_connected(self) -> None:
        if self.transport.name == "mock":
            return
        try:
            self.transport.ensure_ready()
            rc, out, err = self.transport.shell_rc("true", timeout=30)
        except TransportError:
            raise
        if rc != 0:
            detail = (err or out).strip()[:300]
            raise TransportError(
                "无法连接设备或执行 shell 命令。请检查 USB 调试/无线调试授权。"
                + (f" 详情：{detail}" if detail else "")
            )

    def _probe_commands(self, names: tuple[str, ...] | list[str] = PROBE_COMMANDS) -> dict[str, bool]:
        if self.transport.name == "mock":
            commands = getattr(self.transport, "caps", {}).get("commands")
            if isinstance(commands, dict):
                return {name: bool(commands.get(name, True)) for name in names}
            return {name: True for name in names}
        result: dict[str, bool] = {}
        for command in names:
            try:
                rc, out, _ = self.transport.shell_rc(
                    f"command -v {command} >/dev/null 2>&1; echo $?", timeout=20
                )
                result[command] = rc == 0 and out.strip().endswith("0")
            except TransportError:
                result[command] = False
        return result

    def _android_user(self, commands: dict[str, bool]) -> str:
        mock_caps = getattr(self.transport, "caps", {}) if self.transport.name == "mock" else {}
        if isinstance(mock_caps, dict) and mock_caps.get("android_user") is not None:
            return str(mock_caps.get("android_user"))
        if not commands.get("am"):
            return ""
        try:
            rc, out, _ = self.transport.shell_rc("am get-current-user", timeout=20)
        except TransportError:
            return ""
        value = (out or "").strip().splitlines()[0].strip() if (out or "").strip() else ""
        match = re.search(r"\b(\d+)\b", value)
        return match.group(1) if rc == 0 and match else ""

    def _harmony_photo_roots(self) -> list[str]:
        """Discover media-library roots per numeric user uid.

        The historical example path uses uid 100, but multi-user devices and
        newer builds can differ. Probe /mnt/data first, then always keep 100 as
        a documented fallback.
        """
        roots: list[str] = []
        try:
            for entry in self.transport.listdir("/mnt/data"):
                if entry.kind == "dir" and str(entry.name).isdigit():
                    roots.append(harmony_photo_root(entry.name))
        except TransportError:
            pass
        if HARMONY_PHOTO_ROOT not in roots:
            roots.insert(0, HARMONY_PHOTO_ROOT)
        return list(dict.fromkeys(roots))

    def run(self) -> dict[str, Any]:
        self._ensure_connected()
        privilege = "shell"
        privilege_error = ""
        try:
            if self.try_adb_root and hasattr(self.transport, "try_adb_root"):
                if self.transport.try_adb_root():
                    self.transport._privilege = "root"  # type: ignore[attr-defined]
            privilege = self.transport.configure_privilege(self.root_mode)
        except TransportError as exc:
            privilege_error = str(exc)
            privilege = getattr(self.transport, "privilege", "shell")

        device = self.transport.device_info()
        platform = detect_platform(device)
        device.platform = platform
        profile = profile_for(platform)
        identity = device_identity(device)
        commands = self._probe_commands(profile.command_probes)
        android_user = self._android_user(commands)
        packages = self.transport.list_packages()
        probe_paths = list(profile.probe_paths)
        harmony_photo_roots: list[str] = []
        if platform == HARMONY_NEXT:
            harmony_photo_roots = self._harmony_photo_roots()
            for root in harmony_photo_roots:
                if root not in probe_paths:
                    probe_paths.append(root)
        probes = {path: self.transport.access(path).to_dict() for path in probe_paths}
        storage = []
        for path in list(profile.default_roots) + ["/data"]:
            try:
                info = self.transport.df(path)
            except TransportError as exc:
                info = {"path": path, "error": str(exc)}
            if info.get("total_bytes") or info.get("error"):
                storage.append(info)

        def can_read(path: str) -> bool:
            probe = probes.get(path) or {}
            return bool(probe.get("readable") and probe.get("listable"))

        if platform == HARMONY_NEXT:
            public = can_read(HARMONY_PUBLIC_ROOT)
            docs = can_read(HARMONY_PUBLIC_ROOT + "/Docs")
            readable_photos = [root for root in harmony_photo_roots if can_read(root)]
            photo = bool(readable_photos)
            tmp = can_read(HARMONY_TMP_ROOT)
            can_write_public = bool((probes.get(HARMONY_PUBLIC_ROOT) or {}).get("writable"))
            hash_ok = bool(commands.get("sha256sum") or commands.get("md5sum"))
            tier = "limited" if public else "blocked"
            limitations: list[str] = []
            if not public:
                limitations.append("无法读取 /storage/media/100/local/files：HarmonyOS NEXT 公共目录不可读，不能执行有意义扫描。")
            if not docs:
                limitations.append("无法读取公共目录下的 Docs/Download：下载目录清理不可用。")
            if not photo:
                limitations.append("无法读取媒体库 /mnt/data/<uid>/media_fuse/Photo（需要 API 22+、正确的用户 uid 或系统允许）。")
            if not tmp:
                limitations.append("无法读取 /data/local/tmp：临时区清理不可用。")
            if not hash_ok:
                limitations.append("设备缺少 sha256sum/md5sum：重复文件只能按大小分组。")
            if not can_write_public:
                limitations.append("公共目录不可写：隔离区无法创建，执行阶段会被拒绝。")
            if not (commands.get("find") and commands.get("stat") and commands.get("xargs")):
                limitations.append("设备缺少 find/stat/xargs：扫描能力受限。")
            limitations.append("HarmonyOS NEXT 不支持 Android APK / adb / Shizuku / Termux；应用私有缓存无法按文件扫描。")
            limitations.append("应用缓存可尝试官方 bm clean -c -n <bundle>（需开发者模式），本版本不自动执行，需真机验证。")
            if self.transport.name == "mock":
                limitations.append("当前为 mock 模拟设备，结果仅用于测试，不代表任何真实手机。")
            recommended_roots = ([HARMONY_PUBLIC_ROOT] if public else []) + readable_photos
            capabilities = {
                "privilege": "shell",
                "tier": tier,
                "tier_label": {"limited": "受限（仅 HarmonyOS 公共目录）", "blocked": "不可用"}.get(tier, tier),
                "platform": platform,
                "sdcard_read": public,
                "sdcard_write": can_write_public,
                "android_data_read": False,
                "android_obb_read": False,
                "internal_data_read": False,
                "package_manager": bool(packages.available and commands.get("bm")),
                "android_user": android_user,
                "hashing": hash_ok,
                "quarantine_usable": can_write_public,
                "public_read": public,
                "public_write": can_write_public,
                "docs_read": docs,
                "photo_read": photo,
                "harmony_photo_roots": readable_photos,
                "tmp_read": tmp,
                "commands": commands,
            }
            capabilities.update({key: value for key, value in platform_identity_extra(platform).items()
                                 if key not in capabilities})
        else:
            sdcard = can_read("/sdcard")
            android_data = can_read("/sdcard/Android/data")
            android_obb = can_read("/sdcard/Android/obb")
            internal = can_read("/data/data") if privilege in {"root", "su"} else False
            can_write_sdcard = bool((probes.get("/sdcard") or {}).get("writable"))
            can_quarantine = can_write_sdcard or (privilege in {"root", "su"} and internal)
            pm_ok = bool(packages.available and commands.get("pm", True))
            hash_ok = bool(commands.get("sha256sum") or commands.get("md5sum"))

            if privilege in {"root", "su"}:
                tier = "full"
            elif sdcard and android_data:
                tier = "standard"
            elif sdcard:
                tier = "limited"
            else:
                tier = "blocked"

            limitations: list[str] = []
            if not sdcard:
                limitations.append("无法读取 /sdcard，Skill 不能执行有意义的扫描。请先在手机上授权 USB 调试。")
            if not android_data:
                limitations.append(
                    "无法读取 /sdcard/Android/data：该目录在当前权限级别下受限制。"
                    "将无法发现应用外部缓存与卸载残留；请使用 Root/Shizuku 或手动授权。"
                )
            if not android_obb:
                limitations.append("无法读取 /sdcard/Android/obb：OBB 残留无法发现。")
            if not pm_ok:
                limitations.append("无法执行 pm list packages：无法可靠判断应用是否已卸载，卸载残留检测会被跳过。")
            if not internal:
                limitations.append("无法读取 /data/data：应用内部 cache 与内部残留无法扫描（通常需要 Root）。")
            if not hash_ok:
                limitations.append("设备缺少 sha256sum/md5sum：重复文件只能按大小分组，无法确认完全重复。")
            if not can_write_sdcard:
                limitations.append("当前会话没有 /sdcard 写权限：无法把文件移入隔离区，执行阶段会被拒绝。")
            if not commands.get("find") or not commands.get("stat") or not commands.get("xargs"):
                limitations.append("设备缺少 find/stat/xargs：扫描能力受限。")
            if device.sdk and device.sdk >= 30:
                limitations.append(
                    "Android 11+ 启用 scoped storage。ADB shell 能否访问 Android/data 取决于 ROM/版本，"
                    "本报告只依据实际探测结果，不假定一定有权限。"
                )
            if privilege_error:
                limitations.append(f"提权失败：{privilege_error}")
            if platform == HARMONY_COMPAT:
                limitations.append("检测到 HarmonyOS 兼容版：沿用 Android 目录与 adb 通路，扫描规则与 Android 相同。")
            if self.transport.name == "mock":
                limitations.append("当前为 mock 模拟设备，结果仅用于测试，不代表任何真实手机。")
            if not device.sdk and not device.model:
                limitations.append("未能读取设备型号/系统版本，平台按 Android 兜底；请先确认 getprop 是否可用。")
            if self.transport.name == "local" and android_data:
                limitations.append("local 通路能读取 Android/data 可能来自容器挂载，不代表 scoped storage 下普通应用可读。")

            recommended_roots = ["/sdcard"] if sdcard else []
            if internal:
                recommended_roots.append("internal-cache")

            capabilities = {
                "privilege": privilege,
                "tier": tier,
                "tier_label": {
                    "full": "完整（Root/SU）",
                    "standard": "标准（可读 Android/data）",
                    "limited": "受限（仅共享存储）",
                    "blocked": "不可用",
                }.get(tier, tier),
                "platform": platform,
                "sdcard_read": sdcard,
                "sdcard_write": can_write_sdcard,
                "android_data_read": android_data,
                "android_obb_read": android_obb,
                "internal_data_read": internal,
                "package_manager": pm_ok,
                "android_user": android_user,
                "hashing": hash_ok,
                "quarantine_usable": can_quarantine,
                "commands": commands,
            }
            capabilities.update({key: value for key, value in platform_identity_extra(platform).items()
                                 if key not in capabilities})
        capabilities["transport"] = self.transport.name

        return {
            "schema": 1,
            "generated_at": now_iso(),
            "transport": self.transport.name,
            "platform": platform,
            "platform_label": profile.label,
            "transport_detail": {
                "serial": getattr(self.transport, "serial", ""),
                "adb_path": getattr(self.transport, "adb_path", ""),
                "hdc_path": getattr(self.transport, "hdc_path", ""),
                "rish_path": getattr(self.transport, "rish_path", ""),
            },
            "device": device.to_dict(),
            "device_identity": identity,
            "adb_devices": getattr(self.transport, "adb_devices", []) if self.transport.name == "adb" else [],
            "android": device.android,
            "packages": {
                "available": packages.available,
                "installed_count": len(packages.installed),
                "uninstalled_count": len(packages.uninstalled),
                "system_count": len(packages.system),
                "error": packages.error,
            },
            "capabilities": capabilities,
            "storage": storage,
            "probes": probes,
            "limitations": limitations,
            "unsupported_features": list(profile.notes) if platform == HARMONY_NEXT else [],
            "recommended_roots": recommended_roots,
            "warnings": self.warnings,
            "note": "doctor 全程只读；未创建目录、未写入设备。",
        }


def doctor_markdown(report: dict[str, Any]) -> str:
    caps = report.get("capabilities") or {}
    lines = [
        f"# {report.get('platform_label') or 'Android'} 存储体检报告",
        "",
        f"- 生成时间：{report.get('generated_at')}",
        f"- 传输方式：{report.get('transport')}",
        f"- 设备：{((report.get('device') or {}).get('manufacturer') or '')} {((report.get('device') or {}).get('model') or '')}".strip(),
        f"- 设备身份：{(report.get('device_identity') or {}).get('id', '-')}（序列号：{(report.get('device_identity') or {}).get('serial', '-')}）",
        f"- 系统：{report.get('android')}",
        f"- 平台：{report.get('platform_label') or report.get('platform') or '-'}",
        f"- 权限级别：{caps.get('tier_label')}（{caps.get('privilege')}）",
        f"- 当前用户：{caps.get('android_user') or '-'}",
        f"- 应用包：已安装 {((report.get('packages') or {}).get('installed_count'))}，未安装包记录 {((report.get('packages') or {}).get('uninstalled_count'))}",
        "",
        "## 存储空间",
        "",
        "| 路径 | 总容量 | 已用 | 可用 |",
        "|---|---:|---:|---:|",
    ]
    for item in report.get("storage") or []:
        lines.append(
            f"| {item.get('path')} | {human_bytes(item.get('total_bytes'))} | "
            f"{human_bytes(item.get('used_bytes'))} | {human_bytes(item.get('available_bytes'))} |"
        )
    lines += ["", "## 能力探测", "", "| 能力 | 结果 |", "|---|---|"]
    checks = [
        ("共享存储读取", "sdcard_read"), ("共享存储写入", "sdcard_write"),
        ("Android/data 读取", "android_data_read"), ("Android/obb 读取", "android_obb_read"),
        ("应用内部数据读取", "internal_data_read"), ("包管理器", "package_manager"),
        ("哈希工具", "hashing"), ("隔离区可用", "quarantine_usable"),
    ]
    for label, key in checks:
        lines.append(f"| {label} | {'✅' if caps.get(key) else '❌'} |")
    lines += ["", "## 关键路径可访问性", "", "| 路径 | 存在 | 可读 | 可写 | 可列举 | 说明 |", "|---|---|---|---|---|---|"]
    for path, probe in (report.get("probes") or {}).items():
        lines.append(
            f"| {path} | {'是' if probe.get('exists') else '否'} | {'是' if probe.get('readable') else '否'} | "
            f"{'是' if probe.get('writable') else '否'} | {'是' if probe.get('listable') else '否'} | {probe.get('error') or ''} |"
        )
    limitations = report.get("limitations") or []
    if limitations:
        lines += ["", "## 权限限制（必须向用户如实说明）", ""]
        lines += [f"- {item}" for item in limitations]
    lines += ["", "> doctor 仅做只读探测；不会创建、移动或删除任何文件。", ""]
    return "\n".join(lines)
