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
| `recheck [--no-archive]` | 重新扫描并对比上一轮 | 否（写新快照） |
| `archive diff` | 读取本轮目录差分 | 否 |
| `archive stats` | 查看目录档案统计 | 否 |
| `archive mark --path PATH --decision keep/watch/neutral [--note TEXT]` | 给目录写分析标记 | 否 |

`scan` 额外参数：`--roots`、`--max-depth`、`--max-files`、`--no-internal`、`--no-archive`。`snapshot-meta.json` 新增 `coverage` 对象，包含 `status`（good/partial/suspect）、`root_status`、`skipped_paths`、`logical_bytes`、`unique_bytes`、`hardlink_saved_bytes`、`dir_count` 等字段；`directory-diff.json` 记录本轮跨轮目录变化。

`archive mark` 只接受 `keep/watch/neutral`。标记是分析证据，不是删除授权，不改变任何候选风险或选择资格。

状态目录固定结构：

```
.adc-state/
  state.json
  doctor/doctor.json report-doctor.md
  scan/snapshot.db snapshot-meta.json scan-summary.md directory-diff.json
  history/scan-<时间>/ snapshot.db snapshot-meta.json scan-summary.md
  history/archive.db
  analysis/analysis.db analysis.json analysis-summary.json review-template.json report-before.md
  plan/plan.json report-plan.md
  selection/selection.json report-selection.md
  execution/journal.jsonl execution.json report-after.md verification.json report-verify.md report-purge.md report-restore.md
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
