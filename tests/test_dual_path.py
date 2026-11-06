"""Integration tests: dual-path (USB+ADB / PC-side) extensions.

Covers device identity binding, device switching rejection, PC backup
(--backup-to), CSV export, --hash-mode pc and scan history.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path

import pytest

from adc_lib.executor import _recompute_plan_hash, _recompute_selection_hash

from adc_testkit import generated_bytes, journal_entries, quarantine_file_paths

CACHE = "/sdcard/Android/data/com.installed.app/cache"


def _cache_fixture(env, sizes=None):
    sizes = sizes or {"a.bin": 300, "b.bin": 500}
    for name, size in sizes.items():
        env.phone.file(CACHE + "/" + name, size=size)
    return sizes


def _flow(env):
    env.prepare_analysis()
    env.plan_now(None)
    return env.select_all_safe()


# ---------------------------------------------------------------- device identity
def test_device_identity_propagates_through_all_artifacts(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    report = env.ok("doctor").json()
    meta = env.ok("scan").json()
    analysis = env.ok("analyze").json()
    plan = env.plan_now(None)
    selection = env.select_all_safe()

    identities = [
        report["device_identity"],
        meta["device_identity"],
        analysis["device_identity"],
        plan["device_identity"],
        selection["device_identity"],
    ]
    ids = {identity["id"] for identity in identities}
    assert len(ids) == 1
    device_id = ids.pop()
    assert device_id.startswith("device-")
    for identity in identities:
        assert identity["serial"] == "MOCK-ADC-0001"
        assert identity["model"] == "MockPhone"
        assert len(identity["hash_material_sha256"]) == 64

    # hashes keep covering the identity
    assert _recompute_plan_hash(plan) == plan["plan_hash"]
    assert _recompute_selection_hash(selection) == selection["selection_hash"]

    env.execute().expect(0)
    execution = env.execution
    assert execution["device_identity"]["id"] == device_id
    assert execution["device_identity"]["serial"] == "MOCK-ADC-0001"
    assert execution["transport"] == "mock"


def test_device_switch_after_plan_is_rejected(env):
    _cache_fixture(env)
    _flow(env)
    before = env.snapshot()
    original_device = copy.deepcopy(env.device)
    original_id = env.plan["device_identity"]["id"]

    env.device["serial"] = "OTHER-DEVICE-9"
    env.device["fingerprint"] = "other/MockPhone/mock:14/OTHER"
    env.device["model"] = "OtherPhone"
    env.flush()

    result = env.execute()
    result.expect_error(3)
    message = result.stderr
    assert "设备" in message
    assert "不一致" in message or "拒绝" in message

    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)
    # plan/selection still bind the original device
    assert env.plan["device_identity"]["id"] == original_id
    assert env.selection["device_identity"]["id"] == original_id

    # Reconnecting the original phone makes the same token valid again.
    env.device = original_device
    env.flush()
    env.execute().expect(0)
    assert env.execution["device_identity"]["serial"] == "MOCK-ADC-0001"


def test_disconnected_device_blocks_execute(env):
    _cache_fixture(env)
    _flow(env)
    before = env.snapshot()

    env.set_caps(connected=False)
    result = env.execute()
    result.expect_error(3)
    assert "断开" in result.stderr or "不可用" in result.stderr
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)


def test_tampered_device_identity_in_plan_is_rejected(env):
    _cache_fixture(env)
    _flow(env)
    before_plan = copy.deepcopy(env.plan)
    before_selection = copy.deepcopy(env.selection)
    before_tree = env.snapshot()

    tampered_plan = copy.deepcopy(before_plan)
    tampered_plan["device_identity"]["id"] = "device-evil000000"
    env.state_path("plan/plan.json").write_text(json.dumps(tampered_plan, ensure_ascii=False), encoding="utf-8")
    result = env.execute()
    result.expect_error(3)
    assert "plan.json" in result.stderr
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before_tree)

    env.state_path("plan/plan.json").write_text(json.dumps(before_plan, ensure_ascii=False), encoding="utf-8")
    tampered_selection = copy.deepcopy(before_selection)
    tampered_selection["device_identity"]["id"] = "device-evil000000"
    env.state_path("selection/selection.json").write_text(
        json.dumps(tampered_selection, ensure_ascii=False), encoding="utf-8"
    )
    result = env.execute()
    result.expect_error(3)
    assert "selection.json" in result.stderr
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before_tree)


# ---------------------------------------------------------------- PC backup
def test_backup_to_copies_and_verifies_before_quarantine(env):
    sizes = {"a.bin": 300, "b.bin": 500}
    _cache_fixture(env, sizes)
    selection = _flow(env)
    backup_dir = env.tmp / "pc-backup"

    result = env.run("execute", "--token", selection["token"], "--backup-to", str(backup_dir))
    result.expect(0)
    execution = result.json()
    assert execution["status"] == "completed"
    assert execution["backup_files"] == 2
    assert execution["backup_bytes"] == 800
    assert execution["summary"]["backup_files"] == 2
    assert execution["summary"]["backup_bytes"] == 800
    assert execution["backup_dir"] == str(backup_dir.resolve())
    backup_root = Path(execution["backup_root"])
    assert backup_root.parent == backup_dir.resolve()
    assert backup_root.is_dir()

    for name, size in sizes.items():
        source_path = CACHE + "/" + name
        expected = generated_bytes(size, seed=source_path)
        dest = backup_root / "sdcard" / "Android" / "data" / "com.installed.app" / "cache" / name
        assert dest.is_file(), dest
        assert dest.read_bytes() == expected
        assert hashlib.sha256(dest.read_bytes()).hexdigest() == hashlib.sha256(expected).hexdigest()
        # device copy was quarantined after backup
        assert not env.phone.exists(source_path)

    manifest_path = env.state_path("execution/backup-manifest.json")
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["execution_id"] == execution["execution_id"]
    assert manifest["backup_root"] == str(backup_root)
    assert len(manifest["files"]) == 2
    for entry in manifest["files"]:
        name = entry["path"].rsplit("/", 1)[1]
        expected = generated_bytes(sizes[name], seed=entry["path"])
        assert entry["size"] == sizes[name]
        assert entry["sha256"] == hashlib.sha256(expected).hexdigest()
        assert entry["verified_by"] == "sha256"
        assert Path(entry["backup_path"]).is_file()
        assert entry["item"] == execution["items"][0]["id"] or entry["item"]

    quarantined = [entry for entry in journal_entries(env) if entry["event"] == "quarantined"]
    assert len(quarantined) == 2
    for entry in quarantined:
        assert Path(entry["backup_path"]).is_file()
        assert entry["backup_sha256"] == entry["backup_sha256"].lower()
        assert entry["backup_verified_by"] == "sha256"
    assert len(quarantine_file_paths(env)) == 2
    assert env.ok("verify").json()["summary"]["failed"] == 0


def test_backup_to_supports_permanent_delete_mode(env):
    sizes = {"a.bin": 300}
    _cache_fixture(env, sizes)
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe(mode="delete")
    backup_dir = env.tmp / "pc-backup-delete"

    result = env.run(
        "execute",
        "--token",
        selection["token"],
        "--mode",
        "delete",
        "--allow-permanent-delete",
        "--backup-to",
        str(backup_dir),
    )
    result.expect(0)
    execution = result.json()
    assert execution["status"] == "completed"
    assert execution["backup_files"] == 1
    assert execution["summary"]["released_bytes"] == 300
    assert not env.phone.exists(CACHE + "/a.bin")
    backup_root = Path(execution["backup_root"])
    assert (backup_root / "sdcard" / "Android" / "data" / "com.installed.app" / "cache" / "a.bin").is_file()
    assert quarantine_file_paths(env) == []


def test_backup_to_unusable_target_fails_without_cleaning(env):
    _cache_fixture(env)
    selection = _flow(env)
    before = env.snapshot()
    blocked = env.tmp / "not-a-directory"
    blocked.write_text("this is a file, not a backup directory", encoding="utf-8")

    result = env.run("execute", "--token", selection["token"], "--backup-to", str(blocked))
    result.expect_error(3)
    assert "备份" in result.stderr
    assert not env.state_path("execution/execution.json").exists()
    env.assert_unchanged(before)


# ---------------------------------------------------------------- CSV export
def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_export_csv_rows_match_analysis(env):
    _cache_fixture(env)
    env.phone.file("/sdcard/Download/legacy_app.apk", size=1500)
    analysis = env.prepare_analysis()
    out_dir = env.tmp / "export-out"

    manifest = env.ok("export", "--out", str(out_dir)).json()
    assert manifest["analysis_id"] == analysis["analysis_id"]
    items_csv = out_dir / "analysis-items.csv"
    files_csv = out_dir / "analysis-files.csv"
    assert items_csv.exists() and files_csv.exists()
    assert {str(items_csv), str(files_csv)} <= set(manifest["files"])
    assert (out_dir / "export-manifest.json").exists()

    item_rows = _read_csv(items_csv)
    assert len(item_rows) == len(analysis["items"])
    assert {"id", "category", "risk", "action", "primary_path", "total_bytes", "selectable"} <= set(item_rows[0])
    assert {row["id"] for row in item_rows} == {item["id"] for item in analysis["items"]}

    conn = env.analysis_db_connection()
    try:
        expected_file_rows = conn.execute("SELECT COUNT(*) FROM candidate_files").fetchone()[0]
    finally:
        conn.close()
    file_rows = _read_csv(files_csv)
    assert len(file_rows) == expected_file_rows
    assert {"candidate_id", "path", "kind", "role", "size"} <= set(file_rows[0])
    assert {row["path"] for row in file_rows} == env.all_candidate_paths()

    # execution-items.csv only appears after an execution
    assert not (out_dir / "execution-items.csv").exists()
    env.plan_now(None)
    env.select_all_safe()
    env.execute().expect(0)
    env.ok("export", "--out", str(out_dir))
    execution_csv = out_dir / "execution-items.csv"
    assert execution_csv.exists()
    execution_rows = _read_csv(execution_csv)
    assert len(execution_rows) == len(env.execution["items"])
    assert {row["id"] for row in execution_rows} == {item["id"] for item in env.execution["items"]}


def test_export_requires_analysis(env):
    env.run("export").expect_error(5)


def test_export_default_output_dir(env):
    _cache_fixture(env)
    env.prepare_analysis()
    manifest = env.ok("export").json()
    export_dir = env.state_dir / "export"
    assert (export_dir / "analysis-items.csv").exists()
    assert str(export_dir / "analysis-items.csv") in manifest["files"]


# ---------------------------------------------------------------- PC-side hashing
def test_hash_mode_pc_detects_duplicates_without_device_hash_tools(env):
    payload = "PC-HASH-CONTENT-" * 30  # 465 bytes, above 100B threshold
    env.phone.file("/sdcard/DCIM/Camera/pc_dup.jpg", content=payload)
    env.phone.file("/sdcard/Pictures/pc_dup_copy.jpg", content=payload)
    # same size, different bytes must not become a duplicate group
    env.phone.file("/sdcard/Misc/pc_same_a.bin", content="A" * 456)
    env.phone.file("/sdcard/Misc/pc_same_b.bin", content="B" * 456)
    env.set_commands(sha256sum=False, md5sum=False)

    env.ok("doctor")
    env.ok("scan")

    device_analysis = env.ok("analyze").json()
    assert device_analysis["hash_mode"] == "device"
    assert not [item for item in device_analysis["items"] if item["category"] == "duplicates"]
    limitations = "\n".join(device_analysis["limitations"])
    assert "hash-mode pc" in limitations or "缺少 hash" in limitations

    pc_analysis = env.ok("analyze", "--hash-mode", "pc").json()
    assert pc_analysis["hash_mode"] == "pc"
    assert pc_analysis["summary"]["hash_mode"] == "pc"
    duplicates = [item for item in pc_analysis["items"] if item["category"] == "duplicates"]
    assert len(duplicates) == 1
    item = duplicates[0]
    assert item["file_count"] == 1
    assert item["total_bytes"] == len(payload)
    assert "pc_dup.jpg" in item["keep_suggestion"]
    assert "/sdcard/DCIM/Camera/pc_dup.jpg" not in item["sample_paths"]
    assert "/sdcard/Pictures/pc_dup_copy.jpg" in item["sample_paths"]
    all_duplicate_paths = set()
    for group in duplicates:
        all_duplicate_paths |= set(group["sample_paths"])
    assert "/sdcard/Misc/pc_same_a.bin" not in all_duplicate_paths
    assert "/sdcard/Misc/pc_same_b.bin" not in all_duplicate_paths
    # temporary pull-to-PC hash files are cleaned up
    assert not env.state_path("analysis/hash-tmp").exists()


def test_hash_mode_default_can_come_from_config(env):
    payload = "CONFIG-HASH-" * 40
    env.phone.file("/sdcard/DCIM/Camera/cfg_dup_a.jpg", content=payload)
    env.phone.file("/sdcard/DCIM/Camera/cfg_dup_b.jpg", content=payload)
    env.set_commands(sha256sum=False, md5sum=False)
    env.set_config(scan={"hash_mode": "pc"})

    env.ok("doctor")
    env.ok("scan")
    analysis = env.ok("analyze").json()
    assert analysis["hash_mode"] == "pc"
    assert [item for item in analysis["items"] if item["category"] == "duplicates"]


# ---------------------------------------------------------------- scan history
def test_scan_archives_previous_round_and_history_lists_it(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.ok("doctor")
    first = env.ok("scan").json()
    history_dir = env.state_dir / "history"
    assert not history_dir.exists() or not list(history_dir.glob("scan-*"))

    second = env.ok("scan").json()
    archives = sorted(history_dir.glob("scan-*"))
    assert len(archives) == 1
    archive = archives[0]
    for name in ("snapshot.db", "snapshot-meta.json", "scan-summary.md"):
        assert (archive / name).exists(), name
    archived_meta = json.loads((archive / "snapshot-meta.json").read_text(encoding="utf-8"))
    assert archived_meta["stats"] == first["stats"]
    assert archived_meta["device_identity"]["id"] == first["device_identity"]["id"]

    history = env.ok("history").json()
    assert history["state"] == str(env.state_dir.resolve())
    scans = history["scans"]
    assert len(scans) == 2
    kinds = {entry["kind"] for entry in scans}
    assert kinds == {"current", "archived"}
    current = next(entry for entry in scans if entry["kind"] == "current")
    assert current["stats"] == second["stats"]
    archived = next(entry for entry in scans if entry["kind"] == "archived")
    assert archived["stats"] == first["stats"]
    assert archived["scanned_at"] == first["scanned_at"]

    env.ok("scan")
    history2 = env.ok("history").json()
    assert len(history2["scans"]) == 3
    assert sum(1 for entry in history2["scans"] if entry["kind"] == "archived") == 2


def test_history_command_without_scans(env):
    payload = env.ok("history").json()
    assert payload["scans"] == []
    assert payload["state"] == str(env.state_dir.resolve())

# ---------------------------------------------------------------- internal (/data) paths
def test_internal_app_cache_backup_and_quarantine(env):
    """/data/data/<pkg>/cache is only scannable with root; its backup/quarantine
    uses the internal namespace, not /sdcard."""
    env.set_caps(privilege="root", commands={"su": True})
    internal_path = "/data/data/com.installed.app/cache/internal.bin"
    env.phone.file(internal_path, size=321)

    report = env.ok("doctor").json()
    assert report["capabilities"]["internal_data_read"] is True
    env.ok("scan")
    analysis = env.ok("analyze").json()
    items = [item for item in analysis["items"] if item["primary_path"] == "/data/data/com.installed.app/cache"]
    assert len(items) == 1
    env.plan_now(None)
    selection = env.select_all_safe()
    assert any(item["primary_path"] == "/data/data/com.installed.app/cache" for item in selection["items"])

    backup_dir = env.tmp / "pc-backup-internal"
    result = env.run("execute", "--token", selection["token"], "--backup-to", str(backup_dir))
    result.expect(0)
    execution = result.json()
    assert execution["status"] == "completed"
    assert execution["backup_files"] == 1
    assert execution["backup_bytes"] == 321
    backup_root = Path(execution["backup_root"])
    backed_up = backup_root / "data" / "data" / "com.installed.app" / "cache" / "internal.bin"
    assert backed_up.is_file(), backed_up
    assert backed_up.read_bytes() == generated_bytes(321, seed=internal_path)

    assert not env.phone.exists(internal_path)
    journal = [entry for entry in journal_entries(env) if entry["event"] == "quarantined"]
    assert len(journal) == 1
    assert journal[0]["quarantine_path"].startswith("/data/local/tmp/.android-deep-clean/quarantine/")
    assert env.phone.exists(journal[0]["quarantine_path"])
    assert env.ok("verify").json()["summary"]["failed"] == 0


# ---------------------------------------------------------------- backup + cleanup failure
def test_backup_succeeds_but_denied_cleanup_keeps_both_copies(env):
    path = CACHE + "/a.bin"
    env.phone.file(path, size=300)
    env.set_caps(denied_ops=["move:" + path])
    env.prepare_analysis()
    env.plan_now(None)
    selection = env.select_all_safe()
    backup_dir = env.tmp / "pc-backup-denied"

    result = env.run("execute", "--token", selection["token"], "--backup-to", str(backup_dir))
    result.expect_error(6)
    execution = env.execution
    assert execution["status"] == "partial"
    assert execution["backup_files"] == 1
    assert execution["summary"]["quarantine_files"] == 0
    # source still on the phone and a verified PC copy exists
    assert env.phone.exists(path)
    backup_root = Path(execution["backup_root"])
    assert (backup_root / "sdcard" / "Android" / "data" / "com.installed.app" / "cache" / "a.bin").is_file()


# ---------------------------------------------------------------- history + identity
def test_scan_history_records_each_device_identity(env):
    env.phone.file(CACHE + "/a.bin", size=300)
    env.ok("doctor")
    env.ok("scan")
    env.device["serial"] = "SECOND-PHONE-2"
    env.device["fingerprint"] = "second/fp"
    env.flush()
    env.ok("scan")

    history = env.ok("history").json()
    assert len(history["scans"]) == 2
    current, archived = history["scans"][0], history["scans"][1]
    assert current["kind"] == "current"
    assert archived["kind"] == "archived"
    assert current["device_identity"]["serial"] == "SECOND-PHONE-2"
    assert archived["device_identity"]["serial"] == "MOCK-ADC-0001"
    assert current["device_identity"]["id"] != archived["device_identity"]["id"]