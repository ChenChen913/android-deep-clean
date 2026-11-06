"""Path safety policy and cleanup-category definitions.

This module is deliberately conservative. A path is only eligible for a
cleanup category if it passes BOTH:
  1. an absolute-path hard block list (system/app-data/user-data), and
  2. the category-specific structural rules below.

When in doubt the guard returns blocked=True.  The analyzer may still list a
blocked item as a "protected finding" (never selectable), which keeps the
report honest without making dangerous data deletable.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

from .models import PackageIndex
from .platform import HARMONY_NEXT, HARMONY_PHOTO_ROOT, HARMONY_PUBLIC_ROOT, normalize_platform
from .util import RISK_ORDER, ext_of, is_under, normalize_device_path, risk_max

# ---------------------------------------------------------------- categories
CATEGORY_META: dict[str, dict[str, Any]] = {
    "app_cache": {
        "title": "应用缓存",
        "risk": "SAFE",
        "action": "clean_dir_children",
        "description": "应用 cache/code_cache/tmp 目录，通常可由应用重新生成。",
    },
    "residue": {
        "title": "卸载残留",
        "risk": "CAUTION",
        "action": "clean_dir_children",
        "description": "包管理器显示未安装，但仍残留的应用外部数据/OBB/媒体目录。",
    },
    "thumbnails": {
        "title": "缩略图缓存",
        "risk": "SAFE",
        "action": "clean_dir_children",
        "description": "系统/应用生成的缩略图缓存，可重新生成。",
    },
    "temp_logs": {
        "title": "临时文件与日志",
        "risk": "SAFE",
        "action": "clean_dir_children",
        "description": "临时扩展名、崩溃日志、下载未完成文件。",
    },
    "trash": {
        "title": "回收站/垃圾桶",
        "risk": "CAUTION",
        "action": "clean_dir_children",
        "description": "文件管理器/应用回收站中的旧文件，删除后不可恢复。",
    },
    "duplicates": {
        "title": "重复文件",
        "risk": "CAUTION",
        "action": "delete_files",
        "description": "SHA-256 完全相同的文件，建议每组至少保留一份。",
    },
    "apk": {
        "title": "旧安装包",
        "risk": "CAUTION",
        "action": "clean_dir_children",
        "description": "长期未修改的 APK/APKS/XAPK/APKM 安装包。",
    },
    "archives": {
        "title": "旧压缩包/备份",
        "risk": "CAUTION",
        "action": "clean_dir_children",
        "description": "旧 zip/7z/rar/tar/bak 等压缩或备份文件。",
    },
    "screenshots": {
        "title": "旧截图",
        "risk": "CAUTION",
        "action": "clean_dir_children",
        "description": "长期未修改的屏幕截图，是否保留需要用户判断。",
    },
    "large_old": {
        "title": "长期未用的大文件",
        "risk": "CAUTION",
        "action": "delete_files",
        "description": "体积大且长期未修改的文件，可能是下载、视频或备份。",
    },
    "media_old": {
        "title": "旧照片/视频",
        "risk": "CAUTION",
        "action": "delete_files",
        "description": "个人媒体文件。默认绝不批量清理，必须逐项确认。",
    },
    "download_old": {
        "title": "旧下载文件",
        "risk": "CAUTION",
        "action": "clean_dir_children",
        "description": "Download 目录中较旧的下载内容。",
    },
    "chat_cache": {
        "title": "聊天应用缓存",
        "risk": "CAUTION",
        "action": "clean_dir_children",
        "description": "微信/QQ/Tencent 目录中明确的 cache/temp 子目录，不包含聊天记录与媒体。",
    },
    "browser_cache": {
        "title": "浏览器缓存",
        "risk": "SAFE",
        "action": "clean_dir_children",
        "description": "已识别浏览器应用的 cache 目录。",
    },
    "ota": {
        "title": "旧系统更新包",
        "risk": "CAUTION",
        "action": "clean_dir_children",
        "description": "Download 等目录中的 OTA/update 压缩包。",
    },
    "empty_dirs": {
        "title": "空目录",
        "risk": "CAUTION",
        "action": "rmdir",
        "description": "长期存在的空目录。仅用 rmdir 删除，目录非空会失败。",
    },
    "protected": {
        "title": "受保护内容",
        "risk": "DANGEROUS",
        "action": "none",
        "description": "数据库、应用数据、OBB、个人媒体等默认保护内容。",
    },
}

CATEGORY_ORDER = [
    "residue", "app_cache", "browser_cache", "chat_cache", "thumbnails",
    "temp_logs", "trash", "duplicates", "apk", "archives", "screenshots",
    "large_old", "media_old", "download_old", "ota", "empty_dirs",
]

# ---------------------------------------------------------------- app catalog
APP_CATALOG: dict[str, dict[str, str]] = {
    "com.tencent.mm": {"label": "微信", "kind": "chat"},
    "com.tencent.mobileqq": {"label": "QQ", "kind": "chat"},
    "com.tencent.tim": {"label": "TIM", "kind": "chat"},
    "com.tencent.wework": {"label": "企业微信", "kind": "chat"},
    "com.android.chrome": {"label": "Chrome", "kind": "browser"},
    "com.chrome.beta": {"label": "Chrome Beta", "kind": "browser"},
    "com.sec.android.app.sbrowser": {"label": "三星浏览器", "kind": "browser"},
    "com.quark.browser": {"label": "夸克浏览器", "kind": "browser"},
    "com.UCMobile": {"label": "UC 浏览器", "kind": "browser"},
    "com.baidu.searchbox": {"label": "百度", "kind": "browser"},
    "org.mozilla.firefox": {"label": "Firefox", "kind": "browser"},
    "com.microsoft.emmx": {"label": "Edge", "kind": "browser"},
    "com.ss.android.ugc.aweme": {"label": "抖音", "kind": "shortvideo"},
    "com.smile.gifmaker": {"label": "快手", "kind": "shortvideo"},
    "com.tencent.qqlive": {"label": "腾讯视频", "kind": "video"},
    "com.qiyi.video": {"label": "爱奇艺", "kind": "video"},
    "com.youku.phone": {"label": "优酷", "kind": "video"},
    "tv.danmaku.bili": {"label": "哔哩哔哩", "kind": "video"},
    "com.tencent.music.lite": {"label": "QQ 音乐", "kind": "music"},
    "com.netease.cloudmusic": {"label": "网易云音乐", "kind": "music"},
    "com.eg.android.AlipayGphone": {"label": "支付宝", "kind": "app"},
    "com.tencent.pb": {"label": "微信电话本", "kind": "app"},
    "com.android.vending": {"label": "Google Play", "kind": "app"},
    "com.google.android.youtube": {"label": "YouTube", "kind": "video"},
    "com.google.android.apps.photos": {"label": "Google 相册", "kind": "media"},
    "com.sec.android.gallery3d": {"label": "三星相册", "kind": "media"},
    "com.miui.gallery": {"label": "小米相册", "kind": "media"},
    "com.coloros.gallery3d": {"label": "OPPO 相册", "kind": "media"},
}


def app_info(package: str) -> dict[str, str]:
    info = APP_CATALOG.get(package)
    if info:
        return dict(info)
    lower = package.lower()
    if any(k in lower for k in ("browser", "chrome", "firefox", "webview")):
        return {"label": package, "kind": "browser"}
    if any(k in lower for k in ("gallery", "photos", "camera")):
        return {"label": package, "kind": "media"}
    if any(k in lower for k in ("video", "player", "tv", "bili", "douyin", "aweme", "kuaishou")):
        return {"label": package, "kind": "video"}
    if any(k in lower for k in ("music", "audio", "podcast")):
        return {"label": package, "kind": "music"}
    return {"label": package, "kind": "app"}


# ---------------------------------------------------------------- hard blocks
HARD_BLOCK_PREFIXES = (
    "/system", "/vendor", "/product", "/product_services", "/odm", "/oem",
    "/apex", "/boot", "/boot.img", "/dtbo.img", "/vbmeta.img", "/vbmeta_system.img",
    "/recovery.img", "/init", "/proc", "/sys", "/dev", "/acct", "/config",
    "/efs", "/firmware", "/persist", "/metadata", "/data/app", "/data/apex",
    "/data/misc", "/data/system", "/data/security", "/data/tombstones",
    "/data/anr", "/data/dalvik-cache", "/data/adb", "/data/vendor",
    "/data/backup", "/data/ssh", "/data/local/prop",
)

PROTECTED_DIR_NAMES = {
    "shared_prefs", "databases", "files", "no_backup", "app_webview",
    "app_textures", "sessions", "cache2", "code_cache2",
}

PROTECTED_EXTENSIONS = {
    ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".sqlite-wal",
    ".realm", ".mmkv", ".keystore", ".jks", ".p12", ".pfx", ".pem",
    ".key", ".cer", ".crt", ".der", ".gpg", ".asc",
}

CHAT_ROOTS = (
    "/sdcard/tencent", "/sdcard/Tencent", "/sdcard/WeiXin", "/sdcard/weixin",
    "/sdcard/WeChat", "/sdcard/wechat", "/sdcard/QQ", "/sdcard/qq",
    "/sdcard/MicroMsg", "/sdcard/TencentQQ",
)

USER_MEDIA_ROOTS = (
    "/sdcard/DCIM", "/sdcard/Pictures", "/sdcard/Movies", "/sdcard/Music",
    "/sdcard/Documents", "/sdcard/Download", "/sdcard/Recordings",
    "/sdcard/Voice Recorder", "/sdcard/Podcasts", "/sdcard/Ringtones",
    "/sdcard/Alarms", "/sdcard/Notifications",
)

CHAT_MEDIA_COMPONENTS = {
    "image", "image2", "video", "video2", "voice", "voice2", "audio",
    "emoji", "avatar", "sns", "backup", "file", "files", "media",
    "msg", "message", "messages", "chat", "database", "databases",
}

CACHE_DIR_NAMES = {"cache", "code_cache", "tmp", "temp", ".tmp", ".temp"}
THUMB_DIR_NAMES = {".thumbnails", "thumbnails", "thumbs", ".thumb", "thumb"}
TRASH_DIR_NAMES = {".trash", "trash", ".recycle", "recycle", ".recyclebin", "recycle_bin", "$recycle.bin"}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp", ".dng", ".raw", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".wmv", ".webm", ".m4v", ".3gp", ".ts", ".mpeg", ".mpg"}
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".opus", ".wma", ".amr"}
MEDIA_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS
ARCHIVE_EXTS = {".zip", ".7z", ".rar", ".tar", ".tgz", ".tar.gz", ".gz", ".bz2", ".xz", ".iso", ".img", ".apk", ".apks", ".xapk", ".apkm"}
APK_EXTS = {".apk", ".apks", ".xapk", ".apkm"}
TEMP_EXTS = {".tmp", ".temp", ".part", ".partial", ".crdownload", ".download", ".!qb", ".dmp", ".hprof"}
LOG_EXTS = {".log", ".log.1", ".log.2", ".txt.log", ".stacktrace", ".trace", ".anr"}
DOC_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".epub", ".mobi", ".rtf"}

SCREENSHOT_RE = re.compile(r"(?:^|[\\/])(?:screenshot|screen_shot|screen-shot|截屏|屏幕截图|screenshot_\d{4})", re.IGNORECASE)
OTA_RE = re.compile(r"(?:^|[\\/])(?:update|ota|upgrade|firmware)[^/]*\.(?:zip|bin|img|tar|gz)$", re.IGNORECASE)


def category_risk(category: str) -> str:
    return CATEGORY_META.get(category, {}).get("risk", "DANGEROUS")


def category_title(category: str) -> str:
    return CATEGORY_META.get(category, {}).get("title", category)


def is_system_path(path: str) -> bool:
    normalized = normalize_device_path(path)
    return any(is_under(normalized, prefix) for prefix in HARD_BLOCK_PREFIXES)


def is_quarantine_path(path: str, config: dict[str, Any] | None = None) -> bool:
    normalized = normalize_device_path(path)
    roots = ["/sdcard/.android-deep-clean"]
    if config:
        roots.append(str(config["quarantine"]["external_root"]))
        roots.append(str(config["quarantine"]["internal_root"]))
    return any(is_under(normalized, root) for root in roots)


CHAT_ROOT_PACKAGES = (
    ("/sdcard/MicroMsg", "com.tencent.mm"),
    ("/sdcard/tencent/MicroMsg", "com.tencent.mm"),
    ("/sdcard/tencent/WeChat", "com.tencent.mm"),
    ("/sdcard/WeiXin", "com.tencent.mm"),
    ("/sdcard/weixin", "com.tencent.mm"),
    ("/sdcard/WeChat", "com.tencent.mm"),
    ("/sdcard/wechat", "com.tencent.mm"),
    ("/sdcard/TencentQQ", "com.tencent.mobileqq"),
    ("/sdcard/tencent/QQ", "com.tencent.mobileqq"),
    ("/sdcard/QQ", "com.tencent.mobileqq"),
)


def package_from_path(path: str) -> str:
    normalized = normalize_device_path(path)
    match = re.match(r"^/sdcard/Android/(?:data|obb|media)/([^/]+)", normalized)
    if match:
        return match.group(1)
    match = re.match(r"^/data/(?:data|user/0)/([^/]+)", normalized)
    if match:
        return match.group(1)
    lower = normalized.lower()
    for prefix, package in CHAT_ROOT_PACKAGES:
        if lower == prefix.lower() or lower.startswith(prefix.lower() + "/"):
            return package
    return ""


def app_dir_kind(path: str) -> str:
    normalized = normalize_device_path(path)
    match = re.match(r"^/sdcard/Android/(data|obb|media)(?:/|$)", normalized)
    return match.group(1) if match else ""


def is_user_media_path(path: str) -> bool:
    normalized = normalize_device_path(path)
    return any(is_under(normalized, root) for root in USER_MEDIA_ROOTS)


def is_chat_path(path: str) -> bool:
    normalized = normalize_device_path(path)
    return any(is_under(normalized, root) for root in CHAT_ROOTS)


def has_component(path: str, names: set[str]) -> bool:
    return any(part.lower() in names for part in normalize_device_path(path).split("/") if part)


def is_cache_path(path: str) -> bool:
    normalized = normalize_device_path(path)
    parts = [p.lower() for p in normalized.split("/") if p]
    if not parts:
        return False
    if any(part in CACHE_DIR_NAMES or part in THUMB_DIR_NAMES for part in parts):
        return True
    if "/Android/data/" in normalized and any(part in CACHE_DIR_NAMES for part in parts):
        return True
    return False


def is_thumb_path(path: str) -> bool:
    return has_component(path, THUMB_DIR_NAMES)


def is_trash_path(path: str) -> bool:
    normalized = normalize_device_path(path)
    name = posixpath.basename(normalized).lower()
    if name.startswith(".trashed-"):
        return True
    return has_component(normalized, TRASH_DIR_NAMES)


def is_protected_filename(name: str) -> bool:
    lower = name.lower()
    if lower in {".nomedia", ".nomedia.tmp"}:
        return True
    if lower.endswith(tuple(PROTECTED_EXTENSIONS)):
        return True
    if lower.startswith(".") and lower.endswith((".bak", ".backup")):
        return True
    return False


def _hard_block(path: str) -> tuple[bool, str]:
    if is_quarantine_path(path):
        return True, "本 Skill 自己的隔离区，禁止作为清理目标"
    for prefix in HARD_BLOCK_PREFIXES:
        if is_under(path, prefix):
            return True, f"系统/应用核心路径受硬保护：{prefix}"
    if is_under(path, "/data/data") or is_under(path, "/data/user/0"):
        return True, "应用内部数据仅允许 cache/code_cache 子目录"
    if is_under(path, "/sdcard/Android"):
        if path in {"/sdcard/Android", "/sdcard/Android/data", "/sdcard/Android/obb", "/sdcard/Android/media"}:
            return True, "Android 根目录本身不允许删除"
    if path == "/sdcard" or path == "/storage/emulated/0":
        return True, "存储根目录不允许删除"
    return False, ""


def _guard_android_tree(path: str, category: str, packages: PackageIndex | None) -> tuple[bool, str, str]:
    """Return (allowed, reason, risk_floor) for /sdcard/Android/** paths."""
    package = package_from_path(path)
    kind = app_dir_kind(path)
    if not package:
        return False, "无法从路径解析应用包名", "DANGEROUS"
    installed = packages.is_installed(package) if packages and packages.available else None
    rel_parts = path.split("/")
    try:
        idx = rel_parts.index("Android")
        sub = rel_parts[idx + 3:]  # after Android/<data|obb|media>/<pkg>
    except (ValueError, IndexError):
        sub = []
    lower_sub = [p.lower() for p in sub]

    if kind == "data":
        cache_ok = bool(lower_sub) and lower_sub[0] in CACHE_DIR_NAMES
        if category == "residue" and installed is False:
            return True, "", "CAUTION"
        if category in {"app_cache", "browser_cache", "chat_cache", "thumbnails", "temp_logs", "trash"} and cache_ok:
            return True, "", "SAFE" if category != "chat_cache" else "CAUTION"
        if installed is False and cache_ok:
            return True, "", "SAFE"
        return False, "使用中应用的非缓存外部数据受保护（仅允许 cache/code_cache/tmp）", "DANGEROUS"
    if kind in {"obb", "media"}:
        if installed is False and category == "residue":
            return True, "", "CAUTION"
        return False, "OBB/Android/media 中的已安装应用数据受保护", "DANGEROUS"
    return False, "Android 目录下非 data/obb/media 的内容受保护", "DANGEROUS"


def _guard_internal_data(path: str, category: str, packages: PackageIndex | None) -> tuple[bool, str, str]:
    package = package_from_path(path)
    installed = packages.is_installed(package) if packages and packages.available else None
    if not package:
        return False, "无法解析应用包名", "DANGEROUS"
    remainder = path[len(f"/data/data/{package}"):] if path.startswith(f"/data/data/{package}") else ""
    parts = [p.lower() for p in remainder.split("/") if p]
    if parts and parts[0] in CACHE_DIR_NAMES:
        return True, "", "SAFE"
    if installed is False and category == "residue":
        return False, "内部应用数据即使残留也仅允许清理 cache/code_cache；整目录删除风险过高", "DANGEROUS"
    return False, "应用数据库/配置/文件区受硬保护", "DANGEROUS"


def _guard_harmony_next(
    path: str,
    category: str,
    *,
    kind: str = "file",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Conservative guard for HarmonyOS NEXT public storage only.

    Only the documented public directory and the media library are eligible.
    App sandboxes, system partitions and /data are always blocked.
    """
    if not category_risk_allowed(category):
        return {"allowed": False, "hard_block": True, "reason": f"未知清理类别：{category}", "risk_floor": "DANGEROUS"}
    roots = (HARMONY_PUBLIC_ROOT, HARMONY_PHOTO_ROOT)
    matched = next((root for root in roots if path == root or is_under(path, root)), "")
    if not matched:
        return {
            "allowed": False,
            "hard_block": True,
            "reason": "HarmonyOS NEXT 仅允许处理公共目录；应用沙箱与系统目录受硬保护",
            "risk_floor": "DANGEROUS",
        }
    if path == matched:
        return {"allowed": False, "hard_block": False, "reason": "公共目录本身不允许删除", "risk_floor": "DANGEROUS"}
    relative = path[len(matched):].lstrip("/")
    name = posixpath.basename(path)
    if is_protected_filename(name):
        return {"allowed": False, "hard_block": True, "reason": "数据库/密钥/.nomedia 等敏感文件受保护", "risk_floor": "DANGEROUS"}
    if any(part.lower() in PROTECTED_DIR_NAMES for part in relative.split("/") if part):
        return {"allowed": False, "hard_block": True, "reason": "应用数据库/配置目录受保护", "risk_floor": "DANGEROUS"}
    if category == "empty_dirs":
        if kind != "dir":
            return {"allowed": False, "hard_block": False, "reason": "空目录清理只接受目录", "risk_floor": "DANGEROUS"}
        if path.count("/") < 7:
            return {"allowed": False, "hard_block": False, "reason": "公共目录顶层不参与空目录清理", "risk_floor": "DANGEROUS"}
    if isinstance(config, dict):
        for prefix in config.get("extra_protected_paths") or []:
            try:
                if is_under(path, normalize_device_path(str(prefix))):
                    return {"allowed": False, "hard_block": True, "reason": f"用户配置的额外保护路径：{prefix}", "risk_floor": "DANGEROUS"}
            except Exception:  # noqa: BLE001
                continue
    # Everything in public storage is user-visible data: never SAFE by default.
    return {"allowed": True, "hard_block": False, "reason": "", "risk_floor": risk_max("CAUTION", category_risk(category))}


def guard_path(
    path: str,
    category: str,
    *,
    kind: str = "file",
    packages: PackageIndex | None = None,
    config: dict[str, Any] | None = None,
    platform: str = "android",
) -> dict[str, Any]:
    """Evaluate a candidate path for a category.

    Returns a dict with allowed/hard_block/reason/risk_floor.  Callers must
    treat any unexpected exception as blocked, never as allowed.
    """
    try:
        path = normalize_device_path(path)
    except Exception as exc:  # noqa: BLE001 - fail closed
        return {"allowed": False, "hard_block": True, "reason": f"路径非法：{exc}", "risk_floor": "DANGEROUS"}

    if normalize_platform(platform) == HARMONY_NEXT:
        return _guard_harmony_next(path, category, kind=kind, config=config)

    hard, hard_reason = _hard_block(path)
    if hard:
        # internal cache paths are the only exception to the generic /data/data block.
        # They must still pass every remaining check below (protected filenames,
        # protected directory names, extra_protected_paths), so we only relax the
        # hard block instead of returning early.
        internal_cache = (
            (is_under(path, "/data/data") or is_under(path, "/data/user/0"))
            and category in {"app_cache", "browser_cache"}
        )
        if not internal_cache:
            return {"allowed": False, "hard_block": True, "reason": hard_reason, "risk_floor": "DANGEROUS"}
        allowed, reason, floor = _guard_internal_data(path, category, packages)
        if not allowed:
            return {"allowed": False, "hard_block": True, "reason": reason, "risk_floor": floor}
        risk_floor = floor
    elif is_under(path, "/sdcard/Android"):
        allowed, reason, floor = _guard_android_tree(path, category, packages)
        if not allowed:
            return {"allowed": False, "hard_block": True, "reason": reason, "risk_floor": floor}
        risk_floor = floor
    else:
        risk_floor = category_risk(category)

    if not category_risk_allowed(category):
        return {"allowed": False, "hard_block": True, "reason": f"未知清理类别：{category}", "risk_floor": "DANGEROUS"}

    name = posixpath.basename(path)
    if is_protected_filename(name):
        return {"allowed": False, "hard_block": True, "reason": "数据库/密钥/.nomedia 等敏感文件受保护", "risk_floor": "DANGEROUS"}
    if any(part.lower() in PROTECTED_DIR_NAMES for part in path.split("/") if part):
        return {"allowed": False, "hard_block": True, "reason": "应用数据库/配置/shared_prefs 目录受保护", "risk_floor": "DANGEROUS"}

    # user personal directories
    if is_user_media_path(path):
        if category in {"empty_dirs"}:
            return {"allowed": False, "hard_block": False, "reason": "个人媒体目录中的空目录可能是用户有意创建，不自动处理", "risk_floor": "DANGEROUS"}
        risk_floor = risk_max(risk_floor, "CAUTION")
        if category in {"app_cache", "thumbnails", "temp_logs", "trash"} and not is_cache_path(path) and not is_thumb_path(path) and not is_trash_path(path):
            return {"allowed": False, "hard_block": False, "reason": "个人目录中的普通文件不属于安全缓存", "risk_floor": "DANGEROUS"}

    # chat directories: cache only, always CAUTION
    if is_chat_path(path):
        if category not in {"chat_cache", "app_cache", "browser_cache", "thumbnails"}:
            return {"allowed": False, "hard_block": False, "reason": "聊天应用媒体/记录目录默认保护，只允许明确 cache/temp 子目录", "risk_floor": "DANGEROUS"}
        if not is_cache_path(path):
            return {"allowed": False, "hard_block": False, "reason": "聊天目录中只有 cache/temp/thumbnails 子目录可考虑清理", "risk_floor": "DANGEROUS"}
        risk_floor = risk_max(risk_floor, "CAUTION")

    # top-level files directly in /sdcard
    if path.startswith("/sdcard/") and path.count("/") == 2 and kind == "file":
        return {"allowed": False, "hard_block": False, "reason": "存储根目录下的散落文件不自动处理", "risk_floor": "DANGEROUS"}

    # top-level user directory itself
    for root in USER_MEDIA_ROOTS + CHAT_ROOTS:
        if path == root:
            return {"allowed": False, "hard_block": False, "reason": "用户目录本身不允许删除", "risk_floor": "DANGEROUS"}

    # empty dirs: only below depth 1 and not app roots
    if category == "empty_dirs":
        if kind != "dir":
            return {"allowed": False, "hard_block": False, "reason": "空目录清理只接受目录", "risk_floor": "DANGEROUS"}
        if path.count("/") < 3:
            return {"allowed": False, "hard_block": False, "reason": "顶层目录不参与空目录清理", "risk_floor": "DANGEROUS"}
        risk_floor = risk_max(risk_floor, "CAUTION")

    # .trash / recycle always caution
    if is_trash_path(path):
        risk_floor = risk_max(risk_floor, "CAUTION")

    # category-specific minimum risk
    risk_floor = risk_max(risk_floor, category_risk(category))
    if isinstance(config, dict):
        extra = config.get("extra_protected_paths") or []
        for prefix in extra:
            try:
                if is_under(path, normalize_device_path(str(prefix))):
                    return {"allowed": False, "hard_block": True, "reason": f"用户配置的额外保护路径：{prefix}", "risk_floor": "DANGEROUS"}
            except Exception:  # noqa: BLE001
                continue

    return {"allowed": True, "hard_block": False, "reason": "", "risk_floor": risk_floor}


def category_risk_allowed(category: str) -> bool:
    return category in CATEGORY_META and category != "protected"


def is_media_file(name: str) -> bool:
    return ext_of(name) in MEDIA_EXTS


def is_apk_file(name: str) -> bool:
    return ext_of(name) in APK_EXTS


def is_archive_file(name: str) -> bool:
    return ext_of(name) in ARCHIVE_EXTS


def is_temp_file(name: str) -> bool:
    ext = ext_of(name)
    return ext in TEMP_EXTS or ext in LOG_EXTS


def is_screenshot(name: str) -> bool:
    return bool(SCREENSHOT_RE.search(name))


def is_ota_file(name: str) -> bool:
    return bool(OTA_RE.search(name))


def is_media_dir_component(path: str) -> bool:
    return any(part.lower() in CHAT_MEDIA_COMPONENTS for part in normalize_device_path(path).split("/") if part)


def risk_of(path: str, category: str) -> str:
    guard = guard_path(path, category)
    return guard.get("risk_floor", "DANGEROUS") if guard.get("allowed") else "DANGEROUS"


def describe_app(package: str) -> str:
    info = app_info(package)
    return f"{info['label']} ({package})" if info["label"] != package else package
