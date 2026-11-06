"""Unit tests: util helpers, stat-line parsing, config loading."""

from __future__ import annotations

import hashlib

import pytest

from adc_lib.config import DEFAULT_CONFIG, load_config, threshold
from adc_lib.transport import parse_stat_line
from adc_lib.util import (
    SafetyError,
    ValidationError,
    atomic_write_json,
    canonical_json,
    display_path,
    ext_of,
    human_bytes,
    is_under,
    json_loads,
    normalize_device_path,
    parse_age_days,
    parse_size,
    read_json,
    risk_le,
    risk_max,
    sha256_file,
    sha256_text,
    shell_quote,
    split_csv,
    sqlite_int64,
)


# ---------------------------------------------------------------- stat lines
def test_unsigned_64bit_stat_values_fit_sqlite():
    assert sqlite_int64(123) == 123
    assert sqlite_int64(2**63) == -(2**63)
    assert sqlite_int64(2**64 - 1) == -1
    line = "81a4|1|1700000000|1700000100|1700000200|18446744073709551615|18446744073709551615|1|644|1000|/sdcard/x"
    entry = parse_stat_line(line)
    assert entry is not None
    assert entry.inode == -1
    assert entry.dev == -1


def test_parse_stat_line_file():
    line = "81a4|1234|1700000000|1700000100|1700000200|555|2049|1|644|1000|/sdcard/DCIM/Camera/a b.jpg"
    entry = parse_stat_line(line)
    assert entry is not None
    assert entry.path == "/sdcard/DCIM/Camera/a b.jpg"
    assert entry.parent == "/sdcard/DCIM/Camera"
    assert entry.name == "a b.jpg"
    assert entry.kind == "file"
    assert entry.size == 1234
    assert entry.mtime == 1700000000
    assert entry.atime == 1700000100
    assert entry.ctime == 1700000200
    assert entry.inode == 555
    assert entry.dev == 2049
    assert entry.nlink == 1
    assert entry.uid == 1000
    assert entry.ext == ".jpg"


def test_parse_stat_line_dir_and_symlink_modes():
    dir_line = "41ed|4096|1700000000|1700000000|1700000000|1|1|2|755|0|/sdcard/DCIM"
    entry = parse_stat_line(dir_line)
    assert entry is not None and entry.kind == "dir"

    link_line = "a1ff|7|1700000000|1700000000|1700000000|2|1|1|777|0|/sdcard/link"
    entry = parse_stat_line(link_line)
    assert entry is not None and entry.kind == "symlink"


def test_parse_stat_line_path_may_contain_pipes():
    line = "81a4|10|1|2|3|4|5|1|644|0|/sdcard/Download/a|b.bin"
    entry = parse_stat_line(line)
    assert entry is not None
    assert entry.path == "/sdcard/Download/a|b.bin"
    assert entry.name == "a|b.bin"


@pytest.mark.parametrize(
    "line",
    [
        "",
        "\n",
        "garbage",
        "81a4|1|2|3|4|5|6|7|644|0",
        "ZZZZ|1|2|3|4|5|6|7|644|0|/sdcard/x",
        "81a4|1|2|3|4|5|6|7|644|0|relative/path",
    ],
)
def test_parse_stat_line_rejects_malformed(line):
    assert parse_stat_line(line) is None


# ---------------------------------------------------------------- quoting
def test_shell_quote_posix_single_quote_escaping():
    bt = "\x60"  # backtick, kept as an escape so this test source stays ASCII-safe
    assert shell_quote("plain") == "'plain'"
    assert shell_quote("") == "''"
    assert shell_quote("a b") == "'a b'"
    assert shell_quote("it's") == "'it'\\''s'"
    assert shell_quote("$(rm -rf /)") == "'$(rm -rf /)'"
    assert shell_quote(bt + "whoami" + bt) == "'" + bt + "whoami" + bt + "'"
    assert shell_quote(123) == "'123'"


# ---------------------------------------------------------------- path normalization
def test_normalize_device_path_rules():
    assert normalize_device_path("/sdcard//DCIM/./a.jpg") == "/sdcard/DCIM/a.jpg"
    assert normalize_device_path("/sdcard/") == "/sdcard"
    with pytest.raises(ValidationError):
        normalize_device_path("relative/path")
    with pytest.raises(ValidationError):
        normalize_device_path("")
    with pytest.raises(SafetyError):
        normalize_device_path("/sdcard/../etc/passwd")
    with pytest.raises(SafetyError):
        normalize_device_path("/sdcard/x\x00y")


