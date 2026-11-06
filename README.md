# Android / HarmonyOS Deep Clean（手机深度清理 Skill）

面向 Android 与华为 HarmonyOS 手机的 AI Agent 深度清理技能：只读扫描 → 证据分析 → AI 复核 → 用户选择 → 确认令牌 → 隔离清理 → 验证/复查。

支持**双通路**：手机端 Agent（Android/鸿蒙兼容版：Termux / Shizuku）与 Windows 电脑端 Agent（Android 用 USB + ADB，HarmonyOS NEXT 用 USB + HDC），共用同一套分类规则、风险分级、保护清单、确认机制、执行日志与验证标准。

HarmonyOS 会先识别系统类型再决定能力：兼容版沿用 Android 规则；NEXT 只处理公共目录与媒体库，明确不支持 APK、adb、Shizuku、Termux 和跨应用沙箱。完整矩阵见 [HarmonyOS 支持与兼容性](docs/harmonyos-support.md)。

核心不是“删得快”，而是：**发现长期积累、隐藏较深、普通清理工具忽略的无用文件，同时绝不误删用户重要数据。**

> 当前状态：Mock 与 CI 通过，真实 Android / HarmonyOS 设备完成验收数为 0。未达到正式清理工具门槛，详见 [真机验收清单与发布门槛](docs/real-device-test-plan.md)。

## 两条通路

| 通路 | 场景 | 连接方式 | 关键参数 |
|---|---|---|---|
| A 手机端（Android / 鸿蒙兼容版） | 没有电脑，手机上的 Agent | Termux 本机 / Shizuku rish | --transport local / rish |
| A 手机端（HarmonyOS NEXT） | 当前不支持 | 无 Android APK / Termux / Shizuku | 使用电脑端 hdc |
| B 电脑端（Android / 鸿蒙兼容版） | Windows 11 + USB 数据线，需要电脑备份/本地哈希/CSV | ADB（USB 调试） | --transport adb --serial <序列号> |
| B 电脑端（HarmonyOS NEXT） | Windows + USB，公共目录清理与电脑备份 | HDC（USB / 无线调试） | --transport hdc --serial <connect-key> |

两条通路调用同一个 CLI、同一套 adc_lib 规则与同一状态机；电脑端只是在 Transport 层通过 adb/hdc 执行。详细说明：

- [电脑端 USB+ADB 工作流](android-deep-clean/references/usb-adb-workflow.md)
- [HarmonyOS Agent 参考](android-deep-clean/references/harmonyos.md)
- [HarmonyOS 支持与兼容性](docs/harmonyos-support.md)
- [双通路架构与能力边界](android-deep-clean/references/dual-path-architecture.md)

## 电脑端快速开始（Windows 11）

1. 安装 Python 3.9+ 与 Android 官方 Platform Tools（含 adb.exe，不需要 Android Studio）。
2. 手机开启开发者选项 → USB 调试，用数据线连接，手机解锁并允许 RSA 授权。
3. 安装 Skill 并确认设备：

~~~powershell
# 安装/更新：先备份旧目录，复制后校验，失败自动回滚
powershell -ExecutionPolicy Bypass -File .\android-deep-clean\scripts\install_skill.ps1

$Skill = "$env:USERPROFILE\.agents\skills\android-deep-clean"
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

## HarmonyOS 快速开始

HarmonyOS 兼容版（1.x–4.x）继续用上面的 adb 流程。HarmonyOS NEXT 需要 hdc：

~~~powershell
# 1) 确认 hdc（来自 HarmonyOS Command Line Tools / SDK toolchains，不需要完整 IDE）
hdc list targets -v

# 2) 识别平台与权限（报告里看 platform / capabilities / limitations）
python "$Skill\scripts\adc.py" doctor --state $State --transport hdc --serial <connect-key>

# 3) 只读扫描公共目录与媒体库
python "$Skill\scripts\adc.py" scan    --state $State --transport hdc --serial <connect-key>
python "$Skill\scripts\adc.py" analyze --state $State --transport hdc --serial <connect-key>
~~~

NEXT 只处理 `/storage/media/100/local/files` 与媒体库；应用私有缓存逐文件扫描、卸载残留、手机端 Agent 都不支持。`bm clean -c` 仅报告能力，不自动执行。详见 [HarmonyOS 支持与兼容性](docs/harmonyos-support.md)。

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
| android-deep-clean/references/ | 安全策略、权限矩阵、路径规则、复核指南、USB+ADB 工作流、HarmonyOS 适配、双通路架构 |
| tests/ | pytest 模拟 Android 与 HarmonyOS NEXT 设备测试套件 |
| docs/ | 安装使用、权限兼容、HarmonyOS 兼容性、测试报告、研究、限制、CHANGELOG |

## 测试

~~~powershell
python -m pytest tests -q
~~~

测试不需要手机：使用 mock transport 和临时设备树（Android 与 HarmonyOS NEXT 两套平台画像）。当前本地全量结果：191 passed / 1 skipped / 0 failed（192 collected）。skip 为 Windows 目录联接环境限制，符号链接用例在可创建链接的环境中会实际执行；电脑端 ADB 与 hdc 的真实连接仍需真机冒烟，见 [真机验收清单](docs/real-device-test-plan.md)。

## 文档

- [安装与使用](docs/install-and-usage.md)
- [权限与兼容性](docs/permissions-and-compatibility.md)
- [HarmonyOS 支持与兼容性](docs/harmonyos-support.md)
- [测试报告](docs/test-report.md)
- [真机验收清单与发布门槛](docs/real-device-test-plan.md)
- [限制与路线图](docs/limitations-and-roadmap.md)
- [Android 存储研究](docs/research-android-storage.md)
- [CLI 契约](docs/cli-contract.md)
- [变更记录](CHANGELOG.md)

## 许可证

MIT。研究仅借鉴 SD Maid 2/SE、SpaceSweep、Android Device Optimizer 等项目的设计思路；未复制其代码。第三方许可证说明见 docs/research-android-storage.md 与 NOTICE。
