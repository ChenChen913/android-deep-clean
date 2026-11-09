# Android 深度清理 Skill — 独立验证测试报告

## 当前版本摘要（v1.9.0，2026-11-09）

| 维度 | 当前结果 |
|---|---|
| 验证对象 | 本地工作区 v1.9.0 |
| 本地 Mock | 247 passed / 1 skipped（248 collected） |
| CI | 本次改动尚未推送运行；CI 矩阵仍为 ubuntu-22.04 / windows-latest × Python 3.9 / 3.13 |
| 本轮改动 | 新增清理结果 HTML 报告（`execution/report-cleanup.html`）及 6 个测试 |
| 真实设备只读 | HONOR HLK-AL00（HarmonyOS 3.0 兼容版 / Android 10 / SDK 29），USB+ADB |
| 真机写操作演练 | 仅自建文件：隔离 3/3、验证 3/3、恢复 3/3；purge 2/2 |
| purge 真机结果 | 2 个文件 / 30 B 永久删除，0 失败；错误令牌 rc=3；重放幂等（0 文件）；purge 后 restore 如实失败 |
| 令牌重放 | 确认令牌单次使用已生效（rc=3） |
| 空间速览真机结果 | 设备已用 78.81 GB；文件扫描 11.08 GB（14.1%）；基础 APK 8.98 GB；未解释差额 58.75 GB |
| MediaStore 真机结果 | 4,965 条索引 / 7.35 GB；4,964 条匹配；`downloads` 集合不可用 |
| 应用级存储真机结果 | 293 个应用，基础 APK 8.98 GB；49 个应用外部 data 合计 1.92 GB |
| 安全确认 | 演练后手机测试目录与空隔离目录已删除；未触碰任何用户真实文件 |
| 真实设备清理/恢复 | 仅在自建测试文件上通过；**用户真实数据清理仍未验收** |
| 真实 HarmonyOS NEXT | 0 台完成验收 |
| 当前定位 | 只读、隔离、验证、恢复、永久删除内建路径均已在自建文件上跑通；**真实数据清理仍未验收** |

阅读说明：当前版本结论只看本节；下方为历史版本记录，数字和被测对象不同属正常。真机验收方法见 [真机验收清单](real-device-test-plan.md)。

## 历史版本

### v1.9.0 清理结果 HTML 报告（2026-11-09）

| 项目 | 结果 |
|---|---|
| 生成时机 | `execute` 后生成；`verify` / `quarantine purge` / `restore` 后自动更新 |
| 覆盖内容 | 清理过程时间线、用户选择、逐项执行结果、失败路径、验证、隔离/释放/恢复、空间记录、剩余问题 |
| 验证方式 | mock 全链路：execute → verify → purge；以及 execute → restore |
| 结果一致性 | 成功/失败数、隔离占用、purge/restore 数量与实际记录一致；未验证步骤显示“尚未运行 verify” |
| 外部资源 | 0（无脚本、无字体、无 CDN） |
| 转义测试 | 失败路径中的 `<img onerror=...>` 被转义，不生成标签 |
| 降级测试 | 缺少 verify / selection 时不崩溃，并明确提示 |
| 渲染失败隔离 | 报告渲染异常不会让已完成的清理返回失败；错误页保留，Markdown/JSON/journal 证据不受影响 |
| 真实设备写操作 | 本轮未执行（未获得用户新的执行确认），真机写操作结论沿用 v1.7.1 |
| 自动化测试 | 新增 6 个；全量 247 passed, 1 skipped |

### v1.8.0 可视化 HTML 报告（2026-11-09）

设备数据来源：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），v1.6.2 真机快照（未重新扫描，未改动手机）。

| 项目 | 结果 |
|---|---|
| 生成方式 | `analyze` 在主机侧渲染，不访问设备 |
| 真机数据报告 | `analysis/report-before.html`，约 95 KB |
| 真机区块 | 空间去向速览（已知 20.06 GB / 25.5%，未解释 58.75 GB / 74.5%）、扫描覆盖、媒体索引、应用级存储、目录变化、候选、限制 |
| 外部资源 | 0（无脚本、无字体、无 CDN；离线可打开） |
| 转义测试 | 恶意路径 `<img src=x onerror=...>` 被转义，不生成标签 |
| 降级测试 | 缺少媒体索引/应用存储/空间速览/目录变化时不崩溃，候选为空时提示“未发现候选” |
| 只读验证 | analyze 前后 mock 设备快照完全一致 |
| 自动化测试 | 新增 5 个；全量 241 passed, 1 skipped |

### v1.7.1 purge 真机验证（2026-11-09）

设备：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），shell 权限（无 root）。仅在自建测试目录内执行写操作。

| 项目 | 结果 |
|---|---|
| 测试对象 | `/sdcard/ADC-DRILL-20261109-1730`，自建 2 个文件（15 B × 2） |
| 候选范围 | 唯一候选 `temp_logs`，2 文件 / 30 B，SAFE |
| execute（隔离） | 2/2 成功；`released_bytes=0` |
| 错误 purge 令牌 | 拒绝，退出码 3 |
| quarantine purge | 2/2 成功，0 失败；`free_delta=8192` 字节 |
| purge 重放 | 幂等：`purged_files=0`、`purged_bytes=0`，无误删 |
| purge 后 restore | 0 恢复 / 2 失败，如实报告不可恢复 |
| 清理 | 手机测试目录与空隔离目录已删除；本地状态目录已删除 |

### v1.7.0 确认令牌单次使用与真机隔离演练（2026-11-09）

设备：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），shell 权限（无 root）。仅在自建测试目录内执行写操作。

| 项目 | 结果 |
|---|---|
| 测试对象 | `/sdcard/ADC-DRILL-20261109-1705`，自建 3 个文件：26 / 31 / 36 B，mtime 2023-01-01 |
| 候选范围 | 唯一候选 `temp_logs`，3 个文件 / 93 B，SAFE，路径全部在测试目录内 |
| execute（隔离） | 3/3 成功，0 失败；`released_bytes=0`（符合隔离不释放空间） |
| verify | 3/3 通过；源目录清空；隔离区 3 个文件 |
| restore | 3/3 成功；SHA-256、大小、mtime 与演练前完全一致；隔离区清空 |
| 发现漏洞 | 使用同一令牌再次 execute 会重新隔离已恢复的文件 |
| 修复 | 令牌哈希写入 `execution/consumed-tokens.json`；重放返回 rc=3 并给出重新 select 的指引 |
| 修复后验证 | 恢复后重放被拒绝，文件保持原位 |
| 清理 | 手机测试目录已删除；空隔离目录已删除；本地演练状态目录已删除 |
| 全量 | 236 passed, 1 skipped |

### v1.6.2 限定目录扫描与只读预览（2026-11-09）

设备：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），shell 权限（无 root）。全程只读。

| 项目 | 结果 |
|---|---|
| 限定根目录 | `scan --roots /sdcard/Download`：648 条目 / 588 文件 / 384.60 MB；覆盖率 good；最大目录为 Download 本身 |
| 媒体索引范围 | 索引仍覆盖全盘 4,967 条（设计如此）；`index_in_scope=133`、`index_only_outside_scope=4,834` |
| 匹配率修正 | 修复前 2.68%（被范围外条目稀释）；修复后范围内 100%；`index_scope_ratio=2.68%` |
| inspect --pull | 只读拉取 2 个缩略图（396,068 B / 463,091 B）到电脑预览目录，核对后已删除 |
| 降级 | `scan --no-media --no-app-storage`：两源 `disabled`、0 条，扫描仍 good |
| 安全 | 未执行 execute/purge/restore；未生成 execution.json；手机隔离区不存在 |

### v1.6.1 recheck 平台误判修复与全链路只读回归（2026-11-09）

设备：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），shell 权限（无 root）。全程只读。

| 项目 | 结果 |
|---|---|
| 真机缺陷 | `recheck` 重建 doctor 时缺 `platform`，导致 meta 平台退化为 `android`，HarmonyOS 兼容版每轮被判「环境变化」并重置基线 |
| 修复后验证 | 连续两轮 recheck：第二轮 `baseline=false`、`environment_changed=false`、`platform=harmonyos_compat` |
| recheck | 候选 35 组；新增 2 组（来自默认配置启用重复检测）；目录 290 个，新增/增长/消失均 0 |
| inspect | `thumbnails-6919b1bd0b` 只读查看 40 个条目，返回 explain 三问 |
| plan | 33 组；可选 1 组（SAFE 缩略图，32 文件 / 5.11 MB）；需复核 32 组 |
| select | 生成隔离模式选择与 12 位确认令牌，未执行 |
| archive | 290 个目录 / 11.14 GB 跟踪；5 个疑似残留（含 `com.android.vending`、`com.google.android.gms` 等 OEM/GMS OBB，仅线索） |
| export | 生成 analysis-items.csv 与 analysis-files.csv |
| 降级 | `scan --no-media --no-app-storage` 成功；两个辅助源均为 `disabled`、0 条；覆盖率仍 good |
| 安全 | 未执行 execute/purge/restore；`/sdcard/.android-deep-clean/quarantine` 不存在；未生成 execution.json |

