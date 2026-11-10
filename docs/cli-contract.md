# ADC CLI 契约（测试与集成用）

状态：v1.3.0（2026-11-09）。命令参数必须写在子命令之后。

## 入口

```powershell
python android-deep-clean/scripts/adc.py <command> [common options] [command options]
```

通用参数：`--state DIR`、`--transport adb|hdc|rish|local|mock`、`--serial`、`--adb-path`、`--hdc-path`、`--hdc-server-port`、`--rish-path`、`--sh-path`、`--su-command`、`--timeout`、`--root-mode auto|none|su|adb-root`、`--try-adb-root`、`--config FILE`、`--json`、`--quiet`，以及 mock 参数 `--mock-root`、`--mock-packages`、`--mock-caps`、`--mock-device`。

## 命令

| 命令 | 说明 | 是否修改设备 |
|---|---|---|
| `doctor` | 只读能力探测 | 否 |
| `scan` | 只读扫描，写 SQLite 快照 | 否 |
| `analyze` | 基于快照生成候选与报告 | 否 |
| `inspect --id ID [--pull N] [--list-paths] [--tree]` | 复核一个候选 | 否（pull 只读设备） |
| `plan [--review FILE]` | 合并 AI 复核，生成计划 | 否 |
| `select ...` | 生成选择清单和令牌 | 否 |
| `execute --token TOKEN [--mode quarantine|delete] [--allow-permanent-delete]` | 执行 | 是 |
| `verify` | 验证执行结果 | 否 |
| `quarantine purge --token PURGE_TOKEN` | 永久删除隔离文件 | 是 |
| `restore [--ids ...] [--all]` | 从隔离区恢复 | 是 |
| `recheck [--no-archive] [--no-media] [--no-app-storage]` | 重新扫描并对比上一轮 | 否（写新快照） |
| `archive diff` | 读取本轮目录差分 | 否 |
| `archive stats` | 查看目录档案统计 | 否 |
| `archive mark --path PATH --decision keep/watch/neutral [--note TEXT]` | 给目录写分析标记 | 否 |

`scan` 额外参数：`--roots`、`--max-depth`、`--max-files`、`--no-internal`、`--no-archive`、`--no-media`、`--no-app-storage`。`snapshot-meta.json` 的 `coverage` 包含 `status`（good/partial/suspect）、`root_status`、`skipped_paths`、`logical_bytes`、`unique_bytes`、`hardlink_saved_bytes`、`dir_count`；`aux` 包含 `media_store` 与 `app_storage` 状态；`directory-diff.json` 记录本轮跨轮目录变化。`analysis-summary.json` 的 `summary` 包含 `storage`、`coverage`、`media_index`、`app_storage`、`space_story`、`directory_changes`；每个 item 带 `explain`（what/why/impact）。

`archive mark` 只接受 `keep/watch/neutral`。标记是分析证据，不是删除授权，不改变任何候选风险或选择资格。

状态目录固定结构：

```
.adc-state/
  state.json
  doctor/doctor.json report-doctor.md
  scan/snapshot.db snapshot-meta.json scan-summary.md directory-diff.json
  scan/aux/sources.json media-index.jsonl app-storage.json
  history/scan-<时间>/ snapshot.db snapshot-meta.json scan-summary.md
  history/archive.db
  analysis/analysis.db analysis.json analysis-summary.json review-template.json report-before.md report-before.html
  plan/plan.json report-plan.md
  selection/selection.json report-selection.md
  execution/journal.jsonl execution.json report-after.md report-cleanup.html verification.json report-verify.md report-purge.md report-restore.md
  recheck/recheck-<时间>/ recheck.json report-recheck.md
```

## 退出码

- 0：成功
- 2：用法错误
- 3：安全策略拒绝（令牌/风险/用户未确认/不可选）
- 4：设备/传输错误
- 5：输入/状态文件校验失败
- 6：执行/验证/purge/restore 部分失败（仍会写报告）
- 1：未预期错误
- 130：中断

## 关键安全规则（测试必须覆盖）

