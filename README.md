# Android Deep Clean（Android 深度清理 Skill）

面向 Android 手机的 AI Agent 深度清理技能：只读扫描 → 证据分析 → AI 复核 → 用户选择 → 确认令牌 → 隔离清理 → 验证/复查。

支持**双通路**：手机端 Agent（Termux / Shizuku）与 Windows 电脑端 Agent（USB + ADB），共用同一套分类规则、风险分级、保护清单、确认机制、执行日志与验证标准。

核心不是“删得快”，而是：**发现长期积累、隐藏较深、普通清理工具忽略的无用文件，同时绝不误删用户重要数据。**

## 两条通路

| 通路 | 场景 | 连接方式 | 关键参数 |
|---|---|---|---|
| A 手机端 | 没有电脑，手机上的 Agent | Termux 本机 / Shizuku rish | --transport local / rish |
| B 电脑端 | Windows 11 + USB 数据线，需要电脑备份/本地哈希/CSV | ADB（USB 调试） | --transport adb --serial <序列号> |

两条通路调用同一个 CLI、同一套 adc_lib 规则与同一状态机；电脑端只是在 Transport 层通过 adb shell 执行。详细说明：

- [电脑端 USB+ADB 工作流](android-deep-clean/references/usb-adb-workflow.md)
- [双通路架构与能力边界](android-deep-clean/references/dual-path-architecture.md)

## 电脑端快速开始（Windows 11）

1. 安装 Python 3.9+ 与 Android 官方 Platform Tools（含 adb.exe，不需要 Android Studio）。
2. 手机开启开发者选项 → USB 调试，用数据线连接，手机解锁并允许 RSA 授权。
3. 安装 Skill 并确认设备：

~~~powershell
$src = ".\android-deep-clean"
$dst = "$env:USERPROFILE\.agents\skills\android-deep-clean"
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
Copy-Item -Recurse -Force $src $dst

$Skill = $dst
$State = ".adc-state"
python "$Skill\scripts\adc.py" devices --adb-path adb
python "$Skill\scripts\adc.py" doctor --state $State --transport adb --serial <序列号>
~~~

4. 扫描/分析（电脑端可把重复候选拉到电脑算哈希）：

~~~powershell
python "$Skill\scripts\adc.py" scan    --state $State --transport adb --serial <序列号>
python "$Skill\scripts\adc.py" analyze --state $State --transport adb --serial <序列号> --hash-mode pc
~~~

5. AI 按 SKILL.md 复核 → plan → 用户选择 → select 令牌 → 用户确认 → execute：

~~~powershell
python "$Skill\scripts\adc.py" plan   --state $State --transport adb --serial <序列号> --review review.json
python "$Skill\scripts\adc.py" select --state $State --transport adb --all-safe
python "$Skill\scripts\adc.py" execute --state $State --transport adb --serial <序列号> --token <TOKEN> --backup-to D:\phone-backup
python "$Skill\scripts\adc.py" verify --state $State --transport adb --serial <序列号>
python "$Skill\scripts\adc.py" export --state $State --out D:\phone-report
~~~

6. 复查/历史/恢复：

~~~powershell
python "$Skill\scripts\adc.py" history --state $State
python "$Skill\scripts\adc.py" recheck --state $State --transport adb --serial <序列号>
python "$Skill\scripts\adc.py" restore --state $State --transport adb --serial <序列号> --all
~~~

## 手机端快速开始

~~~powershell
python scripts/adc.py doctor  --state .adc-state --transport local
python scripts/adc.py scan    --state .adc-state --transport local
python scripts/adc.py analyze --state .adc-state --transport local
~~~

Shizuku 环境用 --transport rish --rish-path rish。其余流程和电脑端完全相同。

## 电脑端专属能力

- **设备发现/多设备**：devices 列出 serial/状态；多设备必须 --serial，避免操作错手机。
- **设备身份绑定**：doctor/scan/plan/selection/execution 携带 device_identity；execute 前校验当前手机必须是计划中的那台。
- **断开保护**：每项执行前检查连接；检测到断开立即 aborted，后续项目不再执行。
- **电脑本地哈希**：analyze --hash-mode pc 只把大小相同的重复候选通过 USB 拉到电脑，本地计算 SHA-256。
- **电脑备份**：execute --backup-to DIR 在清理前复制并校验文件；备份失败则不清理源文件。
- **CSV 导出**：export 生成候选、完整文件、执行结果 CSV；Markdown 报告仍由各阶段生成。
- **历史扫描**：scan 自动归档上一轮快照到 history/scan-*；history 查看。
- **前后对比**：execution/verify 记录逻辑处理量、隔离量、实际释放与可用空间变化。

## 安全设计（摘要）

- 扫描/分析全程只读；execute 前需要 select 生成的确认令牌。
- 默认 quarantine 到设备隔离区；永久删除必须 --allow-permanent-delete 且只允许 SAFE。
- plan_hash / selection_hash / 令牌三重校验，且绑定设备身份；执行前重新 stat 校验大小/mtime/inode；变化即跳过。
- 路径白名单/黑名单、realpath 检查、拒绝符号链接与路径穿越；不使用递归删除。
- DANGEROUS（数据库/密钥/应用数据/已安装 OBB/个人原片/聊天媒体）永不可选。
- 权限不足、扫描截断、失败项、断连都写入报告，不假装完整。
- 电脑端绝不自动授权未知电脑、不自动 root、不通过危险命令突破权限。

## 项目结构

| 路径 | 内容 |
|---|---|
| android-deep-clean/SKILL.md | 技能主说明（平台 Skill 入口） |
| android-deep-clean/scripts/adc.py | CLI 入口 |
| android-deep-clean/scripts/adc_lib/ | transport / scan / rules / analyze / plan / execute / verify / export |
| android-deep-clean/references/ | 安全策略、权限矩阵、路径规则、复核指南、USB+ADB 工作流、双通路架构 |
| tests/ | pytest 模拟 Android 设备测试套件 |
| docs/ | 安装使用、权限兼容、测试报告、研究、限制、CHANGELOG |

## 测试

~~~powershell
python -m pytest tests -q
~~~

测试不需要手机：使用 mock transport 和临时设备树。当前全量结果：178 passed / 2 skipped / 0 failed（180 collected）。2 条 skip 为 Windows 真实符号链接权限限制；电脑端 ADB 的真实连接仍需真机冒烟，见 docs/test-report.md 的“未覆盖/需真机”章节。

## 文档

- [安装与使用](docs/install-and-usage.md)
- [权限与兼容性](docs/permissions-and-compatibility.md)
- [测试报告](docs/test-report.md)
- [限制与路线图](docs/limitations-and-roadmap.md)
- [Android 存储研究](docs/research-android-storage.md)
- [CLI 契约](docs/cli-contract.md)
- [变更记录](CHANGELOG.md)

## 许可证

MIT。研究仅借鉴 SD Maid 2/SE、SpaceSweep、Android Device Optimizer 等项目的设计思路；未复制其代码。第三方许可证说明见 docs/research-android-storage.md 与 NOTICE。