### v1.6.0 空间去向速览与候选解释（2026-11-09）

设备：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），shell 权限（无 root）。只读分析。

| 项目 | 结果 |
|---|---|
| 自动化测试 | 233 passed / 1 skipped；新增 5 个空间速览/解释用例 |
| 空间速览 | 设备已用 78.81 GB、文件扫描 11.08 GB、基础 APK 8.98 GB、应用外部目录 1.92 GB、媒体索引 7.35 GB；逐项标注重叠关系 |
| 已知不重叠合计 | 20.06 GB（文件扫描 + 基础 APK） |
| 未解释差额 | 58.75 GB（应用内部数据、系统分区、未扫描区域、文件系统开销） |
| 候选解释 | app_cache 等候选正常输出「这是什么 / 为什么列出 / 删除影响」三行 |
| 口径修正 | 存储区块的「未由文件扫描解释」改名为「仅共享存储」，与「未解释差额」区分 |
| 安全 | 只读分析；未生成计划或令牌；候选风险、可选择性、文件清单未改变 |

### v1.5.0 应用级存储（部分口径）（2026-11-09）

设备：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），shell 权限（无 root）。测试全程只读，未执行 select/execute/purge/restore。

| 项目 | 结果 |
|---|---|
| 自动化测试 | 228 passed / 1 skipped；新增 6 个应用级存储用例 |
| 采集方式 | `pm list packages -f` + 批量 `stat`；不使用 Root、不使用 `dumpsys diskstats` |
| 应用数量 | 293 个已安装应用；基础 APK 9,642,984,225 B（8.98 GB） |
| 外部目录 | 49 个应用有外部 data，合计 2,060,725,476 B（1.92 GB）；media/obb 0 B |
| 外部 cache | 4,953,179 B（4.72 MB），是外部 data 的子集 |
| 遗留目录 | 1 个未安装应用外部目录，109 B（`com.tencent.ams`） |
| 未知范围 | 应用内部 data/cache、split APK 未计入；普通 shell 无 StorageStatsManager 权限 |
| 候选证据 | app_cache / residue 候选附带包级 APK/外部目录证据；候选 ID、风险、可选择性未改变 |
| 安全 | 未修改手机文件；未生成清理计划或令牌；应用统计只作为证据 |

### v1.4.0 MediaStore 只读辅助索引（2026-11-09）

设备：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），Windows + Platform Tools，`--transport adb`，shell 权限（无 root）。测试全程只读，未执行 select/execute/purge/restore。

| 项目 | 结果 |
|---|---|
| 自动化测试 | 222 passed / 1 skipped；新增 11 个 MediaStore 用例 |
| doctor | 通过；293 个已安装包；/sdcard、Android/data、obb 可读；/data/data 不可读 |
| scan | 通过；25.2 秒；30,524 条目 / 24,141 文件 / 11.83 GB；覆盖率 good；无跳过路径 |
| MediaStore 集合 | 图片 4,814 条 / 4.40 GB；视频 73 条 / 2.96 GB；音频 78 条 / 0.53 GB；`downloads` 返回 unknown URL，按部分可用降级 |
| MediaStore 路径 | 4,965 / 4,965 可解析（100%） |
| 交叉核对 | 匹配 4,964；大小不一致 0；仅大小写不同 8；范围内索引缺失 0；范围外 1（预装音乐）；扫描有索引无 132 条 / 10.47 MB |
| 真机发现 1 | 日期列名应为 `datetaken`，不是 `date_taken`；修正后完整投影成功 |
| 真机发现 2 | 该 ROM 不支持 `media_type`；已从投影移除 |
| 真机发现 3 | MediaStore 返回 `tencent`，文件系统为 `Tencent`；交叉核对改为大小写不敏感，并单独统计 |
| 安全 | 未改动手机文件；未生成清理计划或令牌；媒体索引只作为证据 |

### v1.3.0 跨轮目录档案与扫描覆盖率（2026-11-09）

| 项目 | 结果 |
|---|---|
| 自动化测试 | 211 passed / 1 skipped；新增 16 个目录档案/覆盖率用例 |
| 扫描目录统计 | `dir_stats` 覆盖直接大小、子树大小、文件数、最新/最旧 mtime；mock 下与 os.walk 结果一致 |
| 覆盖率 | 根目录状态、跳过路径、截断、深度限制、硬链接去重大小写入 snapshot-meta 与 scan-summary |
| 目录档案 | 首轮 baseline；新增/增长/缩小/消失/复活/应用版本变化/疑似残留/未审查均有 mock 用例 |
| 防误判 | 扫描不完整、权限缺口、指纹变化时不标记消失；有对应测试 |
| 标记边界 | `archive mark` 只写 keep/watch/neutral；有测试确认不新增候选、不改变风险 |
| 真机 | **未验证**；当前环境没有 adb/hdc/设备 |

### v1.2.8 异常时间戳不再参与候选（2026-11-06）

外部复核指出：v1.2.5 只给异常 mtime 增加了提示，但年龄统计与按年龄筛选的生成器仍在使用异常值。经代码核对属实。

| 项目 | 修复前 | 修复后 |
|---|---|---|
| 年龄计算 `_age_days()` | 1980 时间戳照常换算成 45.9 年 | 早于 2000-01-01 或晚于当前 24 小时返回空，不参与年龄统计 |
| APK / 压缩包 / 截图 / Download / 大文件 / 旧媒体 / `.trashed-*` / 空目录 | SQL 仅用 `mtime < 截止时间`，1980 文件被当成很旧 | 增加 `mtime >= 2000-01-01` 与 `mtime <= now+24h` 上下界 |
| 证据 | 先给出异常年龄，再追加一条异常提示 | 不再产生由异常时间计算的年龄 |
| 混合目录 | 异常文件与正常旧文件一起进入候选 | 只保留正常旧文件；异常文件被排除 |

回归用例：`test_anomalous_mtime_cannot_create_age_based_candidate`（1980 的 `.bin` 与 `.zip` 不产生任何候选）、`test_anomalous_mtime_is_excluded_from_mixed_group`（同目录正常旧 zip 仍进入候选，1980 zip 被排除）；全量 195 passed / 1 skipped。

### v1.2.7 真机隔离/恢复演练与设备身份修复（2026-11-06）

设备：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），Windows 11 + Platform Tools，`--transport adb`，shell 权限。测试对象仅为本轮自建的 3 个文件（26 B × 3），不涉及用户任何真实文件。

| 步骤 | 结果 |
|---|---|
| 造数 | `/sdcard/ADC-SelfTest/` 下 3 个文件（a.tmp / b.tmp / c.log），mtime 设为 2020-01-01，记录 SHA-256 |
| doctor + scan | 4 条目 / 3 文件 / 78 B，未截断 |
| analyze | 1 个 `temp_logs` 候选组，SAFE，3 个文件 |
| plan + select | 1 组 / 78 B；令牌 `ADC-A312E333`；quarantine 模式 |
| execute | 首次被设备身份校验拒绝（见缺陷 #11）；修复后 3/3 成功，电脑备份 3 个文件，隔离区 `/sdcard/.android-deep-clean/quarantine/...` |
| verify | 3 项检查全部通过，源文件残留 0，隔离文件缺失 0 |
| 隔离区哈希 | 3 个文件 SHA-256 与造数时完全一致 |
| restore | 3/3 恢复，失败 0；恢复后 SHA-256 与 mtime 均与原始一致，隔离区残留文件 0 |
| 收尾 | 测试目录、隔离区目录、电脑端备份全部删除，手机恢复原状 |

本轮发现并修复的缺陷：

| # | 严重度 | 问题 | 根因 | 修复 |
|---|---|---|---|---|
| 11 | 高 | HarmonyOS 兼容版真机 `execute` 必然报「当前连接的设备与计划设备不一致」，计划与当前序列号相同 | doctor 先做平台识别（harmonyos_compat）再计算身份，执行前的二次校验直接用 `device_info()`，platform 为空被当成 Android，同一手机得到两个 id | 新增 `transport_device_identity()`，统一先 `detect_platform()` 再计算身份；执行前置校验与执行中校验都改用它；新增回归测试 |

### v1.2.6 ADB 真机只读验证与全盘扫描修复（2026-11-06）

设备：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），序列号 MGFNW19903000053，Windows 11 + Android Platform Tools，`--transport adb`，shell 权限（无 root）。

