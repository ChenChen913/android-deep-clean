"""Configuration defaults and merge logic.

Only stdlib is used. A user config is a JSON object deep-merged over
DEFAULT_CONFIG; list values are replaced, not concatenated.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from .util import ValidationError, read_json

DEFAULT_CONFIG: dict[str, Any] = {
    "schema": 1,
    "thresholds": {
        "min_duplicate_bytes": 1024 * 1024,
        "large_file_bytes": 100 * 1024 * 1024,
        "old_file_days": 365,
        "apk_old_days": 180,
        "archive_old_days": 180,
        "temp_old_days": 30,
        "log_old_days": 90,
        "screenshot_old_days": 180,
        "media_old_days": 730,
        "trash_old_days": 30,
        "empty_dir_old_days": 30,
        "download_old_days": 180,
        "chat_cache_old_days": 14,
        "browser_cache_old_days": 14,
        "min_archive_bytes": 10 * 1024 * 1024,
        "min_download_bytes": 5 * 1024 * 1024,
        "min_media_bytes": 50 * 1024 * 1024,
        "large_cache_caution_bytes": 1024 * 1024 * 1024,
    },
    "scan": {
        "max_files": 750000,
        "max_depth": 64,
        "max_hash_files": 5000,
        "max_hash_total_bytes": 4 * 1024 * 1024 * 1024,
        "max_candidates": 600,
        "max_duplicate_groups": 150,
        "max_large_files": 200,
        "deadline_seconds": 3600,
        "include_internal": "auto",
    },
    "report": {
        "sample_paths": 12,
        "max_detail_items": 120,
        "top_protected": 15,
    },
    "quarantine": {
        "external_root": "/sdcard/.android-deep-clean/quarantine",
        "internal_root": "/data/local/tmp/.android-deep-clean/quarantine",
    },
    "safety": {
        "leftover_age_hours": 1,
        "max_files_per_selection": 200000,
        "require_ai_review_for_caution": True,
    },
    "exclude_paths": [
        "/sdcard/.android-deep-clean",
        "/sdcard/Android/.android-deep-clean",
    ],
    "extra_protected_paths": [],
}


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = copy.deepcopy(base)
        for key, value in override.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged
    return copy.deepcopy(override)


def load_config(path: str | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    if path:
        raw = read_json(path, default=None)
        if raw is None:
            raise ValidationError(f"config file not found: {path}")
        if not isinstance(raw, dict):
            raise ValidationError("config root must be a JSON object")
        config = _deep_merge(config, raw)
    _validate_config(config)
    return config


def _validate_config(config: dict[str, Any]) -> None:
    for key in ("thresholds", "scan", "report", "quarantine", "safety"):
        if not isinstance(config.get(key), dict):
            raise ValidationError(f"config.{key} must be an object")
    for key in ("max_files", "max_depth", "max_hash_files", "max_candidates"):
        value = config["scan"].get(key)
        if not isinstance(value, int) or value < 0:
            raise ValidationError(f"config.scan.{key} must be a non-negative integer")
    for key in ("sample_paths", "max_detail_items"):
        value = config["report"].get(key)
        if not isinstance(value, int) or value < 0:
            raise ValidationError(f"config.report.{key} must be a non-negative integer")
    if not isinstance(config.get("extra_protected_paths"), list):
        raise ValidationError("config.extra_protected_paths must be a list")


def threshold(config: dict[str, Any], key: str) -> int:
    try:
        return int(config["thresholds"][key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(f"unknown/invalid threshold: {key}") from exc
