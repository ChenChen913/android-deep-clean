"""Unit tests: guard_path safety policy (the single most safety-critical rule set)."""

from __future__ import annotations

import pytest

from adc_lib.models import PackageIndex
from adc_lib.rules import (
    app_dir_kind,
    guard_path,
    is_chat_path,
    is_protected_filename,
    is_system_path,
    is_user_media_path,
    package_from_path,
)

PACKAGES = PackageIndex(
    installed={"com.installed.app"},
    uninstalled={"com.gone.app"},
    system={"android"},
)


def evaluate(path: str, category: str, *, kind: str = "file", config=None):
    return guard_path(path, category, kind=kind, packages=PACKAGES, config=config)


def test_app_cache_is_allowed_and_safe():
    result = evaluate("/sdcard/Android/data/com.installed.app/cache/a.bin", "app_cache")
    assert result["allowed"] is True
    assert result["hard_block"] is False
    assert result["risk_floor"] == "SAFE"


def test_internal_cache_exception_for_data_data():
    result = evaluate("/data/data/com.installed.app/cache/x.bin", "app_cache")
    assert result["allowed"] is True
    assert result["risk_floor"] == "SAFE"

    # everything else inside app-private data is hard protected
    blocked = evaluate("/data/data/com.installed.app/databases/user.db", "app_cache")
    assert blocked["allowed"] is False and blocked["hard_block"] is True
    blocked = evaluate("/data/data/com.installed.app/files/config.xml", "app_cache")
    assert blocked["allowed"] is False


def test_system_paths_are_hard_blocked():
    for path in (
        "/system/bin/sh",
        "/vendor/lib64/libc.so",
        "/product/etc/x",
        "/apex/com.android.runtime/bin/x",
        "/data/system/packages.xml",
        "/data/app/com.x/base.apk",
        # boot/verified-boot images are real Android system files, not directories
        "/boot.img",
        "/dtbo.img",
        "/vbmeta.img",
        "/vbmeta_system.img",
        "/recovery.img",
    ):
        result = evaluate(path, "large_old")
        assert result["allowed"] is False, path
        assert result["hard_block"] is True, path


def test_sensitive_files_are_protected():
    for name in ("app.db", "music.sqlite", "key.keystore", "release.jks", "secret.pem", ".nomedia"):
        path = "/sdcard/Android/data/com.installed.app/cache/" + name
        result = evaluate(path, "app_cache")
        assert result["allowed"] is False, name
        assert result["hard_block"] is True, name


def test_protected_dir_components_are_blocked():
    result = evaluate("/sdcard/Downloads/shared_prefs/prefs.xml", "download_old")
    assert result["allowed"] is False
    assert is_protected_filename("x.db") is True
    assert is_protected_filename("clip.mp4") is False


def test_storage_roots_and_android_roots_are_blocked():
    for path in ("/sdcard", "/storage/emulated/0", "/sdcard/Android", "/sdcard/Android/data", "/sdcard/Android/obb"):
        result = evaluate(path, "app_cache", kind="dir")
        assert result["allowed"] is False, path


def test_quarantine_area_is_blocked():
    result = evaluate("/sdcard/.android-deep-clean/quarantine/x/files/a.bin", "temp_logs")
    assert result["allowed"] is False
    assert "隔离区" in result["reason"]


def test_installed_app_obb_and_media_are_protected():
    assert evaluate("/sdcard/Android/obb/com.installed.app/main.obb", "residue")["allowed"] is False
    assert evaluate("/sdcard/Android/media/com.installed.app/video.mp4", "residue")["allowed"] is False
    assert evaluate("/sdcard/Android/data/com.installed.app/files/data.bin", "residue")["allowed"] is False