| 步骤 | 结果 |
|---|---|
| devices | 识别 1 台，state=device，自动提示已完成 RSA 授权 |
| doctor | platform=harmonyos_compat；serials/sdk/型号/指纹正确；已安装应用 298（系统 243）；`/sdcard` 112.5 GiB / 可用 17.6 GiB |
| scan（/sdcard/Download） | 237 条目 / 190 文件 / 723.07 MiB |
| scan（/sdcard 全盘） | 125,855 条目 / 117,007 文件 / 25,093,192,713 字节（23.37 GiB），耗时 47.8 秒，未截断，skipped 为空 |
| analyze | 59 个候选组 / 74,885 文件 / 5.02 GiB（5,390,767,059 字节）；CAUTION 56 组、SAFE 3 组；类别：缩略图 3、旧截图 18、应用缓存 22、旧下载 6、重复文件 3、临时日志 2、卸载残留 2、聊天缓存 1、空目录 2 |
| 受保护项 | 3.34 GiB 的 `photoshare.db` 等数据库文件进入 protected_items，未进入候选 |
| inspect | 成功；文件清单与 analysis.db 一致，未加 `--pull` |
| 只读核对 | `/sdcard/.android-deep-clean` 不存在；抽样文件 size/mtime/inode 与分析记录完全一致 |
| 删除/恢复 | 未执行 |

本轮发现并修复的缺陷：

| # | 严重度 | 问题 | 根因 | 修复 |
|---|---|---|---|---|
| 10 | 高 | USB+ADB 下 `scan --roots /sdcard` 只返回 1 个条目，全盘扫描结果为空 | `/sdcard` 是指向 `/storage/self/primary` 的符号链接，`find` 默认不跟随命令行起始链接，只打印链接自身 | `find -H`（只跟随起始链接，不跟随遍历中遇到的符号链接）；`listdir` 同步修正；新增回归测试 |

验证：修复前 `entries=1 / files=0`；修复后同一命令 `entries=125855 / files=117007`，`duration_seconds=47.8`。全量测试 193 passed / 1 skipped。

### v1.2.5 真机只读验证（2026-11-06）

设备：HONOR HLK-AL00，HarmonyOS 3.0.0（Android 10 / SDK 29），DSH Go proot Ubuntu，local 通路，普通应用权限。

| 步骤 | 结果 |
|---|---|
| doctor | platform=harmonyos_compat；privilege=shell；tier=standard；transport=local；型号/版本读取正确 |
| scan | 652 条目 / 593 文件 / 723.34 MiB；skipped 为空；未截断 |
| analyze | 6 个 `download_old` 候选组，全部 CAUTION；`candidate_bytes=309,378,488` |
| 受保护项 | `.db` 与 `.key` 进入 protected_items，未进入候选 |
| inspect | 成功，未加 `--pull`，未拉取文件内容 |
| 删除/恢复 | 未执行；`/sdcard/.android-deep-clean` 不存在 |

已知限制：`pm` 不在容器 PATH，卸载残留检测跳过；容器内 Android/data 可读不代表 scoped storage 权限；`classes.dex` 的 1980 年时间戳已新增 `mtime_anomaly` 证据提示。

### v1.2.4 真实手机扫描修复（2026-11-06）

真实设备反馈：HONOR HLK-AL00（Android 10 / SDK 29），DSH Go proot Ubuntu 24.04，Python 3.12。doctor 成功，但 scan 返回 0 条目。

| 问题 | 根因 | 修复 |
|---|---|---|
| scan 全量失败 | `find` 排除组拼成 `\(-path`，find 报 “paths must precede expression”，xargs 123 | 在 `\(` 与 `\)` 两侧补空格，新增回归测试 |
| privilege 误判 root | proot 内 uid=0 且存在 su，被当成 Android Root | local 通路要求 `uname -o` 为 Android，否则强制 shell |
| tier=full 自相矛盾 | 同上 | 修复后为 shell/standard，符合只读共享存储实际 |
| 设备型号/版本为空 | `getprop` 不在容器 PATH | 回退 `/system/bin/getprop` |
| capabilities.transport 错误 | 平台画像默认值覆盖实际 transport | 最后强制写入实际 transport |

本地全量：191 passed / 1 skipped。真机 scan/analyze 需在修复后重跑。

### v1.2.3 缓存风险与空间账目（2026-11-06）

- 通用应用缓存/浏览器缓存默认改为 CAUTION，需 AI 复核。
- 执行报告新增空间账目：发现、选择、已处理、未处理、已释放、隔离占用。
- HarmonyOS 媒体库 uid 改为动态探测，不再固定 100。
- 安装脚本改为先备份、后校验、失败回滚。
- 本地全量：188 passed / 1 skipped。

### v1.2.2 CI 二次修复（2026-11-06）

| 问题 | 根因 | 修复 |
|---|---|---|
| ubuntu-latest 上 Python 3.9 不存在 | runner 已迁移到 Ubuntu 26.04，toolcache 无 3.9 | CI 矩阵改用 ubuntu-22.04 |
| Linux 上符号链接用例失败 | mock `_to_device()` 用 `resolve()` 跟随链接，链接项被登记为目标路径并覆盖真实文件 | 改为 `normpath()` 保留链接自身路径 |
| Windows CI 符号链接用例断言失败 | 链接在扫描前创建，真实目标文件本身成为候选并被正常隔离 | 夹具改为 analyze/plan 后创建链接，再注入 manifest |

验证：

- WSL Linux 复现：修复前 `entries=6 / files=0 / items=[]`，修复后 `entries=7 / files=1 / items=[app_cache]`。
- Windows 本地全量：186 passed / 1 skipped（符号链接用例本次实际执行并通过）。
- CI：4/4 矩阵作业成功（ubuntu-22.04 / windows-latest × Python 3.9 / 3.13，run 42174357272）。

## v1.2.1 CI 修复复测（2026-11-06）

> 本次只修 CI 环境差异，不改变清理规则。

| 指标 | 结果 |
|---|---|
| 失败现象 | windows-latest 上 76 条用例同日失败，报 `Python int too large to convert to SQLite INTEGER` |
| 根因 | Windows 的 `st_ino` / `st_dev` 为无符号 64 位，超过 SQLite 有符号 64 位上限；本地文件 ID 较小所以未复现 |
| 修复 | `sqlite_int64()` 对 inode/dev 做二补码归一化，mock 与设备 stat 共用 |
| 回归用例 | `2**63`、`2**64-1`、普通整数、超范围 stat 行 |
| 本地全量 | 185 passed / 2 skipped |
| CI 结果 | 以 GitHub Actions 最新运行结果为准 |

## v1.2.0 HarmonyOS 适配复测（2026-11-06）

> 本轮只做平台识别、路径守卫与 mock 全链路验证；没有真实 HarmonyOS 设备，所有 hdc 真机能力均标记为待验证。

| 指标 | 结果 |
|---|---|
| 收集用例 | 186（v1.1.2 基线 183 + 本轮 3） |
| 通过 | 184 |
| 跳过 | 2（Windows 符号链接环境限制） |
| 产物版本 | android-deep-clean 1.2.0 |
| 新增覆盖 | 三平台识别；HarmonyOS NEXT 公共目录守卫；NEXT 全链路 mock |
| 未验证 | HarmonyOS NEXT/兼容版真机 hdc/adb、公共目录可写性、toybox 差异、`bm clean` 实际行为 |

HarmonyOS NEXT mock 全链路结果：

- doctor：`platform=harmonyos_next`，`public_read=true`，`android_data_read=false`，`supports_shizuku/termux=false`，`supports_bm_clean=true`。
- scan：只扫描 `/storage/media/100/local/files`；媒体库不存在时跳过并写入 limitations。
- analyze：旧压缩包、旧 APK、临时日志、重复文件、空目录进入候选；`secret.db`、`.nomedia`、`/data/app` 沙箱文件均未进入清单。
- plan/select/execute/verify/restore：隔离区位于公共目录 Docs 下，执行成功、验证失败 0、恢复成功。
- 回归：Android 既有用例全部通过，未出现分类或哈希回归。

**结论**：架构与 mock 验证通过；真实 HarmonyOS NEXT 设备上必须重新执行 doctor 与只读 scan，确认 hdc、公共目录和 toybox 能力后，才能宣称可用。

## v1.1.2 模拟真机全链路复测（2026-11-06）

> 本轮不连接真实手机，使用 mock transport 在临时目录模拟两种 ROM，覆盖完整清理闭环。

