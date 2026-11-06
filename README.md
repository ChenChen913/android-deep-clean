# Android Deep Clean（Android 深度清理 Skill）

面向 Android 手机的 AI Agent 清理技能：只读扫描 → 证据分析 → AI 复核 → 用户选择 → 确认令牌 → 隔离清理 → 验证/复查。

核心不是“删得快”，而是：**发现长期积累、隐藏较深、普通清理工具忽略的无用文件，同时绝不误删用户重要数据。**

## 交付物

| 路径 | 内容 |
|---|---|
| android-deep-clean/SKILL.md | 技能主说明（平台 Skill 入口） |
| android-deep-clean/scripts/adc.py | CLI 入口 |
| android-deep-clean/scripts/adc_lib/ | 扫描、规则、分析、计划、执行、验证模块 |
| android-deep-clean/references/ | 安全策略、权限矩阵、路径规则、复核指南、报告格式、故障排查 |
| tests/ | pytest 模拟 Android 设备测试套件 |
| docs/ | 安装使用、权限兼容、测试报告、研究、限制 |
| docs/research-android-storage.md | Android 存储/权限/开源项目研究（带来源） |

## 快速开始（Windows + USB 手机）

1. 安装 Python 3.9+ 与 Android Platform Tools（adb）。
2. 手机开启开发者选项 → USB 调试，连接并授权。
3. 安装 Skill：

~~~powershell
$src = ".\android-deep-clean"
$dst = "$env:USERPROFILE\.agents\skills\android-deep-clean"
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
Copy-Item -Recurse -Force $src $dst
~~~

4. 运行（通用参数放在子命令之后）：

~~~powershell
$Skill = "$env:USERPROFILE\.agents\skills\android-deep-clean"
$State = ".adc-state"
python "$Skill\scripts\adc.py" doctor --state $State --transport adb
python "$Skill\scripts\adc.py" scan --state $State --transport adb
python "$Skill\scripts\adc.py" analyze --state $State --transport adb
~~~

5. 让 AI 按 SKILL.md 复核，再 plan/select/execute/verify。默认隔离，可恢复。

详细步骤见 docs/install-and-usage.md；权限与版本见 docs/permissions-and-compatibility.md。

## 安全设计（摘要）

- 扫描/分析全程只读；execute 前需要 select 生成的确认令牌。
- 默认 quarantine 到设备隔离区；永久删除必须 --allow-permanent-delete 且只允许 SAFE。
- plan_hash / selection_hash / 令牌三重校验；执行前重新 stat 校验大小/mtime/inode；变化即跳过。
- 路径白名单/黑名单、realpath 检查、拒绝符号链接与路径穿越；不使用递归删除。
- DANGEROUS（数据库/密钥/应用数据/已安装 OBB/个人原片/聊天媒体）永不可选。
- 权限不足、扫描截断、失败项都写入报告，不假装完整。

## 无手机测试

~~~powershell
python -m pytest tests -q
~~~

或手工 mock：

~~~powershell
python android-deep-clean\scripts\adc.py doctor --state .tmp\state --transport mock --mock-root .tmp\phone --mock-packages .tmp\packages.json --mock-caps .tmp\caps.json --mock-device .tmp\device.json --config .tmp\config.json
~~~

## 文档

- docs/install-and-usage.md
- docs/permissions-and-compatibility.md
- docs/test-report.md
- docs/limitations-and-roadmap.md
- docs/research-android-storage.md
- docs/cli-contract.md

## 许可证

MIT。研究仅借鉴 SD Maid 2/SE、SpaceSweep、Android Device Optimizer 等项目的设计思路；未复制其代码。第三方许可证说明见 docs/research-android-storage.md 与 NOTICE。
