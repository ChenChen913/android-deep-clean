# 安装与使用说明

## 0. 双通路（手机端 / 电脑端）

本 Skill 支持两条通路，共用同一套规则与安全机制：

| 通路 | 环境 | 连接 | 命令示例 |
|---|---|---|---|
| 手机端 Agent | Android 手机上的 Termux / Agent | 本机 shell 或 Shizuku rish | --transport local / rish |
| 电脑端 Agent（Android / 鸿蒙兼容版） | Windows 11 + USB 数据线 | ADB（Platform Tools） | --transport adb --serial <序列号> |
| 电脑端 Agent（HarmonyOS NEXT） | Windows + USB | HDC（Command Line Tools toolchains） | --transport hdc --serial <connect-key> |

电脑端不是另一套清理系统：分类、风险、保护规则、确认令牌、隔离/备份、日志与验证完全共用。电脑端完整流程见 references/usb-adb-workflow.md，HarmonyOS 见 references/harmonyos.md 与 docs/harmonyos-support.md。

## 1. 组成与要求

- Python 3.9+（只用标准库；Windows 上 Python 3.13 已测试）。
- 一台 Android 手机 + ADB（USB 或无线调试）；或 Shizuku/rish；或 Termux 本机。
- 不需要 Root；Root 是可选高阶通道。
- 不需要 pip 安装任何依赖。

Skill 目录结构：

~~~text
android-deep-clean/
  SKILL.md
  scripts/adc.py
  scripts/adc_lib/*.py
  references/*.md
  LICENSE
~~~

## 2. 安装

### 2.1 DeepSeek Harness

用户级技能目录为 %USERPROFILE%\.agents\skills\<skill-name>\。

~~~powershell
# 先备份旧目录，再复制并校验；失败会自动恢复旧版本
powershell -ExecutionPolicy Bypass -File .\android-deep-clean\scripts\install_skill.ps1

python "$env:USERPROFILE\.agents\skills\android-deep-clean\scripts\adc.py" --help
~~~

安装脚本会把旧目录保留为 `<原目录>.bak-时间戳`，不会直接删除用户自己添加的配置或文件；确认新版本可用后再手动清理备份。

新开一个 DSH 会话，技能列表中应出现 android-deep-clean。若未出现：检查 SKILL.md 是否在技能目录根部、frontmatter 是否只有 name/description。

### 2.2 Claude Code / 其他 Agent Skills

把整个 android-deep-clean 目录复制到平台的 skills 根目录（例如 ~/.claude/skills/android-deep-clean）。平台只加载 SKILL.md 的 name/description，正文和 scripts 按需使用。

### 2.3 从项目目录直接运行（不安装）

~~~powershell
python android-deep-clean\scripts\adc.py --help
~~~

Agent 也可以直接调用项目内脚本；安装只是为了平台自动发现。

## 3. 手机准备

### 3.1 ADB（电脑端推荐）

~~~powershell
adb version
adb devices -l
adb shell getprop ro.build.version.release
~~~

- 安装 Android 官方 Platform Tools 即可，不需要 Android Studio。
- HarmonyOS NEXT：安装包含 hdc 的 HarmonyOS Command Line Tools 或 SDK toolchains，不需要完整 DevEco Studio；用 --transport hdc --hdc-path hdc 连接。
- 手机：开发者选项 → USB 调试；数据线连接；解锁手机并允许 RSA 授权。
- 状态必须是 device；unauthorized 去手机点允许；offline 重新插拔/重开调试。
- 无线调试（Android 11+）：无线调试 → 使用配对码 → adb pair IP:PORT → adb connect IP:DEBUG_PORT。
- 多设备：必须 --serial <序列号>；可用 devices 命令查看。
- 手机端则用 --transport local（Termux）或 --transport rish（Shizuku）。

### 3.2 Shizuku

- 安装 Shizuku，按应用内指引启动（无线调试或 root）。
- 安装 rish；命令加 --transport rish --rish-path rish。
- 能力等同 ADB shell，不能读 /data/data。

### 3.3 Root

- 用户已有 Magisk/KernelSU 等，可加 --root-mode su。
- 仍需走完整确认流程；Root 不是跳过安全机制的理由。

### 3.4 Termux（手机端，无需电脑）

适用于 Android / HarmonyOS 兼容版；HarmonyOS NEXT 不支持。

1. 从 F-Droid 安装 Termux。
2. 安装 Python 并授权共享存储：

~~~bash
pkg install python git
termux-setup-storage
~~~

3. 克隆或复制本仓库到 Termux 可写目录。
4. 在仓库根目录执行：

~~~bash
python android-deep-clean/scripts/adc.py doctor  --state .adc-state --transport local
python android-deep-clean/scripts/adc.py scan    --state .adc-state --transport local
python android-deep-clean/scripts/adc.py analyze --state .adc-state --transport local
~~~

后续 inspect / plan / select / execute / verify / restore 与电脑端相同，只是不需要 `--serial` 与 `--adb-path`。

限制：无 root 时 `/data/data` 不可读；Android 11+ 的 `Android/data` 是否可读取决于系统限制，以 doctor 报告为准。Shizuku 用户改用 `--transport rish --rish-path <rish 路径>`。

## 4. 基本流程

所有命令的通用参数写在子命令之后。建议变量：

~~~powershell
$Skill = "$env:USERPROFILE\.agents\skills\android-deep-clean"
$State = ".adc-state"
~~~

### 4.1 设备发现与体检

~~~powershell
# 电脑端：列出所有 ADB 设备与授权状态
python "$Skill\scripts\adc.py" devices --adb-path adb

# 单设备可省略 --serial；多设备必须指定
python "$Skill\scripts\adc.py" doctor --state $State --transport adb --serial <序列号>
~~~

看权限级别、设备身份和存储；若 Android/data 不可读，报告会明确说明。

### 4.2 扫描（只读）

~~~powershell
python "$Skill\scripts\adc.py" scan --state $State --transport adb
~~~

默认 /sdcard；Root 自动追加 /data/data/*/cache。参数：
- --roots /sdcard,/sdcard/Download
- --max-depth 32
- --max-files 500000
- --no-internal
- --no-media（不采集 MediaStore 索引）
- --no-app-storage（不采集应用基础 APK 大小）