- 没有 `select` 令牌时 `execute` 必须拒绝且不改文件。
- 错误令牌、被修改的 plan/selection、非用户确认的 DANGEROUS/CAUTION 项必须拒绝。
- `select` 绝不能把 DANGEROUS 或未复核的 CAUTION 放进 selection。
- 执行前每个文件必须重新 stat：大小、mtime、inode 变化则跳过并报失败。
- 符号链接、路径穿越、系统/数据库/密钥/.nomedia 必须拒绝。
- 默认 quarantine；`--mode delete` 需要 `--allow-permanent-delete` 且 selection 只能含 SAFE。
- 任何失败不得静默：写入 execution.json / journal / report。
- 隔离区文件必须可 restore；purge 必须凭 execute 输出的 purge_token。
- purge / restore 前必须校验隔离文件大小与 journal 记录一致；有 `backup_sha256` 时还要校验内容摘要；不一致则拒绝操作并记录原因。
- restore 的 `quarantine_path` 必须位于本次执行的隔离区；恢复目标必须按候选类别重新通过 `guard_path`，不得恢复系统路径、个人媒体目录或无法确定类别的路径。
- 哈希工具缺失时按大小校验并在报告中标记“仅大小校验”；哈希计算异常记为“完整性校验未完成”，不得视为通过。

## mock 设备目录与 JSON

`--mock-root` 指向一个目录，设备路径映射：

- `/sdcard`、`/storage/emulated/0` → `<root>/sdcard`
- `/data` → `<root>/data`
- `/system` 等 → `<root>/<第一段>`

packages JSON：

```json
{"installed": ["com.a"], "uninstalled": ["com.b"], "system": ["android"], "available": true}
```

caps JSON：

```json
{
  "privilege": "root",
  "inaccessible": ["/sdcard/Android/data"],
  "denied_ops": ["delete:/sdcard/Download/blocked.apk", "move:/sdcard/..."],
  "commands": {"find": true, "stat": true, "xargs": true, "sha256sum": true, "pm": true, "df": true},
  "df": {"total_bytes": 128000000000, "used_bytes": 1, "available_bytes": 1}
}
```

device JSON 字段：`serial, sdk, release, manufacturer, model, build_type, abi, product, fingerprint`。

## 测试数据要求

- 用 Python 生成临时 fixture，`os.utime` 控制 mtime，不要提交大文件。
- 测试结束清理临时状态目录。
- 测试只通过 CLI subprocess 或导入 adc_lib 函数；不得修改 android-deep-clean/。
- 反向测试必须断言“文件仍存在”“没有产生 execution”或明确失败报告。


## v1.1 双通路扩展（2026-11-06）

新增命令/参数：
- devices --adb-path adb：列出 ADB 设备；不依赖 state，不需要 --serial。
- doctor/scan/analyze/execute/verify 支持 --transport adb + --serial <序列号>；多设备必须指定。
- analyze --hash-mode device|pc：电脑本地哈希重复候选；analysis.hash_mode 记录。
- execute --backup-to <PC目录>：清理前备份并校验；execution.backup_files/backup_bytes/backup_root；backup-manifest.json；备份失败跳过该文件。
- export --out <目录>：analysis-items.csv / analysis-files.csv / execution-items.csv。
- history：列出 state/history/scan-* 与当前扫描。
- plan/selection 携带 device_identity；plan_hash/selection_hash 覆盖设备身份；execute 前校验当前设备身份。
- execute 中途断连：status=aborted，未执行项目 aborted，不继续破坏性操作。
- scan 自动归档上一轮快照到 history/scan-*（最多 10 份）。

新增/更新测试契约：
- 设备身份不一致 execute 必须 exit 3，不产生 execution.json，fixture 不变。
- --backup-to 的备份文件内容/SHA-256 与源文件一致；备份失败时源文件仍在。
- --hash-mode pc 必须在不调用设备 sha256 的情况下识别完全重复候选。
- export 行数与 analysis items/files 一致；history 在第二次 scan 后出现归档。

## v1.10.0 扫描、应用存储、MediaStore、隔离区与移动端