| 指标 | 结果 |
|---|---|
| 模拟环境 | 标准权限 ROM（可读 Android/data/OBB）+ 受限 ROM（Android/data、OBB 不可读） |
| 扫描 | 22 个文件 / 46,317,568 字节；受限 ROM 降级为 12 个文件、skipped=2，limitations 如实报告 |
| 分析 | 9 个候选组；`msg.db`、`.nomedia`、`files/`、聊天图片均未进入待清理清单 |
| 复核后计划 | 6 个可选组 / 8 个文件 / 14,450,688 字节 |
| 执行 | status=completed，成功 8 个文件，失败 0，电脑备份 8 个文件 |
| 验证 | 13 项检查全部通过，源文件残留 0 |
| 恢复 | restore 恢复 8 个文件，失败 0 |
| 永久删除 | 第二轮隔离 2 个文件后 purge 成功，受保护的 `msg.db` 仍存在 |
| 全量测试 | 181 passed / 2 skipped |
| 本轮发现 | 候选证据按过滤前清单统计文件数与大小，已修复并补回归用例 |

## v1.1.1 增量复测（2026-11-06）

> 本次修正安全缺口与完整性链，新增 2 条回归用例。下方 v1.1 章节保留为历史基线；其文件指纹表对应 1.1.0 快照，已不再代表当前代码。

| 指标 | 结果 |
|---|---|
| 收集用例 | 182（v1.1 基线 180 + 本次新增 2） |
| 通过 | 180 |
| 跳过 | 2（与 v1.1 相同的 Windows 符号链接环境限制） |
| 耗时 | 60.10s（单进程、mock 传输、无真实 adb） |
| 产品版本 | android-deep-clean 1.1.1 |
| 新增覆盖 | `guard_path` 内部缓存路径仍受敏感扩展名与 `extra_protected_paths` 约束；analysis.db 清单与计划不一致时 execute 直接拒绝（退出码 3），不触碰任何文件 |
| 变更文件 | rules.py / analyzer.py / planner.py / executor.py / util.py |

本次修复后，`plan_hash` 与 `selection_hash` 覆盖每个候选的 `files_digest`；篡改 analysis.db 后即使重算计划哈希，逐文件校验仍会拦截越权路径。旧版本状态目录中的计划需要重新 analyze 和 plan。

## v1.1 增量验证：双通路扩展（电脑端 ADB / PC 功能，task-4）

> 本节为 v1.1 增量结果；第 1-10 节为 v1.0 基线（当时 114 passed / 2 skipped），其结论与覆盖仍然有效，且本轮全量重跑确认旧用例全部通过。

### v1.1-1. 结论摘要

| 指标 | 结果 |
|---|---|
| 收集用例 | 180（v1.0 基线 116 + 本轮新增 64） |
| 通过 | 178 |
| 失败 | 0 |
| 跳过 | 2（与 v1.0 相同的符号链接环境限制用例） |
| 耗时 | 约 53 秒（多次运行 52.9-56.1s，单进程、无真实 adb、无网络） |
| 测试命令 | python -m pytest tests -q |
| 新增产品缺陷 | 1 项（#9，低）——已由 Lead 修复并用新增用例回归验证 |
| 最终判定 | 通过。双通路扩展功能（设备发现/身份绑定/断开保护、PC 备份、CSV 导出、PC 端哈希、扫描历史）按契约工作；v1.0 的 114 条旧用例零回归 |

### v1.1-2. 环境

| 项目 | 值 |
|---|---|
| 操作系统 | Windows-11-10.0.26300-SP0 |
| Python / pytest | 3.13.3 / 9.0.2 |
| 真实 adb | 未安装（NOT FOUND）；ADB 相关测试用临时目录中的伪 adb.cmd + 直接注入 AdbTransport._run 的 fake 完成，不依赖真机 |
| 传输层 | mock（主流程）+ 伪 adb 命令行（devices/doctor 失败路径） |
| 产品版本 | android-deep-clean 1.1.0 |
| 报告时间 | 2026-11-06 12:55 +08:00 |

复现：

~~~text
python -m pytest tests -q
# 需要查看跳过原因时：
python -m pytest tests -q -rs
~~~

被测产品代码指纹（SHA-256 前 16 位，2026-11-06 12:55 快照，含缺陷 #9 修复）：

| 文件 | 指纹 |
|---|---|
| scripts/adc.py | F18A1254E07E0447 |
| scripts/adc_lib/analyzer.py | 9DCAC19FE46F3F07 |
| scripts/adc_lib/cli.py | 99CC0A5F44225046 |
| scripts/adc_lib/config.py | 4EEAE9C77C552217 |
| scripts/adc_lib/device.py | 53B962B81F8C64CC |
| scripts/adc_lib/executor.py | CE9A7B081A089C91 |
| scripts/adc_lib/exporter.py | EC615417ECC42A46 |
| scripts/adc_lib/mock_transport.py | 414DF9693DFEDF45 |
| scripts/adc_lib/models.py | D284A6CB59B64545 |
| scripts/adc_lib/planner.py | 8BEF99AED1B98AFC |
| scripts/adc_lib/reports.py | 877B9EF73650EBA4 |
| scripts/adc_lib/rules.py | 8D2FC43225893F2E |
| scripts/adc_lib/scanner.py | 40A27661E1F6A12F |
| scripts/adc_lib/state.py | 9B6B5FB17495CA4A |
| scripts/adc_lib/transport.py | 90C3C2A665564E2A |
| scripts/adc_lib/util.py | 332D55F708C5FB28 |
| scripts/adc_lib/__init__.py | F12DD2BFBD55AE4F |

### v1.1-3. 真实测试输出

~~~text
................................................................ss...... [ 40%]
........................................................................ [ 80%]
....................................                                     [100%]
=========================== short test summary info ===========================
SKIPPED [1] tests/test_execute.py:388: 当前环境无法创建符号链接（Windows 需要开发者模式/管理员）:
           [WinError 1314] 客户端没有所需的特权
SKIPPED [1] tests/test_execute.py:434: 目录联接未被扫描/分类为缓存目录（平台差异）
178 passed, 2 skipped in 52.94s
~~~

分文件用例数（合计 180）：

| 测试文件 | 用例数 | v1.0 | v1.1 新增 | 关注点 |
|---|---:|---:|---:|---|
| tests/test_unit_util.py | 24 | 24 | 0 | 通用工具/config |
| tests/test_unit_guard.py | 17 | 17 | 0 | guard_path 安全策略 |
| tests/test_unit_planner.py | 9 | 9 | 0 | plan/selection 哈希基础 |
| tests/test_doctor.py | 3 | 3 | 0 | 设备/能力识别 |
| tests/test_scan.py | 5 | 5 | 0 | 扫描准确性/只读 |
| tests/test_analyze_categories.py | 13 | 13 | 0 | 分类识别 |
| tests/test_plan_select.py | 14 | 14 | 0 | 复核/风险/篡改 |
| tests/test_execute.py | 23 | 23 | 0 | 隔离/删除/恢复/purge/verify |
| tests/test_cli_contract.py | 8 | 8 | 0 | 退出码/前置依赖 |
| tests/test_unit_adb.py | 30 | 0 | 30 | ADB 设备解析/就绪/健康/CLI devices/doctor rc4 |
| tests/test_unit_identity.py | 8 | 0 | 8 | device_identity 稳定性 + 哈希覆盖 |
| tests/test_dual_path.py | 17 | 0 | 17 | 身份绑定、备份、导出、PC 哈希、扫描历史 |
| tests/test_device_guard_unit.py | 9 | 0 | 9 | 备份失败安全、运行中断开中止 |
| 合计 | 180 | 116 | 64 | |

### v1.1-4. 新增覆盖矩阵（task-4 需求 → 测试）