`scan` 默认在主机侧写 `scan/aux/sources.json`、`media-index.jsonl` 与 `app-storage.json`；这些辅助数据只用于交叉验证，不授权删除。

### 4.3 分析

~~~powershell
# 设备端计算重复文件哈希（默认）
python "$Skill\scripts\adc.py" analyze --state $State --transport adb --serial <序列号>

# 或：只把大小相同的重复候选通过 USB 拉到电脑，本地计算 SHA-256
python "$Skill\scripts\adc.py" analyze --state $State --transport adb --serial <序列号> --hash-mode pc
~~~

产出清理前报告、analysis-summary.json、review-template.json，并记录 hash_mode。报告包含「存储空间与扫描覆盖」「媒体索引核对」「应用级存储（部分口径）」「跨轮目录变化」；应用级存储只统计基础 APK 与外部目录，内部 data/cache 不可读。

### 4.4 AI 复核

Agent 读取 analysis-summary.json，运行 inspect 抽查，写 review.json（格式见 SKILL.md / references/review-guide.md）。CAUTION 未复核不可选。

### 4.5 计划

~~~powershell
python "$Skill\scripts\adc.py" plan --state $State --transport adb --review "$State\analysis\review.json"
~~~

### 4.6 用户选择与确认

~~~powershell
# 全部可选 SAFE
python "$Skill\scripts\adc.py" select --state $State --transport adb --all-safe

# 按分类
python "$Skill\scripts\adc.py" select --state $State --transport adb --categories app_cache,browser_cache,thumbnails

# 按应用类型
python "$Skill\scripts\adc.py" select --state $State --transport adb --app-kinds browser,chat

# 精确 ID（最安全）
python "$Skill\scripts\adc.py" select --state $State --transport adb --ids app_cache-xxxx,residue-yyyy
~~~

输出确认令牌。把清单给用户看，得到明确确认后再执行。用户拒绝就停止。

