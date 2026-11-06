# 双通路架构：手机端与电脑端共享同一套清理系统

## 1. 总览

~~~text
                    ┌────────────────────────────────────┐
                    │  共享核心（同一套代码/规则/状态机）  │
                    │  rules / analyzer / planner /       │
                    │  executor / verify / reports        │
                    └──────────────┬─────────────────────┘
                                   │
              ┌────────────────────┴────────────────────┐
              │                                         │
      Transport: adb / rish / local              Transport: local(mock)
      Windows PC + USB 数据线                      Android 手机上的 Agent
              │                                         │
      adb shell / exec-out                        Termux + Python
      USB 调试 / 多设备 / 断连                      Shizuku/rish（可选）
~~~

两条通路不是两套清理系统：它们调用同一个 Python CLI、同一个 SQLite/JSON 状态、同样的分类规则、风险分级、复核模板、令牌、安全校验、执行器与验证器。区别只在 Transport 层：

| 层 | 手机端 Agent | 电脑端 Agent |
|---|---|---|
| 设备连接 | Termux 本地 shell / Shizuku rish | USB + ADB |
| 扫描 | 手机 find/stat（或 Termux） | adb shell find/stat（电脑消费输出） |
| 哈希 | 手机 sha256sum 或电脑 PC 模式 | 手机 sha256sum 或电脑 --hash-mode pc |
| 隔离 | 手机 /sdcard/.android-deep-clean | 手机 /sdcard/.android-deep-clean（同一路径） |
| 备份 | 手机本地副本/用户目录 | 电脑 --backup-to 目录 |
| 设备身份 | 设备信息（无 serial 时用 fingerprint） | ADB serial + fingerprint，强制绑定 |
| 断连 | 手机进程终止 | adb 断开检测 + aborted 状态 |

## 2. 共享不变项

以下逻辑只有一份，位于 android-deep-clean/scripts/adc_lib：

1. **分类与规则**：rules.py、analyzer.py；同一类别、同一阈值、同一保护清单。
2. **风险等级**：SAFE / CAUTION / DANGEROUS；DANGEROUS 永不可选。
3. **重要数据保护**：数据库/密钥/.nomedia/应用私有数据/OBB/聊天媒体/个人原片。
4. **确认机制**：plan → select → 用户明确确认 → 令牌；没有令牌不能执行。
5. **执行日志**：journal.jsonl + execution.json + backup-manifest.json。
6. **验证标准**：verify 检查源文件/隔离文件/删除结果，不只看命令退出码。
7. **报告**：Markdown + JSON + CSV 同一结构。

## 3. 两条通路的命令差异

手机端（Termux）：

~~~powershell
python scripts/adc.py doctor --state .adc-state --transport local
~~~

手机端（Shizuku/rish）：

~~~powershell
python scripts/adc.py doctor --state .adc-state --transport rish --rish-path rish
~~~

电脑端（USB+ADB）：

~~~powershell
python scripts/adc.py devices --adb-path adb
python scripts/adc.py doctor --state .adc-state --transport adb --serial <序列号>
python scripts/adc.py scan --state .adc-state --transport adb --serial <序列号>
python scripts/adc.py analyze --state .adc-state --transport adb --serial <序列号> --hash-mode pc
python scripts/adc.py execute --state .adc-state --transport adb --serial <序列号> --token <TOKEN> --backup-to D:\phone-backup
~~~

其余命令（plan/select/inspect/verify/restore/purge/recheck/export/history）完全一致，只换 --transport/--serial。

## 4. 能力边界

| 能力 | 手机端 local | 手机端 rish | 电脑端 adb shell | Root |
|---|---|---|---|---|
| 共享存储读写 | Termux 权限内 | 是 | 是 | 是 |
| Android/data | 取决于 Android 版本 | 通常可（实现细节） | 通常可（实现细节） | 是 |
| /data/data | 否 | 否 | 否 | 是 |
| 应用内部 cache | 否 | 否 | 否 | 是 |
| 设备身份/多设备 | 本机 | 本机 | serial 绑定 | serial 绑定 |
| 电脑备份 | 手动 | 手动 | --backup-to | --backup-to |
| 电脑 CSV 导出 | 状态目录在手机 | 状态目录在手机 | 电脑本地 | 电脑本地 |

无论哪条通路，无法读取的目录都写入 limitations，不把未扫描当成“无垃圾”。

## 5. 为什么不会误操作另一台手机

- AdbTransport.ensure_ready 检测所有设备；多台已授权且未 --serial → 拒绝。
- doctor 生成 device_identity（serial+fingerprint+model+sdk 的短哈希）。
- scan 写入身份；analysis/plan/selection 携带身份；plan_hash/selection_hash 覆盖身份。
- execute 前重新读取当前设备身份；不一致直接安全拒绝，不执行任何文件操作。
- 执行中每项前 health_ok 检查；断开则 aborted，未执行项保持原状。

## 6. 状态目录共享

电脑和手机上的状态目录结构一致：

~~~text
.adc-state/
  doctor/ scan/ analysis/ plan/ selection/ execution/ export/ history/
~~~

电脑端可以保留多个手机的多个状态目录（每个 --state 独立），禁止把 A 手机的计划用于 B 手机（身份绑定会拒绝）。

## 7. 选择哪条通路

- 有电脑、需要大批量扫描/本地哈希/电脑备份：USB + ADB。
- 只有手机、需要便携：Termux local；有 Shizuku 时用 rish 提升 Android/data 可见性。
- 需要内部 /data/data cache：只有 Root 通路；仍走同一确认/隔离/验证流程。
