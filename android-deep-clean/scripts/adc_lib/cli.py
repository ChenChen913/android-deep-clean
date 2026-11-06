"""Command line interface for android-deep-clean.

Run: python scripts/adc.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .analyzer import Analyzer
from .config import load_config
from .device import Doctor, doctor_markdown
from .executor import Executor, load_execution, purge as purge_execution, restore as restore_execution, verify as verify_execution
from .exporter import export_state
from .models import PackageIndex
from .planner import load_analysis, run_plan, run_select
from .reports import recheck_markdown
from .rules import category_title
from .scanner import Scanner, load_packages, load_snapshot
from .state import State
from .transport import AdbTransport, Transport, create_transport
from .util import (
    AdcError,
    SafetyError,
    TransportError,
    UsageError,
    ValidationError,
    human_bytes,
    md_code,
    now_iso,
    read_json,
    split_csv,
    stable_id,
)

_COMMON = {
    "state": "状态目录（默认 .adc-state）",
    "transport": "adb / hdc / rish / local / mock",
    "serial": "设备序列号；hdc 模式下为 connect-key",
    "adb_path": "adb 可执行文件路径",
    "hdc_path": "hdc 可执行文件路径（HarmonyOS NEXT 电脑端）",
    "hdc_server_port": "hdc server 端口（可选，默认 8710）",
    "rish_path": "Shizuku rish 可执行文件路径",
    "sh_path": "local 模式的 shell 路径",
    "su_command": "su 命令（默认 su）",
    "timeout": "单次设备命令超时秒数",
    "root_mode": "auto/none/su/adb-root",
    "config": "可选 JSON 配置覆盖",
    "json": "以 JSON 输出（仍会写报告文件）",
    "quiet": "减少进度输出",
    "mock_root": "mock 模式的设备文件树根目录",
    "mock_packages": "mock 模式的应用包列表 JSON",
    "mock_caps": "mock 模式的权限/能力 JSON",
    "mock_device": "mock 模式的设备信息 JSON",
}


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", default=".adc-state", help=_COMMON["state"])
    parser.add_argument("--transport", choices=["adb", "hdc", "rish", "local", "mock"], default="adb", help=_COMMON["transport"])
    parser.add_argument("--serial", default="", help=_COMMON["serial"])
    parser.add_argument("--adb-path", dest="adb_path", default="adb", help=_COMMON["adb_path"])
    parser.add_argument("--hdc-path", dest="hdc_path", default="hdc", help=_COMMON["hdc_path"])
    parser.add_argument("--hdc-server-port", dest="hdc_server_port", type=int, default=0, help=_COMMON["hdc_server_port"])
    parser.add_argument("--rish-path", dest="rish_path", default="rish", help=_COMMON["rish_path"])
    parser.add_argument("--sh-path", dest="sh_path", default="", help=_COMMON["sh_path"])
    parser.add_argument("--su-command", dest="su_command", default="su", help=_COMMON["su_command"])
    parser.add_argument("--timeout", type=int, default=120, help=_COMMON["timeout"])
    parser.add_argument("--root-mode", dest="root_mode", choices=["auto", "none", "su", "adb-root"], default="auto", help=_COMMON["root_mode"])
    parser.add_argument("--try-adb-root", dest="try_adb_root", action="store_true", help="doctor 时尝试 adb root（仅 userdebug/eng）")
    parser.add_argument("--config", default="", help=_COMMON["config"])
    parser.add_argument("--json", action="store_true", help=_COMMON["json"])
    parser.add_argument("--quiet", action="store_true", help=_COMMON["quiet"])
    parser.add_argument("--mock-root", dest="mock_root", default="", help=_COMMON["mock_root"])
    parser.add_argument("--mock-packages", dest="mock_packages", default="", help=_COMMON["mock_packages"])
    parser.add_argument("--mock-caps", dest="mock_caps", default="", help=_COMMON["mock_caps"])
    parser.add_argument("--mock-device", dest="mock_device", default="", help=_COMMON["mock_device"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="adc",
        description="Android / HarmonyOS 深度清理 Skill CLI（默认只读；计划/令牌/隔离后才可能清理）",
    )
    parser.add_argument("--version", action="version", version=f"android-deep-clean {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("devices", help="列出 USB/无线 ADB 设备与授权状态（电脑端）")
    add_common(p)

    p = sub.add_parser("history", help="查看历史扫描快照（电脑端状态目录）")
    add_common(p)

    p = sub.add_parser("export", help="导出 CSV（候选清单/完整文件清单/执行结果）")
    add_common(p)
    p.add_argument("--out", default="", help="导出目录（默认 <state>/export）")

    p = sub.add_parser("doctor", help="检查设备、版本、存储、权限能力")
    add_common(p)

    p = sub.add_parser("scan", help="只读扫描并生成 SQLite 快照")
    add_common(p)
    p.add_argument("--roots", default="", help="逗号分隔；默认 auto=自动 /sdcard (+内部 cache)")
    p.add_argument("--max-depth", dest="max_depth", type=int, default=None)
    p.add_argument("--max-files", dest="max_files", type=int, default=None)
    p.add_argument("--no-internal", dest="no_internal", action="store_true", help="不扫描 /data/data 下的应用 cache")

    p = sub.add_parser("analyze", help="基于快照生成候选、证据、风险与清理前报告")
    add_common(p)
    p.add_argument("--hash-mode", dest="hash_mode", choices=["device", "pc"], default="", help="重复文件哈希位置：device（手机）或 pc（USB 拉到电脑计算）")

    p = sub.add_parser("inspect", help="查看某个候选的完整清单/预览，供 AI 深度复核")
    add_common(p)
    p.add_argument("--id", required=True, help="候选 ID")
    p.add_argument("--pull", nargs="?", type=int, const=3, default=0, help="拉取前 N 个文件到本机预览")
    p.add_argument("--list-paths", dest="list_paths", action="store_true", help="列出候选全部文件路径")
    p.add_argument("--tree", action="store_true", help="实时列出候选目录的一层/两层内容")

    p = sub.add_parser("plan", help="合并 AI 复核结论，生成清理计划")
    add_common(p)
    p.add_argument("--review", default="", help="AI 复核 JSON 路径（可选；没有复核时 CAUTION 不可选）")

    p = sub.add_parser("select", help="生成选择清单与用户确认令牌（不执行）")
    add_common(p)
    p.add_argument("--ids", default="", help="逗号分隔候选 ID（最精确）")
    p.add_argument("--categories", default="", help="按分类选择，例如 app_cache,temp_logs")
    p.add_argument("--app-kinds", dest="app_kinds", default="", help="按应用类型选择，例如 browser,chat")
    p.add_argument("--risk", default="", help="按风险选择：SAFE/CAUTION")
    p.add_argument("--all-safe", dest="all_safe", action="store_true", help="选择所有可选 SAFE 项")
    p.add_argument("--exclude-ids", dest="exclude_ids", default="")
    p.add_argument("--exclude-categories", dest="exclude_categories", default="")
    p.add_argument("--mode", choices=["quarantine", "delete"], default="quarantine")

    p = sub.add_parser("execute", help="用户确认后执行；默认移入隔离区")
    add_common(p)
    p.add_argument("--token", required=True, help="select 输出的确认令牌")
    p.add_argument("--mode", choices=["quarantine", "delete"], default="", help="默认沿用 selection.mode")
    p.add_argument("--allow-permanent-delete", dest="allow_permanent_delete", action="store_true")
    p.add_argument("--backup-to", dest="backup_to", default="", help="电脑端备份目录：执行前把选中的文件复制并校验到电脑")

    p = sub.add_parser("verify", help="验证执行结果；不修改文件")
    add_common(p)

    q = sub.add_parser("quarantine", help="隔离区管理（purge 会永久删除隔离文件）")
    qsub = q.add_subparsers(dest="quarantine_action", required=True)
    purge = qsub.add_parser("purge", help="永久删除隔离区文件以真正释放空间")
    add_common(purge)
    purge.add_argument("--token", required=True, help="execute 输出的 purge_token")

    p = sub.add_parser("restore", help="从隔离区恢复文件")
    add_common(p)
    p.add_argument("--ids", default="", help="按候选 ID 恢复")
    p.add_argument("--all", action="store_true", help="恢复全部可恢复文件")

    p = sub.add_parser("recheck", help="清理复查：重新扫描、生成新分析并与上一轮对比")
    add_common(p)

    return parser


def _state(args: Any) -> State:
    return State(args.state)


def _config(args: Any) -> dict[str, Any]:
    return load_config(args.config or None)


def _transport(args: Any, config: dict[str, Any], *, configure: bool = True) -> Transport:
    transport = create_transport(args, config)
    if transport.name in {"adb", "hdc"}:
        transport.ensure_ready()
    if configure:
        try:
            transport.configure_privilege(args.root_mode)
        except TransportError:
            if args.root_mode == "auto":
                transport._privilege = "shell"  # type: ignore[attr-defined]
            else:
                raise
    return transport


def _ensure_doctor(state: State, transport: Transport, config: dict[str, Any], args: Any, *, force: bool = False) -> dict[str, Any]:
    existing = state.get_file("doctor")
    if not force and existing and Path(existing).exists():
        report = read_json(existing, default=None)
        if isinstance(report, dict):
            cached_serial = str(((report.get("device_identity") or {}).get("serial")) or "")
            current_serial = ""
            try:
                current_serial = str(transport.device_serial() or "")
            except Exception:  # noqa: BLE001
                current_serial = ""
            if not cached_serial or not current_serial or cached_serial == current_serial:
                return report
    doctor = Doctor(transport, config, root_mode=args.root_mode, try_adb_root=bool(getattr(args, "try_adb_root", False)))
    report = doctor.run()
    out_dir = state.dir("doctor")
    atomic = out_dir / "doctor.json"
    atomic.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report-doctor.md").write_text(doctor_markdown(report), encoding="utf-8")
    state.set_file("doctor", atomic)
    state.set_file("doctor_report", out_dir / "report-doctor.md")
    return report


def _print_human(lines: list[str]) -> None:
    print("\n".join(lines))


def _safe_error(message: str) -> None:
    print(f"ADC_ERROR: {message}", file=sys.stderr)


def cmd_devices(args: Any, config: dict[str, Any]) -> int:
    if getattr(args, "transport", "adb") == "hdc":
        from .hdc_transport import HdcTransport

        transport = HdcTransport(hdc_path=args.hdc_path or "hdc")
        try:
            devices = transport.list_devices()
        except TransportError as exc:
            print(f"ADC_ERROR: {exc}", file=sys.stderr)
            return 4
        if args.json:
            print(json.dumps({"hdc_path": transport.hdc_path, "platform": "harmonyos_next", "devices": devices},
                             ensure_ascii=False, indent=2))
        else:
            if not devices:
                print("未检测到 HarmonyOS 设备。请开启开发者模式/USB 调试，并执行 hdc list targets。")
            else:
                print(f"HDC：{transport.hdc_path}")
                print("connect-key\t状态\t详情")
                for item in devices:
                    print(f"{item['serial']}\t{item['state']}\t{item.get('details', '')}")
            print("\n提示：多台设备必须用 --serial 指定 connect-key。")
        return 0
    transport = AdbTransport(adb_path=args.adb_path or "adb")
    try:
        devices = transport.list_devices()
    except TransportError as exc:
        print(f"ADC_ERROR: {exc}", file=sys.stderr)
        return 4
    from .transport import _ADB_STATE_LABELS

    enriched = [dict(item, label=_ADB_STATE_LABELS.get(item["state"], item["state"])) for item in devices]
    if args.json:
        print(json.dumps({"adb_path": transport.adb_path, "devices": enriched}, ensure_ascii=False, indent=2))
    else:
        if not devices:
            print("未检测到 Android 设备。")
        else:
            print(f"ADB：{transport.adb_path}")
            print("序列号\t状态\t说明\t详情")
            for item in enriched:
                print(f"{item['serial']}\t{item['state']}\t{item.get('label', '')}\t{item.get('details', '')}")
        print("\n提示：只有一个设备且状态为 device 时可省略 --serial；多设备必须用 --serial 指定目标。")
    return 0


def cmd_export(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    manifest = export_state(state, args.out or None)
    if args.json:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    else:
        print("CSV 导出完成")
        for path in manifest.get("files") or []:
            print(f"- {path}")
    return 0


def cmd_history(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    entries: list[dict[str, Any]] = []
    current_scan = state.get_file("scan")
    if current_scan and Path(current_scan).exists():
        meta = read_json(state.get_file("scan_meta"), default={}) or {}
        entries.append({
            "kind": "current",
            "path": current_scan,
            "scanned_at": meta.get("scanned_at", ""),
            "stats": meta.get("stats", {}),
            "device_identity": meta.get("device_identity", {}),
        })
    history_dir = state.root / "history"
    if history_dir.is_dir():
        for directory in sorted(history_dir.glob("scan-*"), key=lambda p: p.stat().st_mtime, reverse=True):
            meta = read_json(directory / "snapshot-meta.json", default={}) or {}
            entries.append({
                "kind": "archived",
                "path": str(directory / "snapshot.db"),
                "scanned_at": meta.get("scanned_at", ""),
                "stats": meta.get("stats", {}),
                "device_identity": meta.get("device_identity", {}),
                "archived_dir": str(directory),
            })
    if args.json:
        print(json.dumps({"state": str(state.root), "scans": entries}, ensure_ascii=False, indent=2))
    else:
        if not entries:
            print("暂无扫描历史。先运行 scan。")
        else:
            print(f"状态目录：{state.root}")
            for entry in entries:
                stats = entry.get("stats") or {}
                print(f"- {entry.get('scanned_at') or '-'}  {entry.get('kind')}  "
                      f"{stats.get('files', 0)} 文件 / {human_bytes(stats.get('bytes', 0))}  {entry.get('path')}")
    return 0


def cmd_doctor(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    transport = _transport(args, config, configure=False)
    report = _ensure_doctor(state, transport, config, args, force=True)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(doctor_markdown(report))
        print(f"\n> 报告文件：{state.get_file('doctor_report')}")
    return 0


def cmd_scan(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    transport = _transport(args, config, configure=True)
    doctor = _ensure_doctor(state, transport, config, args)
    scanner = Scanner(transport, config, state, doctor)
    meta = scanner.scan(
        explicit_roots=args.roots or None,
        max_depth=args.max_depth,
        max_files=args.max_files,
        include_internal=not args.no_internal,
        progress=not args.quiet,
    )
    if args.json:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    else:
        print(meta.get("android", ""), "扫描完成")
        print(f"- 状态目录：{state.root}")
        print(f"- 快照：{state.get_file('scan')}")
        print(f"- 摘要：{state.get_file('scan_meta')}")
        stats = meta.get("stats") or {}
        print(f"- 条目 {stats.get('entries', 0)}，文件 {stats.get('files', 0)}，逻辑大小 {human_bytes(stats.get('bytes', 0))}")
        if meta.get("truncated"):
            print("- ⚠ 扫描被截断，结果不完整；请查看扫描摘要中的限制。")
        print("\n下一步：analyze --state " + args.state)
    return 0


def cmd_analyze(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    conn, scan_meta = load_snapshot(state)
    packages = load_packages(conn)
    transport = _transport(args, config)
    hash_mode = args.hash_mode or str(config["scan"].get("hash_mode") or "device")
    analyzer = Analyzer(transport, config, state, conn, scan_meta, packages, hash_mode=hash_mode)
    analysis = analyzer.run()
    conn.close()
    summary = analysis.get("summary") or {}
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        print("分析完成（未删除任何文件）")
        print(f"- 候选组：{summary.get('candidate_groups', 0)}，文件 {summary.get('candidate_files', 0)}，逻辑大小 {human_bytes(summary.get('candidate_bytes', 0))}")
        print(f"- 需复核：{summary.get('review_required', 0)} 组")
        print(f"- 报告：{state.get_file('report_before')}")
        print(f"- 摘要：{state.get_file('analysis_summary')}")
        print(f"- 复核模板：{state.dir('analysis') / 'review-template.json'}")
        print("\n下一步：AI 复核后 plan --review <review.json> --state " + args.state)
    return 0


def cmd_inspect(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    analysis = read_json(state.require("analysis_json", "分析结果"), default=None)
    if not isinstance(analysis, dict):
        raise ValidationError("analysis.json 无效")
    item = next((entry for entry in analysis.get("items") or [] if entry.get("id") == args.id), None)
    if not item:
        raise UsageError(f"未知候选 ID：{args.id}")
    transport = _transport(args, config)
    analysis_db = analysis.get("analysis_db_path") or ""
    paths: list[dict[str, Any]] = []
    if analysis_db and Path(analysis_db).exists():
        import sqlite3

        conn = sqlite3.connect(analysis_db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM candidate_files WHERE candidate_id=? ORDER BY path", (args.id,)).fetchall()
        paths = [dict(row) for row in rows]
        conn.close()
    result: dict[str, Any] = {"item": item, "files": paths[:5000], "pulled": [], "tree": []}
    if args.list_paths:
        if not args.json:
            for row in paths[:5000]:
                print(row["path"])
            if len(paths) > 5000:
                print(f"... 另有 {len(paths) - 5000} 条，见 analysis.db", file=sys.stderr)
    if args.tree:
        target = item.get("primary_path") or ""
        try:
            rows = list(transport.iter_tree(target, max_depth=2, max_files=300))
            result["tree"] = [{"path": row.path, "kind": row.kind, "size": row.size} for row in rows]
        except Exception as exc:  # noqa: BLE001
            result["tree_error"] = str(exc)
    if args.pull:
        preview_dir = state.dir("analysis") / "preview" / args.id
        preview_dir.mkdir(parents=True, exist_ok=True)
        candidates = [row for row in paths if row.get("kind") == "file"]
        for index, row in enumerate(candidates[: max(0, int(args.pull))]):
            name = f"{index:03d}-" + Path(row["path"]).name
            local = preview_dir / name
            try:
                size = transport.pull(row["path"], str(local), max_bytes=20 * 1024 * 1024)
                result["pulled"].append({"path": row["path"], "local": str(local), "bytes": size})
            except Exception as exc:  # noqa: BLE001
                result["pulled"].append({"path": row["path"], "error": str(exc)})
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif not args.list_paths:
        print(f"# 候选 {item.get('id')} {item.get('title')}")
        print(f"- 风险：{item.get('risk')}；置信度：{item.get('confidence')}")
        print(f"- 文件 {item.get('file_count')}，目录 {item.get('dir_count')}，逻辑大小 {item.get('human_size')}")
        print("- 证据：")
        for evidence in item.get("evidence") or []:
            print(f"  - {evidence.get('text')}")
        print("- 路径：")
        for row in paths[:100]:
            print(f"  - {row.get('path')}")
        if len(paths) > 100:
            print(f"  - ... 另有 {len(paths) - 100} 条")
        if result.get("tree"):
            print("- 实时目录（最多 300 项）：")
            for entry in result["tree"][:50]:
                print(f"  - {entry['kind']} {entry['path']} ({human_bytes(entry['size'])})")
        if result.get("pulled"):
            print("- 已拉取预览：")
            for pull in result["pulled"]:
                if pull.get("error"):
                    print(f"  - 失败 {pull.get('path')}: {pull.get('error')}")
                else:
                    print(f"  - {pull.get('local')}")
        if result.get("tree_error"):
            print(f"- 目录读取错误：{result['tree_error']}")
    return 0


def cmd_plan(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    plan = run_plan(state, config, args.review or None)
    summary = plan.get("summary") or {}
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print("清理计划已生成（不会执行）")
        print(f"- 项目：{summary.get('groups', 0)} 组 / 文件 {summary.get('files', 0)} / {human_bytes(summary.get('bytes', 0))}")
        print(f"- 可选：{summary.get('selectable_groups', 0)} 组；仍待复核：{summary.get('review_required', 0)} 组")
        print(f"- 计划：{state.get_file('plan')}")
        print(f"- 报告：{state.get_file('plan_report')}")
        print("\n下一步：向用户展示计划，用户选择后运行 select。")
    return 0


def cmd_select(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    selection = run_select(state, config, args)
    summary = selection.get("summary") or {}
    if args.json:
        print(json.dumps(selection, ensure_ascii=False, indent=2))
    else:
        print("选择清单已生成（尚未执行）")
        print(f"- 已选：{summary.get('groups', 0)} 组 / 文件 {summary.get('files', 0)} / {human_bytes(summary.get('bytes', 0))}")
        print(f"- 模式：{selection.get('mode')}")
        print(f"- 确认令牌：{selection.get('token')}")
        print(f"- 清单：{state.get_file('selection')}")
        print(f"- 报告：{state.get_file('selection_report')}")
        print("\n必须把清单展示给用户并得到明确同意，才能把令牌交给 execute。")
    return 0


def cmd_execute(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    plan = read_json(state.require("plan", "清理计划"), default=None)
    selection = read_json(state.require("selection", "选择清单"), default=None)
    if not isinstance(plan, dict) or not isinstance(selection, dict):
        raise ValidationError("plan.json 或 selection.json 无效")
    mode = args.mode or str(selection.get("mode") or "quarantine")
    transport = _transport(args, config)
    executor = Executor(transport, config, state, backup_dir=(args.backup_to or None))
    execution = executor.run(plan, selection, args.token, mode, bool(args.allow_permanent_delete))
    summary = execution.get("summary") or {}
    if args.json:
        print(json.dumps(execution, ensure_ascii=False, indent=2))
    else:
        print("执行完成")
        print(f"- 模式：{execution.get('mode')}；状态：{execution.get('status')}")
        print(f"- 成功文件：{summary.get('success_files', 0)}；失败：{summary.get('failed_files', 0)}")
        print(f"- 处理逻辑大小：{human_bytes(summary.get('processed_bytes', 0))}")
        print(f"- 已释放：{human_bytes(summary.get('released_bytes', 0))}（隔离模式需 purge）")
        print(f"- 隔离区：{execution.get('quarantine_root') or '-'}")
        if execution.get("backup_files"):
            print(f"- 电脑备份：{execution.get('backup_files')} 个 / {human_bytes(execution.get('backup_bytes', 0))} -> {execution.get('backup_root')}")
        print(f"- Purge 令牌：{execution.get('purge_token')}")
        print(f"- 报告：{state.get_file('execution_report')}")
        if execution.get("status") == "aborted":
            print(f"⚠ 执行已中止：{execution.get('abort_reason') or '设备断开'}")
        elif execution.get("status") != "completed":
            print("⚠ 有失败项，请查看执行报告；失败文件仍留在原位置。")
        print("\n下一步：verify --state " + args.state)
    return 0 if execution.get("status") == "completed" else 6


def cmd_verify(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    transport = _transport(args, config)
    verification = verify_execution(state, transport, config)
    summary = verification.get("summary") or {}
    if args.json:
        print(json.dumps(verification, ensure_ascii=False, indent=2))
    else:
        print("验证完成")
        print(f"- 检查 {summary.get('checked', 0)}；通过 {summary.get('passed', 0)}；失败 {summary.get('failed', 0)}")
        print(f"- 报告：{state.get_file('verification_report')}")
        if summary.get("failed"):
            print("⚠ 存在异常，请查看报告。")
    return 0 if not summary.get("failed") else 6


def cmd_purge(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    transport = _transport(args, config)
    result = purge_execution(state, transport, args.token)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("隔离区清理完成")
        print(f"- 永久删除：{result.get('purged_files', 0)} 个 / {human_bytes(result.get('purged_bytes', 0))}")
        print(f"- 失败：{result.get('failed', 0)}")
        print(f"- 可用空间变化：{human_bytes(result.get('free_delta', 0))}")
    return 0 if not result.get("failed") else 6


def cmd_restore(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    transport = _transport(args, config)
    result = restore_execution(state, transport, split_csv(args.ids), bool(args.all))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("恢复完成")
        print(f"- 已恢复：{result.get('restored_files', 0)} 个 / {human_bytes(result.get('restored_bytes', 0))}")
        print(f"- 失败：{result.get('failed', 0)}")
    return 0 if not result.get("failed") else 6


def cmd_recheck(args: Any, config: dict[str, Any]) -> int:
    state = _state(args)
    previous, previous_path = load_analysis(state)
    previous_meta = read_json(state.require("scan_meta", "扫描摘要"), default={}) or {}
    transport = _transport(args, config)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    recheck_dir = state.dir("recheck") / f"recheck-{stamp}"
    new_state = State(str(recheck_dir))
    doctor = {
        "device": previous_meta.get("device") or {},
        "android": previous_meta.get("android", ""),
        "capabilities": previous_meta.get("capabilities") or {},
        "limitations": previous_meta.get("limitations") or [],
    }
    scanner = Scanner(transport, config, new_state, doctor)
    meta = scanner.scan(progress=not args.quiet)
    conn, scan_meta = load_snapshot(new_state)
    packages = load_packages(conn)
    analyzer = Analyzer(transport, config, new_state, conn, scan_meta, packages, hash_mode=str(config["scan"].get("hash_mode") or "device"))
    analysis = analyzer.run()
    conn.close()
    prev_map = {(item.get("category"), item.get("primary_path")): item for item in previous.get("items") or []}
    new_map = {(item.get("category"), item.get("primary_path")): item for item in analysis.get("items") or []}
    new_items = [item for key, item in new_map.items() if key not in prev_map]
    remaining = [item for key, item in new_map.items() if key in prev_map]
    vanished = [item for key, item in prev_map.items() if key not in new_map]
    summary = analysis.get("summary") or {}
    recheck = {
        "schema": 1,
        "recheck_id": stable_id("recheck", previous.get("analysis_id", ""), analysis.get("analysis_id", "")),
        "generated_at": now_iso(),
        "previous_analysis_id": previous.get("analysis_id"),
        "previous_analysis_path": previous_path,
        "new_analysis_id": analysis.get("analysis_id"),
        "new_analysis_path": str(new_state.get_file("analysis_json")),
        "new_report_path": str(new_state.get_file("report_before")),
        "summary": {
            "candidate_groups": summary.get("candidate_groups", 0),
            "candidate_bytes": summary.get("candidate_bytes", 0),
            "new_count": len(new_items),
            "remaining_count": len(remaining),
            "vanished_count": len(vanished),
        },
        "new_items": new_items[:200],
        "remaining_items": remaining[:200],
        "vanished_items": vanished[:200],
        "limitations": analysis.get("limitations") or [],
        "note": "复查只读；新候选仍需重新复核、选择与用户确认，不能复用旧令牌。",
    }
    out_dir = state.dir("recheck")
    atomic = out_dir / "recheck.json"
    atomic.write_text(json.dumps(recheck, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "report-recheck.md").write_text(recheck_markdown(recheck), encoding="utf-8")
    state.set_file("recheck", atomic)
    state.set_file("recheck_report", out_dir / "report-recheck.md")
    if args.json:
        print(json.dumps(recheck, ensure_ascii=False, indent=2))
    else:
        print("清理复查完成（未删除任何内容）")
        print(f"- 新增候选：{len(new_items)}；仍存在：{len(remaining)}；已消失：{len(vanished)}")
        print(f"- 新分析报告：{new_state.get_file('report_before')}")
        print(f"- 复查报告：{out_dir / 'report-recheck.md'}")
    return 0


def dispatch(args: Any) -> int:
    config = _config(args)
    if args.command == "devices":
        return cmd_devices(args, config)
    if args.command == "history":
        return cmd_history(args, config)
    if args.command == "export":
        return cmd_export(args, config)
    if args.command == "doctor":
        return cmd_doctor(args, config)
    if args.command == "scan":
        return cmd_scan(args, config)
    if args.command == "analyze":
        return cmd_analyze(args, config)
    if args.command == "inspect":
        return cmd_inspect(args, config)
    if args.command == "plan":
        return cmd_plan(args, config)
    if args.command == "select":
        return cmd_select(args, config)
    if args.command == "execute":
        return cmd_execute(args, config)
    if args.command == "verify":
        return cmd_verify(args, config)
    if args.command == "quarantine":
        if args.quarantine_action == "purge":
            return cmd_purge(args, config)
        raise UsageError("未知 quarantine 子命令")
    if args.command == "restore":
        return cmd_restore(args, config)
    if args.command == "recheck":
        return cmd_recheck(args, config)
    raise UsageError(f"未知命令：{args.command}")


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except AdcError as exc:
        _safe_error(str(exc))
        return exc.exit_code
    except KeyboardInterrupt:
        print("已中断（不会继续执行未完成的删除）", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        _safe_error(f"未预期错误：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