| 需求 | 对应测试 | 结果 |
|---|---|---|
| parse_adb_devices：device/unauthorized/offline/no permissions/多设备/空 | test_unit_adb.py::test_parse_adb_devices_*（8 条） | 通过 |
| ensure_ready：无设备/未授权/离线/无权限/多设备必须 --serial/指定 serial 存在/缺失/状态错 | test_unit_adb.py::test_ensure_ready_*（8 条） | 通过 |
| list_devices 失败、缺 adb 二进制的可操作报错 | test_unit_adb.py::test_list_devices_*（2 条） | 通过 |
| health_ok（serial/device/offline/异常）与 device_info serial | test_unit_adb.py::test_health_ok_requires_serial_and_get_state_device、test_device_serial_and_device_info_serial | 通过 |
| devices 命令 JSON/文本输出、adb 失败 rc4 | test_unit_adb.py::test_devices_command_*（3 条） | 通过 |
| doctor --transport adb：无设备/未授权/缺 adb 均 rc4 | test_unit_adb.py::test_doctor_with_adb_*（3 条） | 通过 |
| device_identity 稳定性；序列号/材料变化才改变 id | test_unit_identity.py（4 条） | 通过 |
| plan_hash/selection_hash 覆盖 device_identity（篡改 id 哈希变，不同设备哈希不同） | test_unit_identity.py（4 条） | 通过 |
| 身份贯穿 doctor/scan/analyze/plan/selection/execution | test_dual_path.py::test_device_identity_propagates_through_all_artifacts | 通过 |
| 设备 A scan/plan/select 后换成设备 B：execute rc=3 且 fixture 不变 | test_dual_path.py::test_device_switch_after_plan_is_rejected | 通过 |
| caps connected=false：execute 拒绝且无 execution.json | test_dual_path.py::test_disconnected_device_blocks_execute | 通过 |
| 篡改 plan/selection 的 device_identity：拒绝 | test_dual_path.py::test_tampered_device_identity_in_plan_is_rejected | 通过 |
| --backup-to：备份内容/SHA-256/execution 统计/backup-manifest.json/journal | test_dual_path.py::test_backup_to_copies_and_verifies_before_quarantine | 通过 |
| --backup-to + 永久删除模式 | test_dual_path.py::test_backup_to_supports_permanent_delete_mode | 通过 |
| 备份目录不可用：rc=3、不清理、fixture 不变 | test_dual_path.py::test_backup_to_unusable_target_fails_without_cleaning | 通过 |
| 备份失败跳过清理、源文件保留（pull 异常/size 不符/SHA 不符/单文件失败隔离） | test_device_guard_unit.py（4 条） | 通过 |
| 备份成功后清理被拒绝：手机源与电脑副本并存 | test_dual_path.py::test_backup_succeeds_but_denied_cleanup_keeps_both_copies | 通过 |
| /data 内部缓存备份（data 命名空间）与内部隔离区 | test_dual_path.py::test_internal_app_cache_backup_and_quarantine | 通过 |
| export：analysis-items.csv / analysis-files.csv / execution-items.csv 行数一致 | test_dual_path.py::test_export_csv_rows_match_analysis | 通过 |
| export 无 analysis rc5、默认输出目录 | test_dual_path.py::test_export_requires_analysis、test_export_default_output_dir | 通过 |
| --hash-mode pc 识别重复、同大小不同内容不误判、hash-tmp 清理 | test_dual_path.py::test_hash_mode_pc_detects_duplicates_without_device_hash_tools | 通过 |
| hash_mode 可由 config.scan.hash_mode 默认设置 | test_dual_path.py::test_hash_mode_default_can_come_from_config | 通过 |
| scan 归档上一轮 + history 列出 current/archived + 空状态 + 三次扫描 | test_dual_path.py::test_scan_archives_previous_round_and_history_lists_it、test_history_command_without_scans | 通过 |
| 历史快照保留各自设备身份（设备切换后） | test_dual_path.py::test_scan_history_records_each_device_identity | 通过 |
| 运行中断开：剩余项 aborted、报告写入、已处理项生效 | test_device_guard_unit.py::test_mid_run_disconnect_aborts_remaining_items | 通过 |
| 文件移动中断开：全部 aborted、源文件保留 | test_device_guard_unit.py::test_device_lost_during_move_aborts_and_keeps_files | 通过 |
| v1.0 全部用例零回归 | tests/ 其余 116 条 | 通过 |

### v1.1-5. 正向用例（摘要）

1. devices/doctor：伪 adb.cmd 输出 device/unauthorized 两类状态；devices --json 返回 {adb_path, devices[{serial,state,details}]}；devices 文本输出；adb 失败 rc4；doctor --transport adb 在无设备/未授权/缺 adb 时 rc4 且提示可操作。
2. 身份绑定：doctor/scan/analyze/plan/selection 的 device_identity.id 完全一致（serial/model/sdk/fingerprint 材料派生），plan_hash/selection_hash 可复算且覆盖身份；execution 记录当前设备身份。
3. PC 备份：--backup-to 在隔离前把文件复制到 <backup>/<execution_id>/sdcard/...，逐文件校验 size + SHA-256，execution.backup_files/backup_bytes、summary、backup-manifest.json、journal 中的 backup_path/backup_sha256 全部一致；delete 模式同样备份后再永久删除；/data 内部缓存备份使用 data 命名空间。
4. CSV 导出：analysis-items.csv 行数 = analysis.items；analysis-files.csv 行数 = analysis.db candidate_files；执行后追加 execution-items.csv 行数 = execution.items；UTF-8-BOM 便于 Excel。
5. PC 端哈希：设备无 sha256sum/md5sum 时 analyze 明确提示可改用 --hash-mode pc；--hash-mode pc 通过 USB 拉取候选到电脑算 SHA-256，正确识别真重复，同大小不同内容不误判，临时 hash-tmp 清理干净；config.scan.hash_mode 可作默认。
6. 扫描历史：第二次 scan 自动把上一轮归档到 <state>/history/scan-*（含 snapshot.db/snapshot-meta.json/scan-summary.md），history 命令列出 current + archived 及各自设备身份；设备切换后两轮历史分别保留 A/B 身份。

### v1.1-6. 反向 / 安全用例（摘要）

1. 设备身份：设备由 A 换成 B（serial/fingerprint/model 变化）后 execute rc=3，错误信息说明设备不一致，不写 execution.json，fixture 完全不变；恢复原设备后同一 token 可继续执行。
2. 断开保护：caps connected=false 时 execute rc=3（不产生执行记录）；运行中 health_ok 变 false 时剩余项目标记 aborted 并写入 execution/report，已处理项生效；文件移动中断开时全部 aborted、源文件保留。
3. 篡改：plan.json 或 selection.json 的 device_identity.id 被改 → 对应哈希不一致，rc=3，文件不动。
4. 备份安全：pull 抛异常 → 跳过清理、源文件保留、不残留半成品（#9 修复后）；size 不符 / SHA 不符 → 删除电脑端半成品并跳过清理；单文件失败不影响同组其它文件；备份目录不可用 → rc=3、零清理；备份成功但清理被 denied_ops 拒绝 → 手机源与电脑副本并存、exit 6。
5. 导出只读：export 只读取 state/analysis.db，要求先 analyze（否则 rc=5）。

### v1.1-7. v1.1 新增产品缺陷与修复状态

| # | 严重度 | 问题 | 发现测试 | 修复状态 |
|---|---|---|---|---|
| 9 | 低 | --backup-to 传输中途异常（USB 断开/设备错误）时，电脑端备份目录残留未校验的半成品文件；源文件保留逻辑正确 | tests/test_device_guard_unit.py::test_backup_partial_copy_is_cleaned_up | 已修复（_backup_file 把 transport.pull 包在 try/except，异常先删除 dest 再抛出），回归通过 |

v1.0 基线中的 8 项缺陷（#1-#8）状态不变，全部已修复且在本轮 178 条全量运行中零回归。

### v1.1-8. v1.1 未覆盖 / 需真机验证项

| 项目 | 原因 / 现状 | 建议 |
|---|---|---|
| 真实 USB/无线 ADB 设备枚举与授权流程 | 本机无 adb/真机；用伪 adb.cmd 与注入 _run 覆盖解析与错误分支，未验证真实 adb server/USB 枚举 | 真机执行 devices/doctor，覆盖 unauth 弹窗、离线、多设备 --serial |
| 真实设备断开（拔线/关 USB 调试） | 用 wrapper 模拟 health_ok/move 失败；真实 adb 的 get-state 延迟、half-open 连接未验证 | 真机执行中拔线演练，核对 aborted 报告与 journal |
| 真实多设备环境 | ensure_ready 多设备逻辑单测通过；真实两台手机同时连接未验证 | 双机冒烟 |
| PC 备份真实吞吐/中断恢复 | mock pull 为本地复制；USB 真实带宽、断电/拔线留下半成品后的恢复策略（#9 只保证清理）未压测 | 真机大文件备份 + 中途拔线 |
| --hash-mode pc 真实 USB 拉取性能 | mock 本地复制；大文件/大量候选的 USB 吞吐与 max_hash_files 预算未实测 | 真机重复照片场景 |
| scan history 保留上限（10 份） | 只验证了 2-3 轮归档；连续 11+ 轮的淘汰逻辑未跑 | 可补快速循环用例或真机多轮 |
| 伪 adb.cmd 依赖 cmd.exe | 仅 Windows 有效（测试内自动 skip 非 Windows）；Linux/macOS 需改用 shell 脚本桩 | CI 跨平台时补 shell 桩 |
| CSV 在 Excel 中的实际打开效果 | 只断言 utf-8-sig 与行数；公式注入/超长字段未验证 | 人工打开抽查 |
| 备份文件的权限/隐私 | 电脑端备份为明文副本；未验证权限继承与用户提示 | 产品文档提示备份目录安全 |

> v1.1 全部数字来自 2026-11-06 12:55 的实际 pytest 输出；新增 64 条用例均以 mock/伪 adb 确定性运行，未依赖 .tmp-smoke、真机或网络。

## 1. v1.0 基线结论（摘要）

