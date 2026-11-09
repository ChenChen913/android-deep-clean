"""Platform profiles and system identification.

Three profiles are supported:

- android            : AOSP / Android phones (adb, Shizuku, Termux)
- harmonyos_compat   : HarmonyOS 1.x-4.x, which keeps the Android runtime and adb
- harmonyos_next     : HarmonyOS NEXT / 5+, native OpenHarmony, hdc only

Only capabilities that are documented (and, where noted, still need a real
device check) are advertised.  Unknown platforms are treated as Android only
when Android build properties are present; otherwise they stay conservative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

ANDROID = "android"
HARMONY_COMPAT = "harmonyos_compat"
HARMONY_NEXT = "harmonyos_next"

HARMONY_PUBLIC_ROOT = "/storage/media/100/local/files"
HARMONY_PHOTO_ROOT = "/mnt/data/100/media_fuse/Photo"
HARMONY_TMP_ROOT = "/data/local/tmp"
HARMONY_QUARANTINE_ROOT = HARMONY_PUBLIC_ROOT + "/Docs/.android-deep-clean/quarantine"
_HARMONY_PHOTO_RE = re.compile(r"^(/mnt/data/(\d+)/media_fuse/Photo)(?:/|$)")


def harmony_photo_root(uid: str | int = "100") -> str:
    return f"/mnt/data/{int(uid)}/media_fuse/Photo"


def match_harmony_photo_root(path: str) -> str:
    """Return the media-library root for any numeric user uid, or ''."""
    match = _HARMONY_PHOTO_RE.match(str(path or ""))
    return match.group(1) if match else ""


@dataclass(frozen=True)
class PlatformProfile:
    id: str
    label: str
    family: str
    transport: str
    default_roots: tuple[str, ...]
    probe_paths: tuple[str, ...]
    user_media_roots: tuple[str, ...]
    command_probes: tuple[str, ...]
    supports_android_packages: bool
    supports_app_cache_files: bool
    supports_shizuku: bool
    supports_termux: bool
    supports_bm_clean: bool
    notes: tuple[str, ...] = ()


ANDROID_PROBES = (
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
)
ANDROID_COMMANDS = ("find", "stat", "xargs", "sha256sum", "md5sum", "df", "pm", "am", "realpath", "rm", "mv", "rmdir", "su")
HARMONY_COMMANDS = ("find", "stat", "xargs", "sha256sum", "md5sum", "df", "rm", "mv", "rmdir", "ls", "toybox", "param", "bm")

PROFILES: dict[str, PlatformProfile] = {
    ANDROID: PlatformProfile(
        id=ANDROID, label="Android", family="android", transport="adb",
        default_roots=("/sdcard",), probe_paths=ANDROID_PROBES,
        user_media_roots=("/sdcard/DCIM", "/sdcard/Pictures", "/sdcard/Movies", "/sdcard/Music",
                          "/sdcard/Documents", "/sdcard/Download", "/sdcard/Recordings"),
        command_probes=ANDROID_COMMANDS,
        supports_android_packages=True, supports_app_cache_files=True,
        supports_shizuku=True, supports_termux=True, supports_bm_clean=False,
    ),
    HARMONY_COMPAT: PlatformProfile(
        id=HARMONY_COMPAT, label="HarmonyOS 兼容版（Android 运行时）", family="harmonyos", transport="adb",
        default_roots=("/sdcard",), probe_paths=ANDROID_PROBES,
        user_media_roots=("/sdcard/DCIM", "/sdcard/Pictures", "/sdcard/Movies", "/sdcard/Music",
                          "/sdcard/Documents", "/sdcard/Download", "/sdcard/Recordings"),
        command_probes=ANDROID_COMMANDS,
        supports_android_packages=True, supports_app_cache_files=True,
        supports_shizuku=True, supports_termux=True, supports_bm_clean=False,
        notes=(
            "HarmonyOS 1.x-4.x 保留 Android 运行时，可沿用 adb / Shizuku / Termux 通路。",
            "系统版本通过 hw_sc.build.platform.version 等属性识别，Android 目录结构仍然有效。",
        ),
    ),
    HARMONY_NEXT: PlatformProfile(
        id=HARMONY_NEXT, label="HarmonyOS NEXT（原生鸿蒙）", family="harmonyos", transport="hdc",
        default_roots=(HARMONY_PUBLIC_ROOT, HARMONY_PHOTO_ROOT),
        probe_paths=(HARMONY_PUBLIC_ROOT, HARMONY_PUBLIC_ROOT + "/Docs", HARMONY_PUBLIC_ROOT + "/Docs/Download",
                     HARMONY_PHOTO_ROOT, HARMONY_TMP_ROOT, "/data/app/el2/100/base", "/data/log"),
        user_media_roots=(HARMONY_PUBLIC_ROOT, HARMONY_PHOTO_ROOT),
        command_probes=HARMONY_COMMANDS,
        supports_android_packages=False, supports_app_cache_files=False,
        supports_shizuku=False, supports_termux=False, supports_bm_clean=True,
        notes=(
            "HarmonyOS NEXT 不兼容 Android APK，也没有 Android ADB Shell / Shizuku / Termux。",
            "hdc 可访问公共目录 /storage/media/100/local/files 与媒体库（API 22+）。",
            "其他应用沙箱默认不可读；debug 应用可用 hdc shell -b <bundle>，但本 Skill 不据此删除文件。",
            "官方 bm clean -c -n <bundle> 可在开发者模式清理指定应用缓存，是否可用需真机冒烟验证。",
            "公共目录的真实可写性、find/stat/toybox 差异仍需 HarmonyOS NEXT 真机验证。",
        ),
    ),
}

_ALIASES = {
    "android": ANDROID,
    "aosp": ANDROID,
    "harmony": HARMONY_COMPAT,
    "harmonyos": HARMONY_COMPAT,
    "harmonyos-compat": HARMONY_COMPAT,
    "harmonyos_compat": HARMONY_COMPAT,
    "harmonyos-next": HARMONY_NEXT,
    "harmonyos_next": HARMONY_NEXT,
    "next": HARMONY_NEXT,
    "openharmony": HARMONY_NEXT,
}


def normalize_platform(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "")
    return _ALIASES.get(text, "")


def profile_for(platform: str) -> PlatformProfile:
    return PROFILES.get(normalize_platform(platform) or ANDROID, PROFILES[ANDROID])


def detect_platform(info: Any) -> str:
    """Detect platform from already-collected device properties.

    HarmonyOS NEXT has no Android SDK level; HarmonyOS 1.x-4.x keeps one.
    Explicit values from a transport (hdc) always win.
    """
    explicit = normalize_platform(getattr(info, "platform", ""))
    if explicit:
        return explicit
    sdk = int(getattr(info, "sdk", 0) or 0)
    api = str(getattr(info, "api_version", "") or "")
    distribution = str(getattr(info, "distribution", "") or "")
    harmony_version = str(getattr(info, "harmony_version", "") or "")
    release = str(getattr(info, "release", "") or "")
    text = " ".join((api, distribution, harmony_version, release)).lower()
    if api or harmony_version or "harmony" in text or "openharmony" in text:
        return HARMONY_COMPAT if sdk > 0 else HARMONY_NEXT
    return ANDROID


def platform_identity_extra(platform: str) -> dict[str, Any]:
    profile = profile_for(platform)
    return {
        "platform": profile.id,
        "platform_label": profile.label,
        "platform_family": profile.family,
        "transport": profile.transport,
        "supports_android_packages": profile.supports_android_packages,
        "supports_app_cache_files": profile.supports_app_cache_files,
        "supports_shizuku": profile.supports_shizuku,
        "supports_termux": profile.supports_termux,
        "supports_bm_clean": profile.supports_bm_clean,
        "default_roots": list(profile.default_roots),
        "notes": list(profile.notes),
    }
