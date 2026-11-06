---
name: android-deep-clean
description: 面向 Android 手机的 AI 深度垃圾清理 Skill。通过 ADB / Shizuku / Root / Termux 只读扫描手机存储，生成带证据的文件分类与风险报告，由 AI 逐项复核后让用户选择，最后以隔离优先的方式安全清理，并验证结果。用于用户提出“手机空间不足/清理手机垃圾/深度清理安卓/清理微信 QQ 缓存/找卸载残留/找多年以前的 APK/找重复照片视频/清理浏览器和短视频缓存/看看手机存储被什么占了/清理后复查”等需求。Use for Android storage cleanup, deep junk analysis, uninstalled-app residue, old APKs, duplicate media, WeChat/QQ/browser caches, and post-clean verification. This skill never deletes without explicit user confirmation and a valid selection token.
---

# Android Deep Clean

一个给 Android 手机做**深度垃圾分析与安全清理**的 Skill。它不是 adb shell rm 命令集合，而是把「扫描 → AI 证据复核 → 风险分级 → 用户选择 → 确认令牌 → 隔离执行 → 验证/复查」串成闭环。

## 核心原则

1. **只读优先**：scan / analyze / plan / select / inspect / verify 都不修改手机。
2. **AI 参与判断，但不凭猜测**：AI 必须看证据（包是否安装、路径语义、文件类型、时间、大小、哈希、内容预览），不能仅凭文件名、年龄或大小认定垃圾。
3. **默认不永久删除**：默认移动到设备隔离区，可恢复；永久删除只对 SAFE 且必须显式 --allow-permanent-delete。
4. **没有用户确认就不执行**：execute 需要 select 生成的令牌；用户拒绝、未回复或要求修改时，绝不执行。
5. **无法访问就如实说**：权限不足导致的缺口必须写进报告，禁止假装扫描完整。
6. **不确定就保留**：无法确认用途的内容宁可留在手机上。

## 硬性安全规则（任何情况下不得违反）

- 禁止绕过本 Skill 的 CLI 直接执行 rm、rm -rf、mv、find -delete、pm clear 等删除命令。
- 禁止把 DANGEROUS 项加入选择；禁止降低风险等级。
- 禁止未经用户明确同意运行 execute / quarantine purge / restore。
- 禁止删除：Android 系统目录、应用数据库/配置/账号数据、OBB（已安装应用）、聊天记录与聊天媒体、个人照片视频原片、未知用途文件。
- 禁止删除符号链接指向的目标；禁止跟随符号链接扫描或删除。
- 禁止清空整个用户目录；只处理候选清单中的具体路径。
- 执行前必须让 CLI 重新 stat 校验大小/mtime/inode；文件有变化就跳过并报告，不得“强行清理”。
- 清理后必须 verify 并向用户报告成功、失败、实际释放、隔离区占用和剩余问题。

## 环境与安装

运行环境：Windows / macOS / Linux 上的 AI Agent（通过 ADB 控制手机），或 Android 本机 Termux（--transport local）。需要 Python 3.9+，只用标准库。

安装到当前 Agent Skills 目录（DeepSeek Harness / 兼容 Agent Skills 的平台）：

~~~powershell
# 1) 把整个 android-deep-clean 目录复制到用户级 skills 目录
Copy-Item -Recurse -Force .\android-deep-clean "$env:USERPROFILE\.agents\skills\android-deep-clean"

# 2) 确认 SKILL.md 位于技能目录根部
Test-Path "$env:USERPROFILE\.agents\skills\android-deep-clean\SKILL.md"
~~~

安装后新开一个会话，平台 Skill 列表里应出现 android-deep-clean。其他平台（Claude Code / Codex / 通用 Agent Skills）把同一个目录放到对应的 skills 根目录即可，目录结构必须保持 SKILL.md + scripts/。详细安装、ADB/Shizuku 配置见 references/installation.md。

调用时先确定技能目录并准备可写状态目录：

~~~powershell
$Skill = "$env:USERPROFILE\.agents\skills\android-deep-clean"
$State = ".adc-state"
python "$Skill\scripts\adc.py" doctor --state $State --transport adb
~~~

注意：**通用参数必须写在子命令之后**（例如 doctor --state ...，不能把 --state 放在 doctor 前面）。

## 权限分级（只使用真实探测结果）

