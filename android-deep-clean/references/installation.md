# 安装、配置与 Windows 开发

## 1. 平台 Skill 规范

本 Skill 遵循当前 Agent Skills 规范：
- 目录根部必须有 SKILL.md；
- YAML frontmatter 只包含 name 和 description；
- 辅助代码放 scripts/，参考文档放 references/；
- 通过 SKILL.md 的 description 触发，正文按需加载 references。

DeepSeek Harness 的用户级技能目录为 %USERPROFILE%\.agents\skills\<skill-name>\（已通过平台运行时 skill resourceBase 验证）。安装后需要新开会话，平台才会重新扫描技能列表。

## 2. DeepSeek Harness 安装

~~~powershell
# 从项目目录安装
$src = ".\android-deep-clean"
$dst = "$env:USERPROFILE\.agents\skills\android-deep-clean"
if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
Copy-Item -Recurse -Force $src $dst
Test-Path "$dst\SKILL.md"
python "$dst\scripts\adc.py" --help
~~~

重启 DSH 或在 UI 中新开会话；技能列表应出现 android-deep-clean。若没出现，检查 SKILL.md frontmatter 与目录层级，不要嵌套成 android-deep-clean/android-deep-clean。

## 3. 其他 Agent 平台

- Claude Code：~/.claude/skills/android-deep-clean/
- Codex / 通用 Agent Skills：按平台配置的 skills 根目录，保持 SKILL.md 在技能目录根部。
- 某些平台不自动执行 Python；Agent 可用 shell 工具调用 python scripts/adc.py。
- 跨平台要求 Python 3.9+；不需要 pip 安装依赖。

## 4. 手机侧配置（ADB）

1. 安装 Android Platform Tools（含 adb）并加入 PATH。
2. 手机开启开发者选项 → USB 调试；数据线连接；手机确认 RSA 授权。
3. 电脑验证：

~~~powershell
adb version
adb devices
adb shell getprop ro.build.version.release
~~~

4. 无线调试（Android 11+）：
   - 开发者选项 → 无线调试 → 使用配对码配对设备；
   - adb pair <IP>:<PAIR_PORT>，输入 6 位配对码；
   - adb connect <IP>:<DEBUG_PORT>；
   - adb devices 确认。
5. 多设备用 --serial；ADB 被占用时 adb kill-server 后重试。
6. 不需要 root；如用户已 root，可显式 --root-mode su，但必须提示风险并仍走确认流程。

## 5. Shizuku（可选）

1. 安装 Shizuku，按应用内指引通过无线调试或 root 启动服务。
2. 安装 rish：Shizuku 内提供安装脚本；在 Termux/终端确认 rish -c id 返回 shell 身份。
3. 运行：

~~~powershell
python "$Skill\scripts\adc.py" doctor --state $State --transport rish --rish-path rish
~~~

4. 能力与 ADB shell 相同；无法读 /data/data。

## 6. Termux（可选，on-device）

1. 安装 Termux 与 Python：pkg install python。
2. 把 android-deep-clean 复制到 Termux 可写目录。
3. 运行 --transport local；termux-setup-storage 后共享存储可读。
4. 没有 root 时只能读 Termux 有权限的路径；doctor 会如实报告。

## 7. Windows 开发与测试

环境：Windows 10/11 + Python 3.13（3.9+ 理论可用）+ pytest（测试可选）。

~~~powershell
cd C:\path\to\安卓清理
python -m compileall -q android-deep-clean\scripts
python -m pytest tests -q
~~~

无手机时用 mock transport（单行命令，避免 PowerShell 续行符）：

~~~powershell
python android-deep-clean\scripts\adc.py doctor --state .tmp\state --transport mock --mock-root .tmp\phone --mock-packages .tmp\packages.json --mock-caps .tmp\caps.json --mock-device .tmp\device.json
~~~

mock fixture 格式见 docs/cli-contract.md。测试重点：权限降级、保护路径、用户拒绝、路径穿越/符号链接、计划后文件变化、删除失败、隔离恢复、清理后验证。

## 8. 打包与分发

Skill 目录本身就是可分发包；如需 .skill/.zip：

~~~powershell
Compress-Archive -Path android-deep-clean -DestinationPath android-deep-clean.zip -Force
~~~

包内应包含：SKILL.md、scripts/（含 adc.py 与 adc_lib/）、references/、LICENSE。不要包含 .adc-state、.tmp-smoke、本地 absolute paths、测试产物。

## 9. 安装验证清单

- SKILL.md 在技能目录根部；
- python scripts/adc.py --help 正常；
- doctor 在目标设备返回权限级别；
- scan 只读（前后 fixture/手机抽查无删除）；
- plan 需要 review；select 生成令牌；execute 无令牌被拒绝；
- verify 能识别源文件/隔离文件状态；
- 报告 UTF-8 中文正常；
- 没有硬编码本机路径。

## 10. v1.1 双通路速查

电脑端（Windows 11 + USB）：

~~~powershell
python "$Skill\scripts\adc.py" devices --adb-path adb
python "$Skill\scripts\adc.py" doctor --state $State --transport adb --serial <序列号>
python "$Skill\scripts\adc.py" scan --state $State --transport adb --serial <序列号>
python "$Skill\scripts\adc.py" analyze --state $State --transport adb --serial <序列号> --hash-mode pc
python "$Skill\scripts\adc.py" execute --state $State --transport adb --serial <序列号> --token <TOKEN> --backup-to D:\phone-backup
python "$Skill\scripts\adc.py" export --state $State --out D:\phone-report
python "$Skill\scripts\adc.py" history --state $State
~~~

手机端（Termux / Shizuku），在 Skill 目录内执行（该目录包含 SKILL.md 与 scripts/）：

~~~powershell
python scripts/adc.py doctor --state .adc-state --transport local
python scripts/adc.py scan --state .adc-state --transport local
python scripts/adc.py analyze --state .adc-state --transport local
~~~

如果从仓库根目录运行，路径应为 `python android-deep-clean/scripts/adc.py`。Shizuku 用户把 `--transport local` 换成 `--transport rish --rish-path <rish 路径>`。

两条通路共用同一套规则、风险分级和确认令牌；详细见 usb-adb-workflow.md 与 dual-path-architecture.md。
