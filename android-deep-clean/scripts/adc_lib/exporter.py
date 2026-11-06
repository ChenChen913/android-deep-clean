"""CSV export for PC-side analysis and audit."""

from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from .state import State
from .util import atomic_write_json, human_bytes, now_iso, read_json


def export_state(state: State, out_dir: str | None = None) -> dict[str, Any]:
    analysis_path = state.require("analysis_json", "分析结果")
    analysis = read_json(analysis_path, default=None) or {}
    db_path = state.get_file("analysis") or (analysis.get("analysis_db_path") or "")
    if not db_path or not Path(db_path).exists():
        from .util import ValidationError

        raise ValidationError("analysis.db 不存在，无法导出文件清单")
    output = Path(out_dir).resolve() if out_dir else state.dir("export")
    output.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []

    items_path = output / "analysis-items.csv"
    with open(items_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "id", "category", "risk", "action", "title", "primary_path",
            "file_count", "dir_count", "total_bytes", "human_size",
            "ai_review_required", "selectable", "app_pkg", "app_kind",
            "keep_suggestion", "sample_paths",
        ])
        for item in analysis.get("items") or []:
            writer.writerow([
                item.get("id", ""), item.get("category", ""), item.get("risk", ""),
                item.get("action", ""), item.get("title", ""), item.get("primary_path", ""),
                item.get("file_count", 0), item.get("dir_count", 0), item.get("total_bytes", 0),
                item.get("human_size", human_bytes(item.get("total_bytes", 0))),
                int(bool(item.get("ai_review_required"))), int(bool(item.get("selectable"))),
                item.get("app_pkg", ""), item.get("app_kind", ""), item.get("keep_suggestion", ""),
                " | ".join(item.get("sample_paths") or []),
            ])
    files.append(items_path)

    files_path = output / "analysis-files.csv"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT c.id AS candidate_id, c.category, c.risk, c.title,
                      f.path, f.kind, f.role, f.size, f.mtime
               FROM candidates c JOIN candidate_files f ON f.candidate_id=c.id
               ORDER BY c.category, c.id, f.path"""
        ).fetchall()
        with open(files_path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["candidate_id", "category", "risk", "title", "path", "kind", "role", "size", "mtime"])
            for row in rows:
                writer.writerow([
                    row["candidate_id"], row["category"], row["risk"], row["title"],
                    row["path"], row["kind"], row["role"], row["size"], row["mtime"],
                ])
        files.append(files_path)
    finally:
        conn.close()

    execution_path = state.get_file("execution")
    if execution_path and Path(execution_path).exists():
        execution = read_json(execution_path, default={}) or {}
        exec_path = output / "execution-items.csv"
        with open(exec_path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["id", "category", "status", "success_count", "failed_count",
                             "processed_bytes", "quarantine_files", "removed_dirs", "error"])
            for item in execution.get("items") or []:
                writer.writerow([
                    item.get("id", ""), item.get("category", ""), item.get("status", ""),
                    item.get("success_count", 0), item.get("failed_count", 0),
                    item.get("processed_bytes", 0), item.get("quarantine_files", 0),
                    item.get("removed_dirs", 0), item.get("error", ""),
                ])
        files.append(exec_path)

    manifest = {
        "schema": 1,
        "exported_at": now_iso(),
        "analysis_id": analysis.get("analysis_id"),
        "state": str(state.root),
        "files": [str(path) for path in files],
    }
    atomic_write_json(output / "export-manifest.json", manifest)
    return manifest