### 4.7 执行（默认隔离，可备份到电脑）

~~~powershell
python "$Skill\scripts\adc.py" execute --state $State --transport adb --serial <序列号> --token <确认令牌>

# 清理前备份到电脑：备份失败则跳过该文件，源文件保留
python "$Skill\scripts\adc.py" execute --state $State --transport adb --serial <序列号> --token <确认令牌> --backup-to D:\phone-backup
~~~

永久删除（仅 SAFE，用户明确要求；selection 必须也使用 delete 模式）：

~~~powershell
python "$Skill\scripts\adc.py" select --state $State --transport adb --all-safe --mode delete
python "$Skill\scripts\adc.py" execute --state $State --transport adb --token <令牌> --mode delete --allow-permanent-delete
~~~

### 4.8 验证、导出与释放

~~~powershell
python "$Skill\scripts\adc.py" verify --state $State --transport adb --serial <序列号>

# CSV 导出：候选 / 完整文件清单 / 执行结果
python "$Skill\scripts\adc.py" export --state $State --out D:\phone-report

# 历史扫描快照
python "$Skill\scripts\adc.py" history --state $State

# 确认无误后永久释放隔离区（不可恢复）
python "$Skill\scripts\adc.py" quarantine purge --state $State --transport adb --token <PURGE_TOKEN>

# 或恢复
python "$Skill\scripts\adc.py" restore --state $State --transport adb --all
~~~

### 4.9 复查

~~~powershell
python "$Skill\scripts\adc.py" recheck --state $State --transport adb
~~~

新增候选需要重新复核/计划/选择/确认，不能复用旧令牌。

### 4.10 跨轮目录档案

~~~powershell
# 首次 scan 建立基线；之后每次 scan 生成目录差分
python "$Skill\scripts\adc.py" scan --state $State --transport adb --serial <序列号>
python "$Skill\scripts\adc.py" archive diff --state $State
python "$Skill\scripts\adc.py" archive stats --state $State

# 给目录写分析标记（不是删除授权）
python "$Skill\scripts\adc.py" archive mark --state $State `
  --path /sdcard/DCIM/Camera --decision keep --note "用户相片目录"
~~~

扫描不完整、权限缺口、系统升级或切换用户时，档案不做消失判断。详细规则见 [跨轮目录档案](../android-deep-clean/references/directory-archive.md)。

## 5. 专项清理示例

| 用户目标 | 推荐选择 |
|---|---|
| 微信/QQ 缓存 | select --categories chat_cache（先复核） |
| 卸载残留 | select --categories residue（逐包确认） |
| 旧 APK | select --categories apk |
| 重复照片/视频 | select --categories duplicates（逐组确认） |
| 长期未用大文件 | select --categories large_old,media_old |
| 浏览器缓存 | select --app-kinds browser |
| 空目录 | select --categories empty_dirs（rmdir 非空失败） |
| 只清 SAFE | select --all-safe |

## 6. 输出与状态

状态目录 .adc-state 保存 doctor、SQLite 快照、分析、计划、选择、journal、验证报告。默认不包含个人文件内容；inspect --pull 才会把预览文件拉到本机。可随时删除状态目录重新开始（但会失去恢复 journal）。

## 7. 无手机测试

~~~powershell
python -m pytest tests -q
~~~

mock 模式需要 fixture JSON，格式见 docs/cli-contract.md。测试覆盖正常扫描、残留、重复、大旧文件、权限降级、保护路径、用户拒绝、令牌校验、文件变化、符号链接、删除失败、隔离恢复、验证与复查。

## 8. 退出码

0 成功；2 用法；3 安全拒绝；4 设备/传输；5 校验失败；6 部分失败；1 未预期；130 中断。

## 9. 注意事项

- Agent 不得自行确认用户意图；必须把 selection 清单和令牌风险展示给用户。
- 隔离不等于释放空间；报告里会区分。
- 不同 ROM/Android 版本能力不同，以 doctor 实际探测为准。
- 真机首次使用建议先跑全盘体检，只读查看报告，再决定是否清理。
