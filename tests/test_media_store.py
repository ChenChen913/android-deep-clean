"""MediaStore auxiliary source: read-only, evidence only, never a candidate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adc_lib.media_store import derive_media_path, parse_content_rows

CAMERA = "/sdcard/DCIM/Camera"


def _row(
    media_id: int,
    path: str,
    *,
    size: int,
    name: str | None = None,
    mime: str = "image/jpeg",
    **overrides,
) -> dict:
    payload = {
        "_id": media_id,
        "_display_name": name or path.rsplit("/", 1)[-1],
        "_size": size,
        "date_modified": 1_700_000_000,
        "mime_type": mime,
        "_data": path,
        "datetaken": 1_699_999_000_000,
        "relative_path": path.rsplit("/", 1)[0].replace("/sdcard/", "") + "/",
        "volume_name": "external_primary",
        "width": 4000,
        "height": 3000,
        "is_pending": 0,
        "is_trashed": 0,
        "media_type": 1,
    }
    payload.update(overrides)
    return payload


def _manifest(env) -> dict:
    return json.loads(env.state_path("scan/aux/sources.json").read_text(encoding="utf-8"))


def _records(env) -> list[dict]:
    path = env.state_path("scan/aux/media-index.jsonl")
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _signature(env) -> list[tuple]:
    return sorted(
        (item["id"], item["category"], item["risk"], item["selectable"],
         item["file_count"], item["total_bytes"], item["files_digest"])
        for item in env.analysis.get("items") or []
    )


def test_content_query_parser_handles_commas_nulls_and_truncation():
    text = (
        "Row: 0 _id=7, _display_name=My, photo.jpg, _size=12, mime_type=image/jpeg\n"
        "Row: 1 _id=8, _display_name=null, _size=null\n"
        "Row: 2 _id=9, _display_name\n"
        "not a row line\n"
    )
    rows = parse_content_rows(text)
    assert len(rows) == 2
    assert rows[0]["_display_name"] == "My, photo.jpg"
    assert rows[1]["_display_name"] is None
    assert rows[1]["_size"] is None


def test_media_path_derivation_from_data_and_volume():
    assert derive_media_path({"_data": "/storage/emulated/0/DCIM/Camera/a.jpg"}) == CAMERA + "/a.jpg"
    assert derive_media_path({
        "volume_name": "external_primary",
        "relative_path": "DCIM/Camera/",
        "_display_name": "a.jpg",
    }) == CAMERA + "/a.jpg"
    assert derive_media_path({
        "volume_name": "1234-5678",
        "relative_path": "Music/",
        "_display_name": "a.mp3",
    }) == "/storage/1234-5678/Music/a.mp3"
    assert derive_media_path({"_display_name": "no-path.jpg"}) == ""


def test_projection_falls_back_when_optional_column_is_rejected(env):
    env.phone.file(CAMERA + "/a.jpg", size=300)
    env.set_media_store(
        images=[_row(1, "/storage/emulated/0/DCIM/Camera/a.jpg", size=300)],
        reject_columns={"images": ["is_pending"]},
    )
    env.ok("doctor")
    meta = env.ok("scan").json()
    manifest = _manifest(env)
    images = next(item for item in manifest["collections"] if item["name"] == "images")
    assert images["status"] == "ok"
    assert images["projection_tier"] == "safe"
    assert images["attempts"] >= 2
    assert manifest["status"] == "collected"
    assert meta["aux"]["media_store"]["status"] == "collected"

    env.ok("analyze")
    analysis = env.analysis
    assert analysis["summary"]["media_index"]["crosscheck"]["matched"] == 1


def test_duplicate_path_is_merged_across_media_collections(env):
    path = "/storage/emulated/0/DCIM/Camera/same.jpg"
    env.phone.file("/sdcard/DCIM/Camera/same.jpg", size=300)
    env.set_media_store(
        images=[_row(1, path, size=300)],
        downloads=[_row(2, path, size=300)],
    )
    env.ok("doctor")
    env.ok("scan")
    records = _records(env)
    assert len(records) == 1
    assert records[0]["collections"] == ["downloads", "images"]
    assert records[0]["path"] == "/sdcard/DCIM/Camera/same.jpg"


def test_media_path_case_difference_is_matched(env):
    env.phone.file("/sdcard/Tencent/MicroMsg/a.jpg", size=300)
    env.set_media_store(images=[_row(
        1, "/storage/emulated/0/tencent/MicroMsg/a.jpg", size=300,
    )])
    analysis = env.prepare_analysis()
    cross = analysis["summary"]["media_index"]["crosscheck"]
    assert cross["matched"] == 1
    assert cross["index_only"] == 0
    assert cross["case_mismatch"] == 1
    assert cross["scan_only"] == 0


def test_subset_scan_does_not_dilute_match_ratio(env):
    """A Download-only scan must not look like a media-index failure."""
    env.phone.file("/sdcard/Download/a.jpg", size=300)
    env.phone.file("/sdcard/DCIM/Camera/b.jpg", size=400)
    env.set_media_store(images=[
        _row(1, "/storage/emulated/0/Download/a.jpg", size=300),
        _row(2, "/storage/emulated/0/DCIM/Camera/b.jpg", size=400),
    ])
    env.ok("doctor")
    env.ok("scan", "--roots", "/sdcard/Download")
    analysis = env.ok("analyze").json()
    cross = analysis["summary"]["media_index"]["crosscheck"]
    assert cross["matched"] == 1
    assert cross["index_only_in_scope"] == 0
    assert cross["index_only_outside_scope"] == 1
    assert cross["index_in_scope"] == 1
    assert cross["match_ratio"] == 1.0
    assert cross["index_scope_ratio"] == 0.5


def test_media_store_crosscheck_and_report(env):
    env.phone.file(CAMERA + "/old.jpg", size=400)
    env.phone.file(CAMERA + "/mismatch.jpg", size=300)
    env.phone.file("/sdcard/Download/scan-only.jpg", size=500)
    env.set_media_store(images=[
        _row(1, "/storage/emulated/0/DCIM/Camera/old.jpg", size=400),
        _row(2, "/storage/emulated/0/DCIM/Camera/stale.jpg", size=123),
        _row(3, "/storage/emulated/0/DCIM/Camera/mismatch.jpg", size=999),
        _row(4, "/storage/emulated/0/DCIM/Camera/trash.jpg", size=100, is_trashed=1),
        _row(5, "", size=50, name="pending.jpg", _data=None, volume_name=None,
             relative_path=None, datetaken=None, is_pending=1),
    ])
    analysis = env.prepare_analysis()

    media = analysis["summary"]["media_index"]
    assert media["status"] == "collected"
    assert media["index"]["items"] == 5
    assert media["index"]["path_known"] == 4
    assert media["index"]["trashed"] == 1
    assert media["index"]["pending"] == 1
    cross = media["crosscheck"]
    assert cross["matched"] == 1
    assert cross["size_mismatch"] == 1
    assert cross["index_only"] == 2
    assert cross["index_only_in_scope"] == 2
    assert cross["path_unknown"] == 1
    assert cross["scan_only"] == 1
    assert cross["match_ratio"] == 0.5
    assert any(item["path"] == CAMERA + "/stale.jpg" for item in cross["samples"]["index_only"])

    conn = env.analysis_db_connection()
    try:
        statuses = dict(conn.execute(
            "SELECT cross_status, COUNT(*) FROM media_items GROUP BY cross_status"
        ).fetchall())
        sources = dict(conn.execute("SELECT name, status FROM source_status").fetchall())
    finally:
        conn.close()
    assert statuses["matched"] == 1
    assert statuses["size_mismatch"] == 1
    assert statuses["index_only"] == 2
    assert statuses["path_unknown"] == 1
    assert sources["media_store"] == "collected"

    item = env.require_item("media_old")
    codes = {entry["code"] for entry in item["evidence"]}
    assert "media_index" in codes
    assert any(
        "媒体索引" in entry.get("text", "") and "2/2" in entry.get("text", "")
        for entry in item["evidence"]
    )
    report_text = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "媒体索引核对（第二数据源）" in report_text
    assert "不能授权删除" in report_text
    assert "不计入文件扫描合计" in report_text
    assert "索引有、文件扫描无（本轮范围内）" in report_text


def test_media_index_never_changes_candidates(env):
    env.phone.file(CAMERA + "/old.jpg", size=400)
    env.phone.file(CAMERA + "/mismatch.jpg", size=300)
    env.prepare_analysis()
    baseline = _signature(env)
    before = env.snapshot()

    env.set_media_store(images=[
        _row(1, "/storage/emulated/0/DCIM/Camera/old.jpg", size=400),
        _row(2, "/storage/emulated/0/DCIM/Camera/mismatch.jpg", size=999),
        _row(3, "/storage/emulated/0/DCIM/Camera/stale.jpg", size=100),
    ])
    env.ok("scan")
    env.ok("analyze")
    after = _signature(env)
    assert baseline == after
    env.assert_unchanged(before)
    assert not [item for item in env.analysis["items"] if item["category"] == "media_index"]


def test_no_media_skips_collection_but_analysis_still_runs(env):
    env.phone.file(CAMERA + "/a.jpg", size=300)
    env.set_media_store(images=[_row(1, "/storage/emulated/0/DCIM/Camera/a.jpg", size=300)])
    env.ok("doctor")
    env.ok("scan", "--no-media")
    manifest = _manifest(env)
    assert manifest["status"] == "disabled"
    assert manifest["items"] == 0
    assert _records(env) == []

    analysis = env.ok("analyze").json()
    assert analysis["summary"]["media_index"]["status"] == "disabled"
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "媒体索引核对（第二数据源）" in report
    assert "本轮未采集" in report


def test_media_store_zero_rows_with_scan_files_is_flagged(env):
    for index in range(10):
        env.phone.file(CAMERA + f"/photo-{index}.jpg", size=300 + index)
    env.set_media_store(images=[], video=[], audio=[], downloads=[])
    env.prepare_analysis()

    manifest = _manifest(env)
    images = next(item for item in manifest["collections"] if item["name"] == "images")
    assert images["status"] == "zero_mismatch"
    assert images["scan_files"] >= 10
    assert manifest["status"] == "partial"
    assert "与文件扫描不一致" in manifest["reason"]
    assert manifest["crosscheck"]["zero_mismatch"] == ["images"]
    assert any("不能据此认为媒体库为空" in item for item in manifest["limitations"])

    media = env.analysis["summary"]["media_index"]
    assert media["status"] == "partial"
    assert media["collection_crosscheck"]["zero_mismatch"] == ["images"]
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "0 条但与扫描不一致" in report
    assert "零记录异常" in report
    assert "不能认定媒体库为空" in report


def test_media_store_zero_rows_without_scan_files_is_empty_not_failure(env):
    env.set_media_store(images=[], video=[], audio=[], downloads=[])
    env.prepare_analysis()

    manifest = _manifest(env)
    images = next(item for item in manifest["collections"] if item["name"] == "images")
    assert images["status"] == "empty"
    assert images["scan_files"] == 0
    assert manifest["status"] == "collected"
    assert "所有集合均为 0 条" in manifest["reason"]
    assert manifest["available"] is True
    assert manifest["crosscheck"]["zero_mismatch"] == []


def test_one_failed_collection_does_not_hide_others(env):
    env.phone.file(CAMERA + "/a.jpg", size=300)
    env.set_media_store(
        images=[_row(1, "/storage/emulated/0/DCIM/Camera/a.jpg", size=300)],
        reject_columns={"images": ["_id"]},
    )
    env.ok("doctor")
    env.ok("scan")

    manifest = _manifest(env)
    by_name = {item["name"]: item for item in manifest["collections"]}
    assert by_name["images"]["status"] == "failed"
    assert by_name["images"]["exit_code"] != 0
    assert by_name["images"]["command"].startswith("content query")
    assert by_name["images"]["attempts"] >= 3
    assert manifest["status"] == "partial"
    assert manifest["available"] is True



def test_unavailable_media_store_degrades_without_failing(env):
    env.phone.file(CAMERA + "/a.jpg", size=300)
    env.ok("doctor")
    meta = env.ok("scan").json()
    assert meta["aux"]["media_store"]["status"] == "unavailable"
    analysis = env.ok("analyze").json()
    media = analysis["summary"]["media_index"]
    assert media["status"] == "unavailable"
    assert media["available"] is False
    assert any("不可用" in entry for entry in media["limitations"]) or media["reason"]
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "媒体索引核对（第二数据源）" in report
    assert "不可用" in report


def test_harmonyos_next_marks_media_store_unsupported(env):
    env.device.update({
        "platform": "harmonyos_next",
        "sdk": 0,
        "release": "",
        "api_version": "22",
        "os_version": "5.0.0",
        "harmony_version": "5.0.0",
    })
    env.flush()
    env.ok("doctor")
    meta = env.ok("scan").json()
    media = meta["aux"]["media_store"]
    assert media["status"] == "unsupported"
    assert media["available"] is False
    assert "HarmonyOS NEXT" in media["reason"]


def test_recheck_no_media_is_reported_in_new_analysis(env):
    env.phone.file(CAMERA + "/a.jpg", size=300)
    env.set_media_store(images=[_row(1, "/storage/emulated/0/DCIM/Camera/a.jpg", size=300)])
    env.prepare_analysis()
    recheck = env.ok("recheck", "--no-media").json()
    new_analysis = json.loads(Path(recheck["new_analysis_path"]).read_text(encoding="utf-8"))
    assert new_analysis["summary"]["media_index"]["status"] == "disabled"


def test_sort_option_falls_back_without_sort(env):
    env.phone.file(CAMERA + "/a.jpg", size=300)
    env.set_media_store(
        images=[_row(1, "/storage/emulated/0/DCIM/Camera/a.jpg", size=300)],
        reject_sort=True,
    )
    env.ok("doctor")
    env.ok("scan")
    manifest = _manifest(env)
    images = next(item for item in manifest["collections"] if item["name"] == "images")
    assert images["status"] == "ok"
    assert images["rows"] == 1
    assert images["attempts"] >= 2
    assert "--sort" not in images["command"]


# ---------------------------------------------------------------- zero-row matrix
# 0 rows only means "empty" when the filesystem scan found nothing at all.
# A handful of files is inconclusive, not empty and not an anomaly.


@pytest.mark.parametrize(
    "file_count,expected",
    [
        (0, "empty"),
        (1, "zero_unconfirmed"),
        (9, "zero_unconfirmed"),
        (10, "zero_mismatch"),
    ],
)
def test_zero_row_threshold_matrix(env, file_count, expected):
    for index in range(file_count):
        env.phone.file(CAMERA + f"/photo-{index}.jpg", size=300 + index)
    env.set_media_store(images=[], video=[], audio=[], downloads=[])
    env.prepare_analysis()

    manifest = _manifest(env)
    images = next(item for item in manifest["collections"] if item["name"] == "images")
    assert images["scan_files"] == file_count
    assert images["status"] == expected

    crosscheck = manifest["crosscheck"]
    if expected == "empty":
        assert "images" in crosscheck["empty"]
        assert crosscheck["zero_unconfirmed"] == []
    elif expected == "zero_unconfirmed":
        assert crosscheck["zero_unconfirmed"] == ["images"]
        assert crosscheck["zero_mismatch"] == []
        assert manifest["status"] == "collected"
        assert "无法确认为空" in manifest["reason"]
        assert any("既不认定为空" in item for item in manifest["limitations"])
    else:
        assert crosscheck["zero_mismatch"] == ["images"]
        assert manifest["status"] == "partial"


def test_zero_unconfirmed_is_reported_in_markdown_and_html(env):
    env.phone.file(CAMERA + "/only-one.jpg", size=300)
    env.set_media_store(images=[], video=[], audio=[], downloads=[])
    env.prepare_analysis()

    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "0 条（少量文件，无法确认）" in report
    assert "零记录待确认" in report
    html = (env.state_dir / "analysis" / "report-before.html").read_text(encoding="utf-8")
    assert "零记录待确认" in html
    assert "无法确认" in html


# ---------------------------------------------------------------- hidden paths
# MediaStore does not index paths containing a hidden component, so the count
# uses the same rule; the excluded number is disclosed instead of disappearing.


def test_hidden_directory_media_does_not_fake_a_mismatch(env):
    for index in range(10):
        env.phone.file(f"/sdcard/DCIM/.private/photo-{index}.jpg", size=300 + index)
    env.set_media_store(images=[], video=[], audio=[], downloads=[])
    env.prepare_analysis()

    manifest = _manifest(env)
    images = next(item for item in manifest["collections"] if item["name"] == "images")
    assert images["scan_files"] == 0, "隐藏目录的媒体不应计入与 MediaStore 的核对"
    assert images["status"] == "empty", "隐藏目录里的文件不能把 0 条记录放大成 zero_mismatch"
    assert manifest["crosscheck"]["hidden_excluded"]["images"] == 10
    assert any("位于隐藏目录" in item for item in manifest["limitations"])


def test_hidden_exclusions_are_reported_not_silent(env):
    env.phone.file("/sdcard/DCIM/.private/photo.jpg", size=300)
    env.phone.file("/sdcard/DCIM/Camera/visible.jpg", size=300)
    env.set_media_store(
        images=[_row(1, "/storage/emulated/0/DCIM/Camera/visible.jpg", size=300)],
        video=[], audio=[], downloads=[],
    )
    env.prepare_analysis()

    manifest = _manifest(env)
    images = next(item for item in manifest["collections"] if item["name"] == "images")
    assert images["scan_files"] == 1
    assert images["status"] == "ok"
    assert manifest["crosscheck"]["hidden_excluded"]["images"] == 1
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    assert "未参与零记录核对" in report


def test_thumbnails_and_trashed_files_stay_out_of_counts(env):
    env.phone.file("/sdcard/DCIM/.thumbnails/thumb-1.jpg", size=100)
    env.phone.file("/sdcard/DCIM/Camera/.trashed-1699999999-old.jpg", size=100)
    env.phone.file("/sdcard/DCIM/Camera/visible.jpg", size=300)
    env.set_media_store(
        images=[_row(1, "/storage/emulated/0/DCIM/Camera/visible.jpg", size=300)],
        video=[], audio=[], downloads=[],
    )
    env.prepare_analysis()

    images = next(item for item in _manifest(env)["collections"] if item["name"] == "images")
    assert images["scan_files"] == 1
    assert images["status"] == "ok"


def test_empty_status_wording_is_consistent_across_reports(env):
    """The label must say "可参与核对" because hidden paths are out of scope."""
    env.set_media_store(images=[], video=[], audio=[], downloads=[])
    env.prepare_analysis()

    label = "0 条（未发现可参与核对的同类文件）"
    report = (env.state_dir / "analysis" / "report-before.md").read_text(encoding="utf-8")
    html = (env.state_dir / "analysis" / "report-before.html").read_text(encoding="utf-8")
    assert label in report
    assert label in html
    assert "0 条（未发现同类文件）" not in report
    assert "0 条（未发现同类文件）" not in html