| 级别 | 前置条件 | 能做什么 | 不能做什么 |
|---|---|---|---|
| full | Root / su | 共享存储 + /data/data/*/cache 等应用内部缓存；完整残留分析 | 仍不能碰系统核心/数据库/运行中应用私有文件 |
| standard | ADB shell 且实际可读 Android/data | 共享存储 + 部分应用外部缓存/残留 | 内部 /data/data |
| limited | 普通 ADB shell / Shizuku shell | 共享存储；Android/data 可能不可读 | 应用内部数据、受限的 Android/data |
| on-device | Termux + --transport local | 本机共享存储与 Termux 可读路径 | 其他应用私有数据（除非 su） |
| blocked | 无 ADB / 未授权 | 无 | 不能扫描，必须提示用户先授权 |

**禁止默认用户有 Root。** 每次开始都运行 doctor，按实际能力选择扫描根目录；报告必须包含“未能访问的路径/限制”。

## 标准工作流（所有模式共用）

### 0. 理解目标并选模式

先问清用户最在意什么：空间不足 / 卸载残留 / 微信缓存 / 重复照片 / 旧 APK / 清理后复查。根据目标选「全盘体检 / 深度清理 / 专项清理 / 清理复查」之一。若用户只想看看，不要执行删除。

### 1. Doctor：环境与权限

~~~powershell
python "$Skill\scripts\adc.py" doctor --state $State --transport adb
~~~

读输出的报告，向用户复述：设备、Android 版本、存储总量/可用、当前权限级别、哪些路径不可访问。若报告显示 blocked，停止并说明原因；不要假装扫描。

### 2. Scan：只读扫描

~~~powershell
python "$Skill\scripts\adc.py" scan --state $State --transport adb
~~~

默认扫描 /sdcard；Root 下会自动追加 /data/data/*/cache。可选参数：--roots、--max-depth、--max-files、--no-internal。大数据量时可接受截断，但必须在报告里说明。

### 3. Analyze：候选 + 证据 + 报告

~~~powershell
python "$Skill\scripts\adc.py" analyze --state $State --transport adb
~~~

生成：
- report-before.md：人类可读的清理前报告（分类、数量、大小、路径示例、理由、风险、建议）。
- analysis-summary.json：AI 优先读这个（比完整 analysis.json 小）。
- review-template.json：需要复核的候选模板。

然后**由 AI 做深度复核**（见下一节），把结论写成 review.json。不要跳过复核直接让用户选 CAUTION。

### 4. Plan：计划，不是执行

~~~powershell
python "$Skill\scripts\adc.py" plan --state $State --transport adb --review "$State\analysis\review.json"
~~~

计划会合并 AI 的 verdict 和理由，仍未复核的 CAUTION 不可选。读 plan/report-plan.md，向用户展示可选范围、风险、保留建议。

### 5. Select：用户选择 + 确认令牌

~~~powershell
# 例：用户明确同意清理安全缓存
python "$Skill\scripts\adc.py" select --state $State --transport adb --categories app_cache,browser_cache,thumbnails
~~~

select 只生成清单和令牌，不执行。必须把清单（路径、数量、大小、风险）给用户看，并问一句明确的确认问题。**用户没有明确说“可以/执行/清理”之前，不得继续。** 用户拒绝就停止；用户改变范围就重新 select。

### 6. Execute：用户确认后执行

~~~powershell
python "$Skill\scripts\adc.py" execute --state $State --transport adb --token <确认令牌>
~~~

默认 quarantine：把文件移动到设备隔离区，保留恢复能力。只有用户明确要求永久删除、且选择的全是 SAFE 时，才使用下面的永久删除流程；**selection 必须也用 select --mode delete 生成**：

~~~powershell
# 先在 select 时明确选择永久删除模式
python "$Skill\scripts\adc.py" select --state $State --transport adb --all-safe --mode delete
# 用户确认后执行
python "$Skill\scripts\adc.py" execute --state $State --transport adb --token <令牌> --mode delete --allow-permanent-delete
~~~

执行中任何文件变化、权限失败、符号链接都会跳过并写入 execution.json / 报告。不要重试为“强行删除”。

### 7. Verify 与报告

~~~powershell
python "$Skill\scripts\adc.py" verify --state $State --transport adb
~~~

向用户报告：成功/失败、逻辑处理大小、**实际已释放**（隔离模式为 0，purge 后才有）、隔离区位置与占用、剩余问题。然后询问是否：
- 恢复：restore --state $State --all（或 --ids）；
- 释放空间：quarantine purge --state $State --token <purge_token>（purge 后不可恢复，必须再次确认）。

### 8. 清理复查（模式 4）

~~~powershell
python "$Skill\scripts\adc.py" recheck --state $State --transport adb
~~~