| 指标 | 结果 |
|---|---|
| 收集用例 | 116 |
| 通过 | 114 |
| 失败 | 0 |
| 跳过 | 2（本机 Windows 无法创建符号链接 / 目录联接未被 mock 扫描识别） |
| 耗时 | 约 39.7 秒（单进程，无网络、无 adb） |
| 测试命令 | python -m pytest tests -q |
| 最终判定 | 通过。全量套件可重复、确定性运行；发现的产品缺陷 8 项全部已由 Lead 修复并用本套件回归验证 |

安全核心结论：在本套件覆盖的范围内，execute 无令牌/错令牌/被篡改的 plan/selection 均被拒绝；DANGEROUS 与未复核 CAUTION 无法进入选择清单；路径穿越、保护路径、符号链接、计划后文件变化、mock 权限拒绝均被拒绝或跳过并写入报告；隔离/恢复/purge/verify 行为符合 CLI 契约。

## 2. 环境与可复现性

| 项目 | 值 |
|---|---|
| 操作系统 | Windows-11-10.0.26300-SP0（Windows 11） |
| Python | 3.13.3（C:\Python313） |
| pytest | 9.0.2 |
| adb | 未安装（NOT FOUND）——测试不依赖 adb |
| 网络 | 不需要；全部为本地临时目录 fixture |
| 传输层 | mock（--transport mock） |
| 产品版本 | android-deep-clean 1.1.0 |
| 工作区 | C:\Users\11853\Desktop\安卓清理 |
| 报告生成时间 | 2026-11-06 12:03 +08:00 |

复现方式（在仓库根目录执行）：

~~~text
python -m pytest tests -q
~~~

- 全部 fixture 建在 pytest 的 tmp_path 临时目录内，测试结束自动清理；不写仓库、不触碰真实设备。
- 每个用例独立创建 mock 设备树 + state 目录，用例之间无顺序依赖。
- 阈值极小的测试 config 由 tests/adc_testkit.py 的 DEFAULT_CONFIG 提供（duplicate 100B、large 1MB、old/apk/archive/screenshot/download 等 1 天），文件 mtime 统一设为 NOW-5 天（远大于阈值），避免边界抖动。
- 文件内容由 sha256(设备路径) 派生，保证同大小文件内容不同；重复文件用例显式写入逐字节相同内容。

被测产品代码指纹（SHA-256 前 16 位，2026-11-06 12:03 快照，用于审计报告对应版本）：

| 文件 | 指纹 |
|---|---|
| android-deep-clean/scripts/adc.py | F18A1254E07E0447 |
| scripts/adc_lib/analyzer.py | E020B1139B0BDFB6 |
| scripts/adc_lib/cli.py | 1882D3E7A431B2BE |
| scripts/adc_lib/config.py | 602C2B53FACB6978 |
| scripts/adc_lib/device.py | BE6AFD932B644670 |
| scripts/adc_lib/executor.py | 478CF6CB6ACAAF9D |
| scripts/adc_lib/mock_transport.py | 7EA645EDCFBB71B5 |
| scripts/adc_lib/models.py | D284A6CB59B64545 |
| scripts/adc_lib/planner.py | A5C611AD960D7754 |
| scripts/adc_lib/reports.py | B8F190484D680DEB |
| scripts/adc_lib/rules.py | 8D2FC43225893F2E |
| scripts/adc_lib/scanner.py | 981E8BF4FFCA9ACE |
| scripts/adc_lib/state.py | 9B6B5FB17495CA4A |
| scripts/adc_lib/transport.py | ABB5C088472A92A2 |
| scripts/adc_lib/util.py | 0F5EF3FD1619F7ED |
| scripts/adc_lib/__init__.py | 5FFC7D5FB8AD44A3 |

## 3. 真实测试输出

命令：python -m pytest tests -q

~~~text
......................................ss................................ [ 62%]
............................................                             [100%]
=========================== short test summary info ===========================
SKIPPED [1] tests/test_execute.py:388: 当前环境无法创建符号链接（Windows 需要开发者模式/管理员）:
           [WinError 1314] 客户端没有所需的特权
SKIPPED [1] tests/test_execute.py:434: 目录联接未被扫描/分类为缓存目录（平台差异）
114 passed, 2 skipped in 39.68s
~~~

说明：本机控制台对中文输出可能存在 cp936 显示乱码，但 pytest 断言读取的是文件/JSON 的 UTF-8 内容，不影响结果。

分文件用例数（合计 116）：

| 测试文件 | 用例数 | 关注点 |
|---|---:|---|
| tests/test_unit_util.py | 24 | stat 行解析、shell_quote、路径规范化、配置加载、通用工具 |
| tests/test_unit_guard.py | 17 | guard_path 安全策略（系统/数据库/.nomedia/聊天/媒体/空目录等） |
| tests/test_unit_planner.py | 9 | plan/selection 哈希、令牌格式、review 门控 |
| tests/test_doctor.py | 3 | 设备/能力识别、权限降级、报告落盘 |
| tests/test_scan.py | 5 | 统计准确性（os.walk 对照）、只读、隔离区排除、权限不足、截断 |
| tests/test_analyze_categories.py | 13 | 各清理类别识别与分组准确性 |
| tests/test_plan_select.py | 14 | 复核门控、风险策略、篡改拒绝、DANGEROUS 不可选 |
| tests/test_execute.py | 23 | 隔离/永久删除/恢复/purge/verify、权限失败、计划后变化、路径穿越、符号链接 |
| tests/test_cli_contract.py | 8 | 退出码 2/3/4/5/6、前置依赖、inspect/recheck、用户未确认 |

辅助文件：

- tests/adc_testkit.py：mock 设备树构造器（PhoneTree）、CLI 调用封装（AdcEnv/CliResult）、只读快照与差异断言、quarantine/journal 读取、review 生成。
- tests/conftest.py：每个用例一个隔离 env fixture。
- tests/fixtures/README.md：fixture 模式（目录映射、四类 JSON、只读/反向模式）文档。

## 4. 覆盖矩阵（任务需求 → 测试）

