# 权限与兼容性说明

本文件面向用户与运维；技术细节与来源见 docs/research-android-storage.md。

## 0. 双通路能力对比

| 能力 | 手机端 local/rish | 电脑端 ADB | Root |
|---|---|---|---|
| 共享存储扫描 | Termux 权限内 / 是 | 是 | 是 |
| Android/data 读取 | 视版本/Shizuku | 通常可（实现细节，OEM 可能收紧） | 是 |
| /data/data 内部缓存 | 否 | 否 | 是 |
| 重复文件哈希 | 手机/本地 | 手机端或 --hash-mode pc | 手机 |
| 电脑备份 | 手动 | --backup-to（校验大小/SHA-256） | --backup-to |
| 多设备防误操作 | 本机 | devices + --serial + device_identity 绑定 | 同 ADB |
| 断连中止 | 进程终止 | health_ok + aborted | 同 ADB |

两条通路使用同一套分类/风险/确认/验证规则。电脑端多设备时未指定 --serial 会直接拒绝；执行前会校验计划设备身份。

## 1. 权限等级

| 等级 | 如何获得 | 可扫描 | 不可扫描 | Skill 行为 |
|---|---|---|---|---|
| Root/SU | Magisk/KernelSU 等，--root-mode su | 共享存储、/data/data/*/cache、内部残留 cache | 系统核心、数据库、运行中应用私有文件仍保护 | full；仍逐项确认 |
| ADB shell（标准） | USB/无线调试 | /sdcard；Android 11+ 通常含 Android/data/obb（实现细节，OEM 可能收紧） | /data/data、/data/media、系统诊断目录 | standard/limited；doctor 实测 |
| Shizuku/rish | Shizuku 启动后 rish | 同 ADB shell | 同 ADB shell | 同 standard/limited |
| SAF/MediaStore | 普通应用/用户选择 | 用户选择的目录/媒体 | Android/data、obb、应用私有、未索引文件 | 不作为本 Skill 主通道 |
| MediaStore（shell 辅助） | ADB shell / rish | 图片/视频/音频/下载媒体索引及元数据 | 未索引文件、应用私有媒体、部分 SD 卡/多用户卷 | v1.4.0 起只读交叉验证；不生成候选、不授权删除 |
| 应用级存储（部分） | ADB shell / rish；root/su 可扩展 | 基础 + 有上限的 split APK、外部 Android/data/media/obb；root/su 下内部 data 的只读 du 抽样 | 普通 shell 的应用内部 data/cache、超出 split 查询上限的应用、其他用户 | v1.10.0 起只读；不生成候选；未知数据不写成 0 |
| 无权限 | 未连接/未授权 | 无 | 全部 | 停止并提示，不假扫描 |

MediaStore 辅助索引（v1.4.0）：

- `scan` 通过 `content query` 查询 `external` 卷的 `images/video/audio/downloads`；使用 shell UID 2000 或 Shizuku shell，不需要 Root，也不绕过系统权限。
- `downloads` 集合仅 Android 10 / API 29+ 尝试；`relative_path`、`is_pending` 为 API 29+；`is_trashed` 为 API 30+。字段被设备拒绝时自动降级到更小投影。
- Termux `local` 通常没有媒体读取权限，会记为不可用；ADB/Shizuku 也可能因 OEM 收紧失败，报告只写差异，不据此删除文件。
- 媒体索引不保证覆盖其他用户、工作资料、隐藏目录、应用私有媒体或所有 SD 卡卷；其大小与文件扫描、应用统计口径不同，不能相加。
- 真机验证：HONOR HLK-AL00（HarmonyOS 3.0 兼容版 / Android 10 / SDK 29）只读通过；图片/视频/音频可采集，`downloads` 集合在该 ROM 返回 unknown URL，已降级为部分可用；路径大小写差异已按共享存储不敏感处理。

应用级存储（v1.10.0）：

- `scan` 使用 `pm list packages -f` 与批量 `stat` 读取基础 APK；对有上限的应用（默认 120 个）执行 `pm path <package>` 识别 split APK；外部 data/media/obb 由文件扫描统计。
- 应用内部 data/cache 需要 StorageStatsManager 权限或 Root。普通 ADB shell 标记 `unavailable`；root/su 且存在 `du` 时，对默认最多 60 个应用做只读 `du` 抽样。本工具不使用会写系统缓存文件的 `dumpsys diskstats`，也不要求安装辅助 APK。
- APK 状态区分 `ok` / `partial` / `permission_denied` / `missing` / `no_path` / `stat_failed`；split 查询有应用数量上限；“已知合计”不是应用总占用，不能与设备已用空间相加。
- 历史真机验证（v1.5.0）：293 个应用，基础 APK 8.98 GB，外部 data 1.92 GB；v1.10.0 的 split 与内部 data 仅完成 mock/单元验证，未重新真机验收。

Android 版本差异：
- Android 10：默认 scoped storage，但 target 29 可临时退出；MediaStore 新增 Downloads 集合。
- Android 11+：强制 scoped + FUSE；Android/data、obb 从 SAF 移除；ADB shell 因 UID 2000 通常仍可访问，但这是实现细节。
- Android 12+：MediaStore 提供 `is_trashed`；回收站条目可能延迟刷新。
- Android 13：媒体权限细分；普通应用相册访问受限。
- Android 14：Selected Photos；普通应用不能全相册批量。
- Android 15：未检索到共享存储权限变更。
- Android 16：MediaStore 版本号按应用隔离；未见 ADB 收紧。

## 2. 兼容性矩阵

| 环境 | 支持 | 说明 |
|---|---|---|
| Windows 10/11 + Python 3.9+ | 完整 | 主要开发/测试环境；adb 通过 PATH 或 --adb-path |
| macOS / Linux + Python 3.9+ | 完整 | 命令同 Windows；路径用引号 |
| Android Termux + --transport local | 受限 | 受 Termux 权限；无 root 不能读他应用私有数据 |
| Shizuku + rish | 标准 | 无电脑场景；能力同 shell |
| Root/SU | 完整 | 内部 cache；风险高，需用户明确同意 |
| 无 adb/无设备 | 仅 mock | 用于开发测试；不代表真机 |
| Python 3.8 及以下 | 不支持 | 使用 3.9+ 语法（list[str] 等） |

## 3. 真机验证项（未验证前不得写成确定事实）

1. adb shell 访问 /sdcard/Android/data、obb 的 OEM 差异；
2. 微信/QQ 当前版本目录（MicroMsg/cache、image2 等）；
3. .trash/.trashed-*/.thumbnails 的实际位置；
4. 非 root 读 /data/anr；
5. Android 15/16 OEM 限制；
6. 不同 ROM 的 toybox find/stat/xargs 参数；
7. 内部隔离区 /data/local/tmp 可写性；
8. 隔离 move 是否同文件系统。