重新扫描并对比上一轮，输出新增/仍存在/已消失候选。**新候选不能复用旧令牌**，必须重新复核、选择和确认。

## 四种工作模式

### A. 全盘体检
Doctor → Scan → Analyze → 输出清理前报告。默认**不执行**。适合“先看看手机里有什么”。如果用户看完想清理，再走 Plan/Select/Execute。

### B. 深度清理（最重要）
Doctor → Scan → Analyze → AI 逐项复核（residue / app_cache / chat_cache / thumbnails / temp_logs / trash / duplicates / apk / archives / screenshots / large_old / media_old / empty_dirs）→ Plan → 用户选择 → 确认 → 隔离执行 → Verify。重点找多年积累、隐藏较深、普通清理工具忽略的文件；但**不因为“老/大”就自动删**。

### C. 专项清理
先正常 Scan/Analyze，再用筛选缩小范围：
- 微信/QQ 缓存：select --categories chat_cache（只清 cache/temp，不碰聊天记录与 image2/video2/voice2/emoji）。
- 卸载残留：select --categories residue（CAUTION，必须先复核，逐包确认存档/下载）。
- 旧 APK：select --categories apk。
- 重复照片/视频：select --categories duplicates（逐组确认，保留建议的那份）。
- 长期未用大文件：select --categories large_old,media_old。
- 浏览器/短视频缓存：select --app-kinds browser,shortvideo 或 --categories browser_cache。
- 若用户只给某个目录，可 scan --roots /sdcard/某目录 后重新 analyze。

### D. 清理复查
清理后运行 recheck，识别新产生的垃圾、仍存在的项目、已消失的项目；对新候选重新走完整流程。

## AI 深度复核标准

先读 analysis-summary.json 的 review_items。对每一组：
1. 看 evidence、sample_paths、reasons、keep_suggestion。
2. 对体积大、风险高、证据弱、路径含义不明、涉及个人数据的组，运行 inspect --id <ID>；需要看内容时用 --pull 3 拉预览（图片可用图像查看；文档/压缩包先不要解压删除）。
3. 按下面的规则给 verdict：trash（有充分证据可清）、keep（保留）、uncertain（不确定，保留）。
4. 写 reason，说明检查了什么；trash 的 reason 不能是空话。风险只能保持或上调，不能下调。
5. DANGEROUS 永远 keep/uncertain，禁止 trash。

### 判定要点
- **卸载残留**：包不在 pm list packages 已安装列表；但外部数据可能含游戏存档/下载/聊天备份 → CAUTION，逐包确认；不要自动全选。
- **缓存**：只有 cache / code_cache / tmp / temp / .thumbnails 这类可再生目录才算 SAFE；files、databases、shared_prefs、app_webview 不是缓存。大缓存（>1GB）上调 CAUTION。
- **聊天应用**：只清明确 cache/temp；MicroMsg/.../image2、video2、voice2、emoji、Backup、WeChat Files 是用户数据/聊天媒体，禁止批量清。
- **重复文件**：SHA-256 完全相同才可列为重复；保留建议只是建议，仍需用户确认。不要按相似文件名/大小判断。
- **照片/视频/截图**：个人数据。没有内容级确认就 uncertain/keep，不得标记 trash；重复照片也至少保留一份。
- **APK / 压缩包 / 备份**：可能是离线重装包或唯一备份 → CAUTION，逐项问。
- **旧日志/临时文件**：确认扩展名和路径确属临时/日志；正在排障的日志保留。
- **空目录**：只用 rmdir，非空会失败；应用占位目录可能有用，CAUTION。
- **未知用途**：一律保留；不要用“看起来像垃圾”当理由。

复核文件格式（保存为 review.json，放在状态目录可访问位置）：

~~~json
{
  "schema": 1,
  "analysis_id": "<analysis.json 中的 analysis_id>",
  "decisions": [
    {
      "id": "app_cache-xxxxxxxxxx",
      "verdict": "trash",
      "risk": "SAFE",
      "reason": "cache 目录，仅含可再生缓存文件；已检查 sample_paths 与大小，不含离线包",
      "evidence_checked": ["path", "size", "age", "package"]
    },
    {
      "id": "residue-yyyyyyyyyy",
      "verdict": "keep",
      "risk": "CAUTION",
      "reason": "卸载残留内发现 GameSave 目录，可能是用户存档；保留",
      "evidence_checked": ["path", "package", "sample_paths"]
    }
  ]
}
~~~

## 报告要求

