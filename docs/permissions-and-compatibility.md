# 权限与兼容性说明

本文件面向用户与运维；技术细节与来源见 docs/research-android-storage.md。

## 1. 权限等级

| 等级 | 如何获得 | 可扫描 | 不可扫描 | Skill 行为 |
|---|---|---|---|---|
| Root/SU | Magisk/KernelSU 等，--root-mode su | 共享存储、/data/data/*/cache、内部残留 cache | 系统核心、数据库、运行中应用私有文件仍保护 | full；仍逐项确认 |
| ADB shell（标准） | USB/无线调试 | /sdcard；Android 11+ 通常含 Android/data/obb（实现细节，OEM 可能收紧） | /data/data、/data/media、系统诊断目录 | standard/limited；doctor 实测 |
| Shizuku/rish | Shizuku 启动后 rish | 同 ADB shell | 同 ADB shell | 同 standard/limited |
| SAF/MediaStore | 普通应用/用户选择 | 用户选择的目录/媒体 | Android/data、obb、应用私有、未索引文件 | 不作为本 Skill 主通道 |
| 无权限 | 未连接/未授权 | 无 | 全部 | 停止并提示，不假扫描 |

Android 版本差异：
- Android 10：默认 scoped storage，但 target 29 可临时退出。
- Android 11+：强制 scoped + FUSE；Android/data、obb 从 SAF 移除；ADB shell 因 UID 2000 通常仍可访问，但这是实现细节。
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
- 快照使用 SQLite，单次扫描写入 .adc-state/scan/snapshot.db。

## 5. 已知不兼容/限制

- 无法读取其他应用 /data/data（非 root）；无法清系统缓存分区。
- 部分 ROM 的 Android/data 对 shell 不可读；doctor 实测决定。
- 文件名含换行/特殊字符时 stat 行解析可能跳过；报告不做完整保证。
- 相似图片（非完全重复）不自动判重；需要 AI 预览 + 用户确认。
- atime 通常不可靠，年龄按 mtime。
- SA F/MediaStore 删除后媒体库可能延迟刷新；verify 以文件系统为准。
- 不保证覆盖所有 OEM 的私有缓存目录；未知路径一律保留。