def test_display_path_maps_mount_aliases():
    assert display_path("/sdcard/DCIM/a.jpg") == "/sdcard/DCIM/a.jpg"
    assert display_path("/storage/emulated/0/DCIM/a.jpg") == "/sdcard/DCIM/a.jpg"
    assert display_path("/storage/self/primary") == "/sdcard"
    assert display_path("/mnt/sdcard/Download/x.apk") == "/sdcard/Download/x.apk"
    assert display_path("/data/data/com.x/cache/a") == "/data/data/com.x/cache/a"


def test_is_under_boundaries():
    assert is_under("/sdcard/DCIM/a", "/sdcard/DCIM") is True
    assert is_under("/sdcard/DCIM", "/sdcard/DCIM") is True
    assert is_under("/sdcard/DCIMX/a", "/sdcard/DCIM") is False
    assert is_under("/sdcard/DCIM/a", "/sdcard") is True
    assert is_under("/data", "/sdcard") is False


# ---------------------------------------------------------------- misc helpers
def test_human_bytes_parse_size_ext_split():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1023) == "1023 B"
    assert human_bytes(1024) == "1.00 KB"
    assert human_bytes(1024 * 1024) == "1.00 MB"
    assert human_bytes(None) == "-"
    assert human_bytes(-1) == "-"

    assert parse_size("10") == 10
    assert parse_size("1KB") == 1024
    assert parse_size("1.5 MB") == int(1.5 * 1024 * 1024)
    assert parse_size("2G") == 2 * 1024**3
    with pytest.raises(Exception):
        parse_size("not-a-size")

    assert ext_of("A.JPG") == ".jpg"
    assert ext_of("noext") == ""
    assert ext_of(".hidden") == ".hidden"

    assert split_csv("a, b,,c") == ["a", "b", "c"]
    assert split_csv(None) == []
    assert split_csv(["x ", " y"]) == ["x", "y"]


def test_risk_helpers():
    assert risk_max("SAFE", "CAUTION") == "CAUTION"
    assert risk_max(None, "SAFE") == "SAFE"
    assert risk_max() == "SAFE"
    assert risk_le("SAFE", "CAUTION") is True
    assert risk_le("DANGEROUS", "CAUTION") is False


def test_canonical_json_and_sha256_helpers(tmp_path):
    assert canonical_json({"b": 1, "a": [2, 3]}) == '{"a":[2,3],"b":1}'
    assert sha256_text("abc") == hashlib.sha256(b"abc").hexdigest()

    target = tmp_path / "blob.bin"
    payload = bytes(range(200)) * 100
    target.write_bytes(payload)
    assert sha256_file(target) == hashlib.sha256(payload).hexdigest()

    json_path = tmp_path / "data.json"
    atomic_write_json(json_path, {"k": "值"})
    assert read_json(json_path) == {"k": "值"}
    assert read_json(tmp_path / "missing.json", default={"d": 1}) == {"d": 1}
    with pytest.raises(Exception):
        json_loads("{not json}")


def test_parse_age_days():
    assert parse_age_days(None) is None
    assert parse_age_days(0, now=86400) == pytest.approx(1.0)
    assert parse_age_days(86400 * 2, now=86400) == 0.0  # future clamped to 0


# ---------------------------------------------------------------- config
def test_load_config_defaults_and_merge(tmp_path):
    config = load_config()
    assert config["thresholds"]["min_duplicate_bytes"] == DEFAULT_CONFIG["thresholds"]["min_duplicate_bytes"]
    assert threshold(config, "large_file_bytes") == 100 * 1024 * 1024

    override = tmp_path / "config.json"
    override.write_text('{"thresholds": {"min_duplicate_bytes": 5}, "extra_protected_paths": ["/sdcard/Private"]}', encoding="utf-8")
    merged = load_config(str(override))
    assert merged["thresholds"]["min_duplicate_bytes"] == 5
    # untouched defaults survive a deep merge
    assert merged["thresholds"]["old_file_days"] == DEFAULT_CONFIG["thresholds"]["old_file_days"]
    assert merged["extra_protected_paths"] == ["/sdcard/Private"]

    with pytest.raises(ValidationError):
        load_config(str(tmp_path / "does-not-exist.json"))


@pytest.mark.parametrize(
    "payload",
    [
        [1, 2, 3],
        '{"thresholds": 5}',
        '{"scan": {"max_files": -1}}',
        '{"scan": {"max_files": "many"}}',
        '{"extra_protected_paths": "nope"}',
    ],
)
def test_load_config_rejects_invalid_shapes(tmp_path, payload):
    import json as json_module

    path = tmp_path / "bad.json"
    path.write_text(payload if isinstance(payload, str) else json_module.dumps(payload), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(str(path))


def test_threshold_unknown_key_raises():
    config = load_config()
    with pytest.raises(ValidationError):
        threshold(config, "no_such_threshold")
