# 报告与数据格式

## 1. 必须给用户的清理前报告字段

- 设备、Android 版本、扫描时间、权限级别；
- 扫描文件数、目录数、逻辑大小；
- 候选分类：组数、文件数、大小、风险、建议；
- 每个候选：ID、路径示例、完整路径获取方式、证据、理由、保留建议、AI verdict；
- 预计可释放空间（逻辑大小估算，明确不是已释放）；
- 受保护内容（DANGEROUS，不提供删除）；
- 权限限制/未扫描路径；
- 用户下一步选项（SAFE 批量 / CAUTION 逐项 / 只保留报告）。

## 2. 清理后报告字段

- 执行 ID、时间、模式（quarantine/delete）；
- 计划项数、成功文件数、失败文件数；
- 处理逻辑大小、实际已释放、隔离文件数与大小；
- 隔离区位置、purge 令牌；
- 失败项目路径与原因；
- 验证结果（源文件消失/隔离文件存在/大小一致）；
- 存储可用空间变化（注意系统噪声）；
- 剩余问题、建议复查。
- 媒体集合状态：ok / empty / zero_unconfirmed / zero_mismatch / failed / skipped；0 条时按「可参与核对」的同类文件数分档：0 个为 empty，1 到阈值−1 为 zero_unconfirmed，达到阈值（默认 10）为 zero_mismatch。隐藏路径不参与核对（MediaStore 不索引），排除数量写入 `crosscheck.hidden_excluded` 并在限制中说明。
- 应用存储：基础/split APK、内部 data（root/su 时）、外部 data/media/obb，以及 permission_denied / partial / unavailable 状态。
- 隔离区：realpath 基线、父目录校验、哈希校验状态（通过 / 仅大小 / 未完成）。

HTML 报告为单文件离线页面，760px / 560px 两档断点。窄屏下表格不再横向滚动，而是借助每个单元格的 `data-label` 变成“标签在上、值在下”的卡片；`overflow-x:auto` 仅保留给导航与少量代码块。深色模式通过 `prefers-color-scheme: dark` 全量覆盖，两套语义色都重新调过对比度。已在 Chromium 390/560/760/1360 视口与深色模式下检查，无页面级横向溢出；真实 iOS Safari / 微信内置浏览器仍需单独验收。

## 3. 状态文件结构

~~~text
.adc-state/
  state.json                     运行清单（各报告路径）
  doctor/doctor.json             能力探测
  doctor/report-doctor.md
  scan/snapshot.db               文件快照（SQLite）
  scan/snapshot-meta.json        统计/限制/包列表摘要
  scan/scan-summary.md
  analysis/analysis.db           候选 + 完整文件清单
  scan/directory-diff.json       本轮跨轮目录变化
  history/archive.db             跨轮目录档案（主机侧）
  analysis/analysis.json         完整分析（大）
  analysis/analysis-summary.json AI 优先读（小）
  analysis/review-template.json  复核模板
  analysis/report-before.md      清理前报告
  analysis/report-before.html    清理前可视化报告（单文件、离线）
  plan/plan.json                 计划 + plan_hash
  plan/report-plan.md
  selection/selection.json       选择 + token + selection_hash
  selection/report-selection.md
  execution/journal.jsonl        逐文件移动/删除/失败记录
  execution/execution.json       执行汇总 + purge_token
  execution/report-after.md
  execution/report-cleanup.html  清理结果可视化报告（执行/验证/释放/恢复后更新）
  execution/verification.json
  execution/report-verify.md
  execution/report-purge.md
  execution/report-restore.md
  recheck/recheck-<时间>/        新扫描/新分析
  recheck/recheck.json
  recheck/report-recheck.md
~~~

## 4. 核心 JSON 字段

### analysis-summary.json

