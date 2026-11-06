"""Small, dependency-free helpers shared by all ADC modules."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

RISK_ORDER = {"SAFE": 0, "CAUTION": 1, "DANGEROUS": 2}
RISK_LABELS = {"SAFE": "安全", "CAUTION": "谨慎", "DANGEROUS": "危险"}
SQLITE_INT_BITS = 64
SQLITE_INT_MASK = (1 << SQLITE_INT_BITS) - 1
SQLITE_INT_SIGN = 1 << (SQLITE_INT_BITS - 1)


def sqlite_int64(value: Any) -> int | None:
    """Normalize an unsigned 64-bit stat value into SQLite's signed range.

    Windows file IDs (st_ino/st_dev) are unsigned 64-bit and can exceed
    2**63-1, which sqlite3 refuses to bind.  Two's-complement wrapping keeps
    distinct values distinct while staying inside the signed 64-bit range.
    """
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    number &= SQLITE_INT_MASK
    return number - (1 << SQLITE_INT_BITS) if number & SQLITE_INT_SIGN else number


ACTION_LABELS = {
    "delete_files": "隔离/删除文件",
    "clean_dir_children": "清理目录内容",
    "rmdir": "删除空目录",
}


class AdcError(Exception):
    """Base error carrying a stable code and process exit code."""

    code = "error"
    exit_code = 1

    def __init__(self, message: str, *, code: str | None = None, exit_code: int | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if exit_code is not None:
            self.exit_code = exit_code


class UsageError(AdcError):
    code = "usage"
    exit_code = 2


class SafetyError(AdcError):
    code = "safety"
    exit_code = 3


class TransportError(AdcError):
    code = "transport"
    exit_code = 4


class ValidationError(AdcError):
    code = "validation"
    exit_code = 5


class PartialError(AdcError):
    code = "partial"
    exit_code = 6


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def utc_ts_to_iso(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone().isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def human_bytes(num: int | float | None) -> str:
    if num is None:
        return "-"
    try:
        value = float(num)
    except (TypeError, ValueError):
        return "-"
    if value < 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(value)} B"
    return f"{value:.2f} {units[idx]}"


def parse_size(text: str | int | None) -> int:
    if text is None:
        raise UsageError("size value is required")
    if isinstance(text, int):
        return text
    raw = str(text).strip().lower().replace(" ", "")
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(b|kb|k|mb|m|gb|g|tb|t)?", raw)
    if not match:
        raise UsageError(f"invalid size: {text!r} (examples: 500MB, 2GB, 1048576)")
    number = float(match.group(1))
    unit = match.group(2) or "b"
    factor = {
        "b": 1, "kb": 1024, "k": 1024, "mb": 1024**2, "m": 1024**2,
        "gb": 1024**3, "g": 1024**3, "tb": 1024**4, "t": 1024**4,
    }[unit]
    return int(number * factor)


def parse_age_days(mtime: int | float | None, now: float | None = None) -> float | None:
    if mtime is None:
        return None
    current = now if now is not None else datetime.now(timezone.utc).timestamp()
    return max(0.0, (current - float(mtime)) / 86400.0)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *parts: str | None) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return f"{prefix}-{sha256_text(raw)[:10]}"


def random_token(prefix: str = "ADC") -> str:
    return f"{prefix}-{secrets.token_hex(4).upper()}"


def ensure_dir(path: str | os.PathLike[str]) -> str:
    Path(path).mkdir(parents=True, exist_ok=True)
    return str(path)


def atomic_write_text(path: str | os.PathLike[str], text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_write_json(path: str | os.PathLike[str], value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: str | os.PathLike[str], default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {path}: {exc}") from exc


def read_text(path: str | os.PathLike[str], default: str | None = None) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return default


def normalize_device_path(path: str) -> str:
    """Normalize an absolute Android path without resolving symlinks."""
    if not isinstance(path, str) or not path:
        raise ValidationError("empty path")
    if "\x00" in path:
        raise SafetyError("path contains NUL byte")
    if not path.startswith("/"):
        raise ValidationError(f"device path must be absolute: {path!r}")
    if ".." in path.split("/"):
        raise SafetyError(f"path traversal is not allowed: {path!r}")
    normalized = posixpath.normpath(path)
    if normalized.startswith("//"):
        normalized = "/" + normalized.lstrip("/")
    if normalized == "." or not normalized.startswith("/"):
        raise ValidationError(f"invalid device path: {path!r}")
    return normalized


def display_path(path: str) -> str:
    """Map common Android aliases to /sdcard for stable reporting."""
    normalized = normalize_device_path(path)
    for alias in ("/storage/emulated/0", "/storage/self/primary", "/mnt/sdcard"):
        if normalized == alias:
            return "/sdcard"
        if normalized.startswith(alias + "/"):
            return "/sdcard" + normalized[len(alias):]
    return normalized


def is_under(path: str, root: str) -> bool:
    """True when path equals root or is a child of root (POSIX, no symlink resolution)."""
    try:
        p = posixpath.normpath(path)
        r = posixpath.normpath(root)
    except (TypeError, ValueError):
        return False
    if p == r:
        return True
    return p.startswith(r.rstrip("/") + "/")


def shell_quote(value: str) -> str:
    """POSIX single-quote escaping, safe for adb shell / toybox commands."""
    if not isinstance(value, str):
        value = str(value)
    return "'" + value.replace("'", "'\\''") + "'"


def split_csv(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def ext_of(name: str) -> str:
    base = posixpath.basename(name).lower()
    if "." not in base:
        return ""
    return "." + base.rsplit(".", 1)[1]


def safe_local_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return cleaned[:120] or "file"


def md_escape(text: Any) -> str:
    raw = "" if text is None else str(text)
    raw = raw.replace("\r", " ").replace("\n", " ")
    return raw.replace("|", "\\|")


def md_code(text: Any) -> str:
    raw = "" if text is None else str(text)
    raw = raw.replace("\r", " ").replace("\n", " ")
    return "`" + raw.replace("`", "\\`") + "`"


def risk_max(*risks: str | None) -> str:
    values = [r for r in risks if r]
    if not values:
        return "SAFE"
    return max(values, key=lambda r: RISK_ORDER.get(r, 99))


def risk_le(a: str, b: str) -> bool:
    return RISK_ORDER.get(a, 99) <= RISK_ORDER.get(b, 99)


def batch_iter(items: Iterable[Any], size: int = 100) -> Iterable[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def format_dt(ts: int | float | None) -> str:
    if ts is None:
        return "-"
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return "-"


def age_label(days: float | None) -> str:
    if days is None:
        return "-"
    if days < 1:
        return "<1 天"
    if days < 30:
        return f"{int(days)} 天"
    if days < 365:
        return f"{days / 30:.1f} 个月"
    return f"{days / 365:.1f} 年"


def json_loads(text: str, what: str = "JSON") -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid {what}: {exc}") from exc


CANDIDATE_FILE_FIELDS = ("path", "size", "mtime", "inode", "dev", "kind", "role")


def _record_dict(record: Any) -> dict[str, Any]:
    """Normalize dataclass / dict / sqlite3.Row rows into a plain dict."""
    if isinstance(record, dict):
        return record
    to_dict = getattr(record, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    keys = getattr(record, "keys", None)
    if callable(keys):
        return {key: record[key] for key in keys()}
    return dict(record)


def compute_files_digest(files: Iterable[Any]) -> str:
    """Stable digest of a candidate's file manifest.

    The digest binds plan/selection hashes to the exact file rows stored in
    analysis.db, so editing the database cannot silently change what gets
    deleted while keeping the same plan hash.
    """
    records = []
    for item in files:
        data = _record_dict(item)
        records.append({key: data.get(key) for key in CANDIDATE_FILE_FIELDS})
    records.sort(key=lambda row: str(row.get("path") or ""))
    return sha256_text(canonical_json(records))


def plan_hash_payload(plan: dict[str, Any]) -> dict[str, Any]:
    """Canonical fields covered by plan_hash (shared by planner and executor)."""
    payload = [
        {key: item.get(key) for key in (
            "id", "category", "risk", "action", "primary_path",
            "total_bytes", "file_count", "selectable", "files_digest",
        )}
        for item in plan.get("items") or []
    ]
    return {
        "analysis_id": plan.get("analysis_id"),
        "device_identity": (plan.get("device_identity") or {}).get("id", ""),
        "platform": plan.get("platform") or "android",
        "items": payload,
    }


def compute_plan_hash(plan: dict[str, Any]) -> str:
    return sha256_text(canonical_json(plan_hash_payload(plan)))


def selection_hash_payload(selection: dict[str, Any]) -> dict[str, Any]:
    """Canonical fields covered by selection_hash.

    Includes the selected item metadata and the user-facing summary so that a
    tampered confirmation page (sizes/counts/risk) cannot be executed.
    """
    return {
        "plan_hash": selection.get("plan_hash"),
        "device_identity": (selection.get("device_identity") or {}).get("id", ""),
        "platform": selection.get("platform") or "android",
        "mode": selection.get("mode"),
        "items": [
            {key: item.get(key) for key in (
                "id", "category", "risk", "action", "primary_path",
                "total_bytes", "file_count", "selectable", "files_digest",
            )}
            for item in selection.get("items") or []
        ],
        "summary": {
            key: (selection.get("summary") or {}).get(key)
            for key in ("groups", "files", "bytes")
        },
    }


def compute_selection_hash(selection: dict[str, Any]) -> str:
    return sha256_text(canonical_json(selection_hash_payload(selection)))


def safe_display_path(path: Any) -> str:
    """Display a device path without ever raising on malicious/legacy input."""
    raw = "" if path is None else str(path)
    try:
        return display_path(raw)
    except Exception:  # noqa: BLE001
        return raw