| 需求 | 对应测试 | 结果 |
|---|---|---|
| doctor 识别设备/能力 | test_doctor.py::test_doctor_reports_device_packages_and_capabilities | 通过 |
| doctor 权限不足降级 + limitations | test_doctor.py::test_doctor_degrades_when_permissions_and_tools_are_missing | 通过 |
| doctor 报告文件（json/md） | test_doctor.py::test_doctor_writes_json_and_markdown_reports | 通过 |
| scan 条目/大小与 os.walk 精确对照 | test_scan.py::test_scan_stats_match_os_walk | 通过 |
| scan 只读（哈希/mtime/size 清单不变） | test_scan.py::test_scan_and_downstream_read_only_steps_do_not_touch_device | 通过 |
| scan 排除自身隔离区 | test_scan.py::test_scan_excludes_skill_own_quarantine_area | 通过 |
| scan 权限不足记录 skipped | test_scan.py::test_scan_records_inaccessible_subtree_and_analyze_degrades | 通过 |
| scan max_files 截断上报 | test_scan.py::test_scan_max_files_truncation_is_reported | 通过 |
| 卸载残留 Android/data | test_analyze_categories.py::test_uninstall_residue_for_data_obb_and_media | 通过 |
| 卸载残留 Android/obb | 同上 | 通过 |
| 卸载残留 Android/media | 同上 | 通过 |
| 重复文件（同内容不同名/路径）+ 保留建议 | test_analyze_categories.py::test_duplicates_keep_suggestion_and_same_size_different_content | 通过 |
| 不同内容同大小不误判 | 同上 | 通过 |
| 长期大文件 | test_analyze_categories.py::test_large_old_file_is_detected | 通过 |
| 旧 APK | test_analyze_categories.py::test_old_apk_is_detected | 通过 |
| 旧压缩包 | test_analyze_categories.py::test_old_archive_is_detected | 通过 |
| 旧截图 | test_analyze_categories.py::test_old_screenshot_is_detected | 通过 |
| 缓存目录 | test_analyze_categories.py::test_app_cache_group_reports_exact_size_and_file_count | 通过 |
| 缩略图缓存 | test_analyze_categories.py::test_thumbnail_cache_is_detected | 通过 |
| 临时文件/日志 | test_analyze_categories.py::test_temp_logs_are_detected | 通过 |
| 空目录候选与分组 | test_analyze_categories.py::test_empty_dir_candidate_and_grouping | 通过 |
| 微信/QQ cache 且不碰 image2/video 等媒体 | test_analyze_categories.py::test_chat_cache_only_includes_cache_dirs_not_media | 通过 |
| 分组大小与文件数准确/汇总一致 | test_app_cache_group_reports_exact_size_and_file_count + 各分类用例 | 通过 |
| 新鲜文件不被年龄规则误报 | test_analyze_categories.py::test_fresh_files_are_not_flagged_by_age_rules | 通过 |
| 分析报告/review 模板落盘 | test_analyze_categories.py::test_analysis_writes_reports_and_review_template | 通过 |
| 未复核 CAUTION 不可 select | test_plan_select.py::test_unreviewed_caution_is_not_selectable | 通过 |
| review=keep/uncertain 不可 select | test_plan_select.py::test_keep_or_uncertain_review_stays_unselectable（2 参数） | 通过 |
| review=trash 后可 select | test_plan_select.py::test_reviewed_trash_caution_becomes_selectable | 通过 |
| review 输入校验（未知 ID/非法 verdict/缺 reason/降风险/analysis_id 不符/重复） | test_plan_select.py::test_review_validation_rejects_unsafe_or_malformed_input | 通过 |
| select 必须显式范围 + 未知 ID | test_plan_select.py::test_select_requires_scope_and_known_ids | 通过 |
| delete 模式只能选 SAFE | test_plan_select.py::test_delete_mode_selection_rejects_caution | 通过 |
| DANGEROUS 不可 select / 不进 selection | test_plan_select.py::test_selection_never_contains_dangerous_items、test_protected_files_never_become_candidates_or_selections、test_dangerous_item_in_tampered_plan_cannot_enter_selection | 通过 |
| 数据库/.nomedia/密钥/系统路径保护 | test_unit_guard.py 多条 + test_execute.py::test_protected_and_system_paths_in_manifest_are_refused | 通过 |
| plan/selection 报告落盘 | test_plan_select.py::test_plan_and_selection_reports_written | 通过 |
| 篡改 plan 被拒绝且文件不动 | test_plan_select.py::test_tampered_plan_is_rejected_without_touching_files | 通过 |
| 篡改 selection（ids/mode）被拒绝 | test_plan_select.py::test_tampered_selection_ids_or_mode_are_rejected | 通过 |
| 篡改 selection 元数据/summary 被拒绝 | test_plan_select.py::test_tampered_selection_metadata_must_be_rejected + test_unit_planner.py::test_selection_hash_covers_item_metadata_and_summary | 通过 |
| 用户拒绝/未确认不执行 | test_cli_contract.py::test_user_declining_never_executes | 通过 |
| 缺少选择清单 / 无 token / 错 token | test_execute.py::test_execute_requires_selection_and_valid_token | 通过 |
| 隔离执行 + journal + report + verify | test_execute.py::test_quarantine_execute_moves_files_and_verify | 通过 |
| 永久删除模式（需 flag 且 SAFE） | test_execute.py::test_permanent_delete_mode、test_permanent_delete_requires_flag_and_matching_mode | 通过 |
| denied_ops 隔离失败：报失败且源文件仍在 | test_execute.py::test_denied_move_reports_failure_and_keeps_source | 通过 |
| denied_ops 删除失败：报失败且源文件仍在 | test_execute.py::test_denied_delete_reports_failure_and_keeps_source | 通过 |
| 计划后文件改 mtime/大小/被删：跳过并报失败 | test_execute.py::test_changed_or_deleted_files_are_skipped_after_plan | 通过 |
| 计划后目标目录变化：报失败不误删 | test_execute.py::test_missing_target_directory_is_reported_as_failure | 通过 |
| 路径穿越（恶意 manifest）：拒绝 + 报告 | test_execute.py::test_path_traversal_in_manifest_is_refused_with_report | 通过 |
| 符号链接文件拒绝 | test_execute.py::test_executor_refuses_symlink_entry（stub）+ test_execute_refuses_real_symlink_from_tampered_manifest（本机跳过） | 通过/跳过 |
| 父目录符号链接拒绝 | test_execute.py::test_executor_refuses_realpath_redirect（stub）+ test_execute_refuses_parent_directory_link（本机跳过） | 通过/跳过 |
| 文件不在目标目录内/大小/inode 变化拒绝 | test_execute.py::test_executor_refuses_changed_size_mtime_inode、test_executor_refuses_file_outside_target_directory | 通过 |
| 空目录 rmdir 成功 | test_execute.py::test_empty_dir_rmdir_removes_only_empty_directory | 通过 |
| 非空目录 rmdir 失败且不递归 | test_execute.py::test_empty_dir_rmdir_refuses_non_empty_directory_without_recursion | 通过 |
| verify 正确判断（源文件重建/quarantine 丢失） | test_execute.py::test_verify_detects_source_recreated_after_quarantine、test_verify_detects_missing_quarantine_file | 通过 |
| restore 恢复隔离文件（--all / --ids，幂等） | test_execute.py::test_restore_recovers_quarantined_files、test_restore_by_candidate_id | 通过 |
| purge 错令牌拒绝 / 正确令牌永久删除 / 二次 purge | test_execute.py::test_purge_requires_token_and_deletes_permanently | 通过 |
| stat 行解析 | test_unit_util.py::test_parse_stat_line_*（4 组） | 通过 |
| shell_quote | test_unit_util.py::test_shell_quote_posix_single_quote_escaping | 通过 |
| 路径规范化 | test_unit_util.py::test_normalize_device_path_rules、test_display_path_maps_mount_aliases、test_is_under_boundaries | 通过 |
| guard_path 关键规则 | tests/test_unit_guard.py（17 条） | 通过 |
| plan/selection hash + token | test_unit_planner.py（9 条）、test_plan_select.py 相关 | 通过 |
| 退出码契约 0/2/3/4/5/6 | test_cli_contract.py（8 条）+ test_execute 中的 3/6 | 通过 |
| inspect / recheck | test_cli_contract.py::test_inspect_candidate_lists_manifest、test_recheck_creates_new_snapshot_and_comparison | 通过 |

## 5. 正向用例（摘要）

1. doctor：mock 设备 serial/model/sdk、packages 计数、capabilities（privilege/tier/sdcard_read/android_data_read/hashing/quarantine_usable）、df 容量、recommended_roots 与 report-doctor.md 全部与 fixture 一致；doctor 全程只读。
2. scan：条目数 = 根目录 + 目录数 + 文件数，大小之和与 os.walk 独立计算结果逐字节一致；SQLite entries 表路径集合与 os.walk 完全一致；scan/analyze/plan/select 全链路前后 fixture 文件哈希、大小、mtime 清单不变；不产生 execution 记录。
3. analyze：13 类场景各自命中且分组大小/文件数准确（总大小 = 各文件 size 之和；file_count = 文件数；summary 汇总与 items 一致）；重复文件建议保留 DCIM/Camera 中一份，只列出其余副本为删除目标；同大小不同内容不误判；聊天缓存只含 cache/ 子目录，image2/video2 等媒体目录不会进入候选。
4. plan/select：review=trash 后 CAUTION 可选；selection 含 plan_hash/mode/token/selection_hash，token 形如 ADC-XXXXXXXX，用户确认字段 confirmed=false；报告落盘。
5. execute：quarantine 模式移入隔离区且内容逐字节一致，journal 记录 quarantined，verify 全通过；delete 模式永久删除并统计 released_bytes；空目录仅用 rmdir 删除；restore --all/--ids 恢复原路径与内容；purge 后隔离文件与源文件永久消失且 verify/restore 行为正确。
6. recheck/inspect：inspect 输出的文件清单与 analysis.db 候选清单一致；recheck 生成新快照/新分析/对比报告。

## 6. 反向 / 安全用例（摘要）

1. 令牌与确认：无 selection、无 token、错误 token、用户未确认（仅 select 未 execute）均不产生 execution、fixture 完全不变。
2. 篡改：改 plan.json（total_bytes）→ 拒绝（plan hash）；改 selection（增删 item id / 改 mode）→ 拒绝；改 selection 元数据或 summary（total_bytes/bytes/risk/primary_path）→ selection_hash 不匹配拒绝。
3. 风险策略：DANGEROUS 永不进入候选/selection/execute；未复核 CAUTION 不可 select；review=keep/uncertain 不可 select；delete 模式拒绝 CAUTION；review 非法输入（未知 ID、非法 verdict、trash 无实质 reason、降风险、analysis_id 不符、重复 decision）全部 exit 5。
4. 路径类：manifest 注入 /sdcard/../../etc/passwd → 拒绝并报“非法路径”，不越界删除；注入 .nomedia / /system/bin/decoy → 受保护路径拒绝、源文件仍在；stub transport 验证 symlink 类型、realpath 重定向、inode/size/mtime 变化、目标目录外文件全部拒绝。
5. 执行期变化：计划后改 mtime、改大小、删除文件 → 逐文件跳过并在 failures 记录原因，其余文件正常处理，exit 6；目标目录被删除 → 组级失败、exit 6、不误删无关文件。
6. mock 权限失败：denied_ops move/delete → 该文件失败且源文件仍在，其余文件正常，exit 6，report 可见。
7. 扫描权限不足：inaccessible=/sdcard/Android/data → doctor 降级 tier=limited、scan 记录 skipped、analyze 不崩溃且 limitations 含说明。
8. restore/purge：purge 错令牌 exit 3 且隔离文件仍在；正确令牌永久删除后 restore 无文件可恢复、二次 purge 幂等；二次 restore 幂等（0 恢复、0 失败）。
9. 空目录：rmdir 遇到执行前被写入文件 → 失败、目录与文件均保留、不递归删除。