~~~json
{
  "schema": 1,
  "analysis_id": "analysis-...",
  "analysis_hash": "...",
  "summary": {
    "candidate_groups": 12,
    "candidate_files": 34,
    "candidate_bytes": 12345678,
    "risk_totals": {"SAFE": {"groups": 3, "files": 10, "bytes": 1000}},
    "categories": [],
    "limitations": [],
    "review_required": 5,
    "storage": {"coverage_status": "good", "file_scan": {}, "unexplained": {}, "media_index": {}, "app_storage": {}},
    "coverage": {"status": "good", "reasons": []},
    "media_index": {"status": "partial", "index": {}, "crosscheck": {"match_ratio": 1.0, "index_in_scope": 133, "index_scope_ratio": 0.0268}},
    "app_storage": {"status": "collected", "known": {}, "unknown": {}, "top_external": []},
    "space_story": {"headline": "...", "items": [], "known_disjoint_human": "...", "unexplained_human": "...", "caveats": []},
    "directory_changes": {"baseline": false, "summary": {}, "grew": []}
  },
  "review_items": [
    {"id": "...", "category": "residue", "title": "...", "risk": "CAUTION",
     "total_bytes": 123, "human_size": "123 B", "file_count": 2,
     "sample_paths": ["/sdcard/..."], "evidence": [{"code": "path", "text": "..."}]}
  ],
  "items": []
}
~~~

### plan.json

~~~json
{
  "schema": 1,
  "plan_id": "plan-...",
  "plan_hash": "...",
  "analysis_id": "analysis-...",
  "items": [
    {"id": "...", "category": "app_cache", "risk": "SAFE", "action": "clean_dir_children",
     "selectable": true, "file_count": 2, "total_bytes": 1000, "ai_verdict": "trash",
     "ai_reason": "..."}
  ],
  "summary": {"groups": 1, "selectable_groups": 1, "review_required": 0}
}
~~~

### selection.json

~~~json
{
  "schema": 1,
  "selection_id": "selection-...",
  "selection_hash": "...",
  "plan_hash": "...",
  "mode": "quarantine",
  "token": "ADC-XXXXXXXX",
  "items": [],
  "summary": {"groups": 0, "files": 0, "bytes": 0}
}
~~~

### execution.json

~~~json
{
  "execution_id": "exec-...",
  "mode": "quarantine",
  "status": "completed",
  "quarantine_root": "/sdcard/.android-deep-clean/quarantine/exec-...",
  "purge_token": "PURGE-XXXXXXXX",
  "items": [{"id": "...", "status": "success", "success_count": 2, "failed_count": 0}],
  "failures": [],
  "summary": {"success_files": 2, "failed_files": 0, "processed_bytes": 1000,
               "released_bytes": 0, "quarantine_files": 2, "quarantine_bytes": 1000}
}
~~~

## 5. 报告写法

- 用“逻辑大小”“已隔离”“实际释放”三个词，不要混用。
- 候选详情必须给真实路径示例；大组说明“完整清单在 analysis.db”。
- 风险标签同时给英文和中文：SAFE（安全）、CAUTION（谨慎）、DANGEROUS（危险）。
- 不确定的写“待确认/建议保留”，不要写成“垃圾”。
- 权限不足写进“限制”章节，不隐藏在脚注。
- 失败项逐条列路径和原因；不要只给总数。


## 6. v1.1 电脑端新增字段

- 所有阶段（doctor/scan/analysis/plan/selection/execution）应展示 device_identity：serial、model、id 短哈希。
- execution.json 额外字段：transport、device_identity、backup_dir/backup_root/backup_files/backup_bytes、abort_reason、status=aborted。
- execution/backup-manifest.json：每个备份文件的手机路径、电脑路径、SHA-256、大小、校验方式。
- export 目录：analysis-items.csv、analysis-files.csv、execution-items.csv、export-manifest.json。
- scan：previous_scan（上一轮归档路径）、device_identity；history 命令列出当前与归档快照。
- 电脑端报告必须写明“操作的是哪台设备”以及“断连/备份/权限失败”的实际结果。