def test_uninstalled_app_residue_is_allowed_with_caution():
    for path, kind in (
        ("/sdcard/Android/data/com.gone.app/cache/left.bin", "file"),
        ("/sdcard/Android/obb/com.gone.app/main.obb", "file"),
        ("/sdcard/Android/media/com.gone.app/download.bin", "file"),
    ):
        result = evaluate(path, "residue", kind=kind)
        assert result["allowed"] is True, path
        assert result["risk_floor"] == "CAUTION", path


def test_chat_media_is_protected_but_chat_cache_allowed():
    for media in (
        "/sdcard/tencent/MicroMsg/abc/image2/keep.jpg",
        "/sdcard/tencent/MicroMsg/abc/video2/keep.mp4",
        "/sdcard/tencent/MicroMsg/abc/voice2/keep.silk",
        "/sdcard/tencent/MicroMsg/abc/msg/chat.db",
    ):
        result = evaluate(media, "chat_cache")
        assert result["allowed"] is False, media

    cache = evaluate("/sdcard/tencent/MicroMsg/abc/cache/wx.bin", "chat_cache")
    assert cache["allowed"] is True
    assert cache["risk_floor"] == "CAUTION"
    assert is_chat_path("/sdcard/tencent/MicroMsg/abc/cache/wx.bin") is True


def test_user_media_directories_raise_risk_and_plain_files_are_blocked_from_cache_categories():
    result = evaluate("/sdcard/DCIM/Camera/photo.jpg", "duplicates")
    assert result["allowed"] is True
    assert result["risk_floor"] == "CAUTION"

    blocked = evaluate("/sdcard/DCIM/Camera/photo.jpg", "app_cache")
    assert blocked["allowed"] is False
    assert is_user_media_path("/sdcard/DCIM/Camera/photo.jpg") is True


def test_top_level_loose_files_are_protected():
    result = evaluate("/sdcard/random.bin", "download_old")
    assert result["allowed"] is False
    assert "散落" in result["reason"]


def test_empty_dirs_require_dir_kind_and_depth():
    file_result = evaluate("/sdcard/Misc/EmptyDir", "empty_dirs", kind="file")
    assert file_result["allowed"] is False

    dir_result = evaluate("/sdcard/Misc/EmptyDir", "empty_dirs", kind="dir")
    assert dir_result["allowed"] is True
    assert dir_result["risk_floor"] == "CAUTION"

    top = evaluate("/sdcard/Misc", "empty_dirs", kind="dir")
    assert top["allowed"] is False


def test_unknown_categories_are_rejected():
    result = evaluate("/sdcard/Misc/x.bin", "not_a_category")
    assert result["allowed"] is False
    result = evaluate("/sdcard/Misc/x.bin", "protected")
    assert result["allowed"] is False


def test_extra_protected_paths_config():
    config = {"extra_protected_paths": ["/sdcard/Private"]}
    result = evaluate("/sdcard/Private/data.bin", "download_old", config=config)
    assert result["allowed"] is False
    assert result["hard_block"] is True
    assert "额外保护" in result["reason"]


def test_invalid_paths_fail_closed():
    result = guard_path("/sdcard/../system/bin/sh", "app_cache")
    assert result["allowed"] is False
    assert result["hard_block"] is True


def test_package_and_path_helpers():
    assert package_from_path("/sdcard/Android/data/com.installed.app/cache/a") == "com.installed.app"
    assert package_from_path("/sdcard/Android/obb/com.x/main.obb") == "com.x"
    assert package_from_path("/data/data/com.y/cache/a") == "com.y"
    assert package_from_path("/sdcard/DCIM/a.jpg") == ""
    assert app_dir_kind("/sdcard/Android/data/com.x/cache") == "data"
    assert app_dir_kind("/sdcard/Android/obb/com.x") == "obb"
    assert app_dir_kind("/sdcard/DCIM") == ""
    assert is_system_path("/system/bin/sh") is True
    assert is_system_path("/sdcard/DCIM/a.jpg") is False
    assert is_protected_filename(".nomedia.tmp") is True