## 4. 性能与规模

- 扫描 10 万级文件通常可接受；百万级可能较慢，建议缩小 --roots 或提高 --max-depth 限制。
- 重复检测只对大小相同的文件计算 SHA-256，默认预算 4GB/5000 文件；超出会报告未完全校验。
- 隔离是逐文件 mv；超大缓存组会较慢，但更安全。隔离区仍占用空间，purge 后才释放。
- 快照使用 SQLite，单次扫描写入 .adc-state/scan/snapshot.db；目录统计写入同一快照的 dir_stats 表。
- 跨轮目录档案写入主机侧 .adc-state/history/archive.db，不需要新增手机权限，也不访问设备。

## 5. 已知不兼容/限制

- 无法读取其他应用 /data/data（非 root）；无法清系统缓存分区。
- 部分 ROM 的 Android/data 对 shell 不可读；doctor 实测决定。
- 电脑端多设备/未授权/离线设备必须先通过 devices + --serial 明确目标；设备身份不匹配会拒绝执行。
- 电脑备份需要电脑磁盘空间；备份校验失败时不会清理源文件。
- 文件名含换行/特殊字符时 stat 行解析可能跳过；报告不做完整保证。
- 相似图片（非完全重复）不自动判重；需要 AI 预览 + 用户确认。
- atime 通常不可靠，年龄按 mtime。
- SA F/MediaStore 删除后媒体库可能延迟刷新；verify 以文件系统为准。
- 不保证覆盖所有 OEM 的私有缓存目录；未知路径一律保留。