清理前报告必须包含：
- 文件分类、数量、占用空间；
- 具体目录/文件路径（示例至少 8 条，完整清单在 analysis.db）；
- 判断为垃圾的理由与证据；
- 风险等级（SAFE / CAUTION / DANGEROUS）；
- 建议操作与保留建议；
- 预计可释放空间（说明是逻辑大小估算）；
- 未能访问的路径与权限限制。

清理后报告必须包含：
- 实际清理数量、失败数量；
- 实际释放空间（隔离模式为 0，purge 后才是真实释放）；
- 隔离区位置与占用；
- 失败项目与原因；
- 权限限制/剩余问题/建议复查。

不要把“总容量”当清理结果。必须逐项说明准备删除什么。

## 命令速查

| 目的 | 命令 |
|---|---|
| 体检 | adc.py doctor --state .adc-state |
| 扫描 | adc.py scan --state .adc-state |
| 分析 | adc.py analyze --state .adc-state |
| 复核单个候选 | adc.py inspect --state .adc-state --id <ID> [--pull 3] [--tree] |
| 生成计划 | adc.py plan --state .adc-state --review review.json |
| 生成选择/令牌 | adc.py select --state .adc-state --categories app_cache |
| 执行（隔离） | adc.py execute --state .adc-state --token <TOKEN> |
| 验证 | adc.py verify --state .adc-state |
| 恢复 | adc.py restore --state .adc-state --all |
| 释放隔离区 | adc.py quarantine purge --state .adc-state --token <PURGE_TOKEN> |
| 复查 | adc.py recheck --state .adc-state |

统一加 --transport adb|rish|local|mock；Windows 上连接手机通常用 adb。无手机时用 --transport mock 做模拟测试。

## 故障与降级

- **ADB 未连接/未授权**：提示开启开发者选项 → USB 调试 → 手机上确认 RSA 指纹；无线调试用 adb pair + adb connect。不要继续假扫描。
- **Android/data 不可读**：说明 scoped storage 限制；可建议 Root、Shizuku 或使用系统设置清理；不要声称已扫描应用缓存。
- **无 Root**：跳过 /data/data 内部缓存；报告“内部数据不可读”。
- **无 sha256sum**：重复检测降级为大小分组并明确说明不能确认完全重复；不要猜。
- **扫描超时/截断**：报告截断范围；可提高 --max-files 或缩小 --roots 后重扫。
- **执行失败/文件变化**：报告失败项，保留源文件；重新扫描/计划，不得绕过校验。
- **设备存储不足导致隔离失败**：不要改为直接删除；报告失败并建议先释放其他空间或使用永久删除（仅 SAFE、用户明确确认）。

## Windows 开发与测试

在项目根目录（包含 android-deep-clean/、tests/）：

~~~powershell
python -m pytest tests -q
~~~

测试不需要手机：fixture 目录 + mock transport 模拟文件系统与权限。手工冒烟：

~~~powershell
python android-deep-clean\scripts\adc.py scan --state .tmp\state --transport mock --mock-root .tmp\phone --mock-packages .tmp\packages.json --mock-caps .tmp\caps.json --config .tmp\config.json
~~~

详细测试步骤和报告见项目 docs/test-report.md。

## 已知限制（必须对用户诚实）

- 非 Root 下无法读取 /data/data；部分 ROM 的 Android/data 也不可读。
- atime 在 Android 上通常不可靠，年龄判断主要用 mtime；不能把“未访问”当唯一证据。
- 无法识别所有应用私有格式/加密缓存；未知内容一律保留。
- 相似图片（非完全重复）需要图像内容复核，本 Skill 只做完全重复与截图/媒体候选，不自动删相似图。
- 隔离区仍占用空间，必须 purge 才真正释放；恢复依赖本机状态目录中的 journal。
- 不覆盖系统级清理（如系统缓存分区）；不保证所有 ROM 的 pm/find 行为一致。
- 真机兼容性需按 references/android-access.md 的矩阵验证。

## 参考文件

- references/safety-policy.md：风险分级、保护清单、执行前检查、失败处理。
- references/android-access.md：ADB/Shizuku/SAF/scoped storage/root 的版本差异与权限矩阵。
- references/path-rules.md：各类垃圾的路径特征、应用目录、误删风险。
- references/review-guide.md：AI 复核方法、反例、review.json 模板。
- references/workflow.md：四种模式的完整命令序列与用户对话模板。
- references/report-format.md：报告字段与 JSON 结构。
- references/installation.md：各平台安装、ADB/Shizuku 配置、Windows 开发。
- references/troubleshooting.md：常见错误与降级路径。