## 7. 发现的产品缺陷与修复状态

验证期间共发现 8 项产品问题（均先以测试固化复现，再修复，最后全量回归通过）。修复均由 Lead 完成，verifier 只读复核并重跑。

| # | 严重度 | 问题 | 发现测试 | 修复状态 |
|---|---|---|---|---|
| 1 | 中 | 聊天缓存候选 app_pkg/app_kind 为空（package_from_path 不识别 /sdcard/tencent、/sdcard/MicroMsg 等聊天根），导致 select --app-kinds chat 永远选不中微信/QQ 缓存 | test_analyze_categories.py::test_chat_cache_only_includes_cache_dirs_not_media | 已修复（新增聊天根→包名映射），回归通过 |
| 2 | 中 | 被修改的 selection 元数据（items.total_bytes / summary.bytes）未被拒绝：旧 selection_hash 只覆盖 plan_hash/ids/mode | test_plan_select.py::test_tampered_selection_metadata_must_be_rejected、test_unit_planner.py | 已修复（selection_hash 覆盖 items 关键字段 + summary，planner/executor 共用 compute_selection_hash），回归通过 |
| 3 | 中低 | 篡改 plan.json 把条目 risk 改为 DANGEROUS 并 selectable=true 后，select 仍将其放入 selection（execute 层会拦，但违反“select 绝不能把 DANGEROUS 放进 selection”） | test_plan_select.py::test_dangerous_item_in_tampered_plan_cannot_enter_selection | 已修复（select 前校验 plan_hash、强制拒绝 DANGEROUS/未知风险），回归通过 |
| 4 | 高 | clean_dir_children 目标目录缺失/被改时，组级失败不计入 failed_files，execute 返回 rc=0 且 status=completed（静默成功） | test_execute.py::test_missing_target_directory_is_reported_as_failure | 已修复（早期失败分支 failed_count=1，summary 增加 failed_groups，CLI 返回 6），回归通过 |
| 5 | 中高 | manifest 注入带 .. 的恶意路径时，执行报告渲染抛异常：CLI rc=3、无 stdout、report-after.md 未生成（execution.json 已写成 partial） | test_execute.py::test_path_traversal_in_manifest_is_refused_with_report | 已修复（reports 全部改用 safe_display_path，永不抛异常），回归通过 |
| 6 | 低 | restore --all 二次执行报失败（原路径已存在），非幂等 | test_execute.py::test_restore_recovers_quarantined_files | 已修复（跳过 restore_status=restored 的条目），回归通过 |
| 7 | 低 | /boot.img、/dtbo.img、/recovery.img 等“/boot 前缀 + 非目录”系统镜像文件未被 hard block 命中 | test_unit_guard.py::test_system_paths_are_hard_blocked | 已修复（HARD_BLOCK_PREFIXES 增加系统镜像文件），回归通过 |
| 8 | 中 | 空目录候选生成错误：gen_empty_dirs 查询未取 kind，CandidateFile 默认 kind=file，导致 guard 以“空目录只接受目录”拒绝全部空目录（空目录清理功能完全失效）；sample_paths 也曾显示父目录 | test_analyze_categories.py::test_empty_dir_candidate_and_grouping、test_execute.py::test_empty_dir_rmdir_* | 已修复（查询带 kind/size/inode/dev；sample_paths 修正），回归通过 |

回归结论：以上 8 项修复后，全量 116 用例 0 失败（2 条符号链接集成用例因本机 Windows 权限跳过，见下节）。

说明：验证过程中另有若干“测试自身假设错误”（例如旧 selection_hash 覆盖范围、plan hash 对已经为 False 的 selectable 再次置 False、测试未创建其断言依赖的文件），均已修正测试而非弱化产品断言，不计入产品缺陷。

## 8. 未覆盖 / 需真机或特殊环境验证

| 项目 | 原因 / 现状 | 建议 |
|---|---|---|
| 真实符号链接文件（os.symlink） | 本机 Windows 无 SeCreateSymbolicLinkPrivilege（WinError 1314），集成用例自动 skip；执行器对 symlink 类型的拒绝逻辑已用 stub transport 单元测试覆盖并通过 | 在开发者模式 Windows 或 Linux CI 上补跑 test_execute_refuses_real_symlink_from_tampered_manifest |
| 父目录符号链接（目录联接） | mklink /J 可创建，但 mock 传输层 _to_device 会把联接解析成目标路径，扫描后不会形成 cache 候选（平台/实现差异），用例 skip；realpath 重定向拒绝逻辑已用 stub 单测覆盖 | 真机上用符号链接目录验证 execute 的 realpath 异常拒绝路径 |
| 真机 adb / rish / local 传输 | 本套件只测 mock；USB/无线调试授权、Shizuku rish、Termux 本机 shell 行为需真机 | 真机冒烟：doctor/scan 只读步骤 |
| Root / su / adb-root 提权 | mock 只能模拟 privilege 字段；真实 su 授权弹窗、adb root 可用性需真机 | userdebug 设备验证 internal-cache 扫描与 /data/data 隔离 |
| Android 11+ scoped storage 真实行为 | 不同 ROM/版本对 /sdcard/Android/data 的 ADB 可见性不同；mock 只能模拟 readable/inaccessible | 在多品牌 ROM 上验证 doctor 探测与 limitations 文案 |
| 真实可用空间变化 df_delta | mock df 为固定值，free_delta 恒 0；真实 purge 后空间变化、系统写入干扰未验证 | 真机 purge 前后统计可用空间 |
| 中断（Ctrl+C, 退出码 130） | 未自动化（Windows 下信号语义不同） | 真机/交互式验证中断时 journal 一致 |
| 真实断连/超时（TransportError 退出码 4） | 仅覆盖 mock root 不存在（exit 4）；adb 断连、命令超时需真机 | 真机拔线/超时演练 |
| 大规模目录性能与 deadline 截断 | 有 max_files 截断单测；75 万文件、1 小时 deadline 的真实性能未跑 | 真机大目录压测 |
| inspect --pull 预览拉取 | 只覆盖 inspect --id/--json；--pull 会写本地预览文件，未覆盖 | 真机只读预览验证 |
| .trashed-* 新版检测规则 | 代码已支持，但本轮套件未新增专门用例（Lead 在验证后期加入） | 建议补一条 .trashed-时间戳 候选用例 |
| 媒体/压缩包内容真伪 | 分类依赖扩展名/文件名规则，不做内容解码；真实损坏文件/伪装扩展名未验证 | 真机抽样 |
| 内容被改但 size+mtime+inode 全相同 | 契约只要求 size/mtime/inode 复核；该场景无法检测（设计限制，非缺陷） | 如需更强保证，可对高风险项增加执行前哈希复核 |
| Windows 命令行退出码显示 | 部分 shell 可能把非 0 显示为 1；测试统一断言 subprocess.returncode（6 等） | 文档中提示 |

## 9. 测试隔离性与只读保证

- 每个用例使用独立 tmp_path：phone/（mock 设备树）、fixtures/（config/packages/caps/device/review JSON）、state/（CLI 状态目录）。
- 只读断言：doctor/scan/analyze/plan/select 前后对整棵 mock 设备树做相对路径到 (kind,size,mtime_ns,sha256) 清单比对，任何新增/删除/改动都会失败。
- 反向用例除断言退出码/报告外，额外断言“源文件仍存在”“未产生 execution.json”“无关文件未被删除”。
- 未修改 android-deep-clean/ 任何文件；测试仅写 tests/** 与本报告。

## 10. 结论

1. android-deep-clean Skill 在 mock 传输下的 CLI 契约实现完整：12 个命令、状态目录结构、退出码 0/2/3/4/5/6 均有测试覆盖并通过。
2. 安全闸门有效：令牌、哈希、风险等级、保护路径、路径穿越、符号链接、执行前 stat 复核、权限失败上报、隔离优先策略均按契约工作；部分失败一律非 0 退出并写入报告。
3. 验证期间发现的 8 项产品缺陷（含 1 项高、2 项中高/中）均已修复并通过回归；最终 114 passed / 0 failed / 2 skipped（均为环境限制的符号链接集成用例）。
4. 剩余风险集中在真机相关能力（adb/rish/root/scoped storage/真实符号链接/性能）；建议按第 8 节清单做一轮真机冒烟后再发布。

> 本报告中的全部数字来自实际命令输出；未为了让测试通过而放宽断言。2 条 skip 原因为运行环境限制，已在第 8 节列出替代覆盖与补跑建议。