- MediaStore：每个集合独立状态 `ok|empty|zero_mismatch|failed|skipped`；查询成功但 0 条且文件扫描同类文件达到 `zero_mismatch_min_files`（默认 10）时，集合为 `zero_mismatch`、整体 `partial`；没有同类文件时为 `empty`，不是失败。`sources.json` 记录 command、projection、exit_code、stderr、rows、attempts、elapsed。（v1.10.2 起新增 `zero_unconfirmed` 与 `crosscheck.hidden_excluded`，以文末 v1.10.1–v1.10.3 节为准。）
- 应用存储：`pm list packages -f` + 有上限的 `pm path`；APK 文件状态 `ok|missing|permission_denied|stat_failed`；split 状态 `collected|partial|disabled`。`/data/data` 只在 root/su 且存在 `du` 时做有上限的只读统计；普通 shell 明确 `unavailable`，禁止使用 `dumpsys diskstats`。
- 特殊缓存：必须同时匹配名称/扩展名与允许根目录（`/sdcard/Android/data` 或已知聊天根目录）；`.qcl` 等候选一律 CAUTION/待确认，不进入 SAFE；DCIM 等用户媒体目录下的 `.cache` 不产生缓存候选。
- 隔离区：execute 写入 `quarantine_real_roots`；purge/restore/verify 校验隔离根真实路径基线、隔离文件父目录真实路径；restore 目标父目录必须与记录路径 display-equivalent；无法解析 realpath 时拒绝。操作前再次 realpath 以缩小 TOCTOU 窗口。
- HTML：640px / 340px 断点；320/360/390/430/1280 实测无页面级横向溢出；表格在 `.scroll` 内用 `width:max-content` 横向滚动；目录为单行横向滚动。
- 新配置：`media_store.zero_crosscheck_min_files`、`app_storage.split_apk_max_packages`、`app_storage.internal_max_packages`。

## v1.10.1–v1.10.3 口径与状态补充

以下条目覆盖上文相同主题的旧说明，以本节为准。

- 特殊缓存：`Android/data/<pkg>/files/**` 下命中 `special_cache_rule` 的目录与文件均可进入候选；`special_cache_scope()` 让缓存内部文件继承缓存根的结论，执行器逐文件复检不会拒绝。风险下限固定 CAUTION，仅对 `app_cache|browser_cache|chat_cache` 生效；`files/databases`、`files/Downloads` 等非缓存路径继续拒绝。
- 扫描覆盖：`find`/`stat` 的 stderr 不再丢弃，权限失败写入 skipped 并把覆盖率降为 `partial`；`doctor.capabilities` 新增 `android_data_partial_read` 与 `android_data_sample`（抽样最多 6 个应用目录）。
- 应用存储：基础 APK 优先 `base.apk`，厂商 bundle 按 `pm path` 顺序取第一个非 `split_*` 条目；基础 APK 不可读时 `apk_base_bytes=null`，不使用 split 顶替。内部 `du` 的退出码与错误参与判定：权限/IO 失败标 `partial`，`cache`/`code_cache` 不存在属正常情况。
- 内部 cache：目录不存在或存在但为空才是 `0`；读不到时 `internal_cache_bytes` / `internal_code_cache_bytes` 写 NULL，汇总新增 `internal_cache_unknown`，manifest 新增 `internal.cache_unknown`，`internal.cache_bytes` 没有已知值时为 `null`。
- MediaStore：集合状态新增 `zero_unconfirmed`（0 条且可核对同类文件为 1 到阈值−1）；`crosscheck` 新增 `hidden_excluded`；`empty` 的显示文案为「0 条（未发现可参与核对的同类文件）」。

## v1.11.0 报告呈现契约补充

本节只涉及 HTML 的版式与呈现，**命令、参数、JSON 字段与文件名均未变化**。

- 报告文件路径不变：`analysis/report-before.html` 与 `execution/report-cleanup.html`；单文件、无外部资源、无 JS 依赖。
- 新增「可信状态」呈现层：`已知` / `部分` / `未知` / `无法确认` / `失败` 各有独立的图标、配色与图例。数值缺失时显示「未知」，不写 `0`；`partial` 的数值在文案上标注为下限。
- 候选呈现：按 `total_bytes` 降序，渲染上限 60 组（原 400），每组为 `<details>`，默认只展开「体积最大 / 风险最高 / 需复核」三组；超出部分提示到 `analysis.db` 与 `analysis.json` 查看，字段一致。
- 响应式：断点为 760px 与 560px。560px 以下 `table` 通过 `td[data-label]` 转为堆叠卡片，不再依赖横向滚动。深色模式由 `prefers-color-scheme: dark` 驱动。
- 纹理与图形全部为内联 CSS（渐变、`repeating-linear-gradient` 斜纹），没有图片、字体或图表库依赖。
- 旧版渲染器保留在 `adc_lib/html_report_legacy.py`，不被任何模块引用；需要回退时覆盖回 `html_report.py` 即可。

