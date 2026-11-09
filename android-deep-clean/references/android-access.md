# Android 访问通道、权限与版本兼容

本文件说明 Skill 为什么只能做这些事，以及权限不足时如何降级。结论基于 2026-11-06 的官方文档与 AOSP 源码研究，详见 docs/research-android-storage.md。标注“需真机验证”的内容不得在报告中写成确定事实。

## 1. 四条真实通道

| 通道 | 典型使用 | 能读共享存储 | 能读 Android/data | 能读 /data/data | 适用 |
|---|---|---|---|---|---|
| ADB shell（UID 2000） | 电脑 + USB/无线调试 | 是 | Android 11+ 通常可读写，但属实现细节，OEM 可能收紧（需真机验证） | 否 | 本 Skill 默认 |
| Shizuku + rish | 手机内授权 shell 权限 | 是 | 同 ADB shell | 否 | 无电脑、Android 11+ |
| root / su | Magisk/KernelSU 等 | 是 | 是 | 是 | 高阶用户；必须显式启用 |
| SAF / MediaStore | 普通应用/用户选择 | 部分 | 否（Android 11+ 从 SAF 移除） | 否 | 不作为本 Skill 主通道 |
| MANAGE_EXTERNAL_STORAGE | all-files 应用 | 全部共享存储（不含 Android/data） | 否 | 否 | 备选；本 Skill 不依赖 |
| Termux local | 手机上运行 Python | 是（Termux 权限范围内） | 视 Android 版本 | 否（除非 su） | --transport local |

关键结论：
- adb shell 不是 root：UID 2000，持有 shell 权限、sdcard_r/sdcard_rw 补充组；AOSP FUSE 对 uid<10000 放行，所以 Android 11+ 通常能读写 Android/data/obb，但这不是 API 契约。
- /data/data 只有 root（或 debuggable 应用的 run-as）可读。
- adb root 只在 ro.debuggable=1（userdebug/eng）构建可用；user 构建会拒绝。
- MANAGE_EXTERNAL_STORAGE 仍不能写 /sdcard/Android/data，也不能读他应用内部数据。
- Android 13 起媒体权限细分；Android 14 引入“部分照片”授权；这些限制针对普通应用，ADB shell 不直接受媒体权限约束。
- Android 15 未见共享存储权限模型变更；Android 16 仅 MediaStore 版本号按应用隔离。OEM 差异需真机验证。

## 2. 版本对照（对清理能力的影响）

| Android | API | 要点 | Skill 行为 |
|---|---|---|---|
| 5–9 | 21–28 | legacy 外部存储；老设备路径直接可见 | 仍走统一 CLI；权限按实际探测 |
| 10 | 29 | 默认 scoped storage；requestLegacyExternalStorage 可临时退出 | 不假设；doctor 探测 Android/data 可读性 |
| 11 | 30 | 强制 scoped；FUSE；Android/data、obb 从 SAF 移除；all-files 出现 | 权限分水岭；内部 cache 仅 root |
| 12 / 12L | 31/32 | FUSE passthrough；无权限模型变化 | 同 11 |
| 13 | 33 | READ_MEDIA_* 细分 | 普通应用读相册受限；shell 不受影响 |
| 14 | 34 | Selected Photos | 普通应用不能全相册批量；需逐项 |
| 15 | 35 | 未检索到共享存储变更 | 同 14 |
| 16 | 36 | MediaStore#getVersion 按应用唯一 | 不要依赖 MediaStore 版本做判断 |

## 3. 权限探测与降级（doctor）

doctor 会实际执行并记录：
- 设备型号、Android 版本、SDK、构建类型、ABI；
- 当前身份（shell / su / root）；
- /sdcard、Android/data、Android/obb、Android/media、DCIM、Download、/data/data、/data/local/tmp 的存在/可读/可写/可列举；
- find、stat、xargs、sha256sum、df、pm、realpath、rm、mv、rmdir、su 是否可用；
- 存储总量/已用/可用；
- 限制清单（limitations）。

反例：
- 如果 /sdcard/Android/data 无法列举：报告“应用外部缓存/残留不可见”，不要生成该类别候选；不得声称“全盘已扫描”。
- 如果 pm 不可用：跳过卸载残留检测，并说明“无法可靠判断包是否安装”。
- 如果 sha256sum/md5sum 不可用：重复文件只按大小分组，必须标注“不能确认完全重复”。
- 如果 /data/data 不可读：报告内部应用缓存不可见；不要用 root 提权去绕过用户未同意的限制。

## 4. ADB 配置（Windows）

1. 手机：设置 → 关于手机 → 连点版本号 7 次 → 开发者选项 → USB 调试。
2. 电脑：安装 Android Platform Tools，确认 adb version 可用；数据线连接；手机上确认 RSA 指纹。
3. 验证：adb devices 显示 device；adb shell getprop ro.build.version.sdk 返回数字。
4. 无线调试（Android 11+）：开发者选项 → 无线调试 → 使用配对码；adb pair IP:PORT 输入配对码；adb connect IP:DEBUG_PORT；adb devices。
5. 多设备：用 --serial <序列号> 指定，或在 doctor 中确认目标。
6. 常见坑：USB 模式仅充电、授权弹窗未点允许、OEM 额外开关（USB 调试安全设置/无线调试验证）、ADB 服务被安全软件拦截。

## 5. Shizuku / rish

- Shizuku 通过 app_process 启动服务，提供 shell UID 级权限；不提供 root。
- 安装 Shizuku 并按应用内指引启动（无线调试或 root 启动）。
- 在终端安装 rish（Shizuku 提供脚本）后，可用 --transport rish --rish-path rish。
- 能力同 ADB shell：共享存储、部分 Android/data；不能读 /data/data。
- 若 rish 命令不可用，Doctor 会失败；改用 adb 或 Termux。

## 6. Root / su

- 仅在用户明确选择高阶清理时使用；--root-mode su 让所有设备命令走 su -c。
- Root 能力大、风险高：必须仍走 guard_path、候选复核、用户逐项确认、隔离执行；不得因为 root 就批量删除 /data/data。
- adb root（userdebug）只影响 adbd 身份；--try-adb-root 仅在 doctor 显式启用时尝试，并会记录结果。
- 不自动 root、不安装 Magisk、不修改系统分区、不 remount。

## 7. Termux local

- 在 Android 上安装 Termux + Python 3.9+，把 Skill 复制到 Termux 可读目录。
- 运行 --transport local；getprop/pm 等命令可能因 Termux 环境不同失败，doctor 必须如实报告。
- Termux 默认只能访问自己的目录与共享存储；没有 root 时不能读其他应用私有数据。

## 8. 媒体与 SAF 说明

- SAF 不能让本 Skill 全盘读取 Android/data 或应用私有缓存；它不是本 Skill 的扫描通道。
- v1.4.0 起，ADB shell / Shizuku 可用 `content query` 只读读取 MediaStore 的图片/视频/音频/下载索引；它作为文件系统扫描之外的辅助证据，不替换 find，也不授权删除。
- `downloads` 集合需要 API 29+；`relative_path`/`is_pending` 为 API 29+，`is_trashed` 为 API 30+。字段不支持时降级投影，权限或命令失败时记录为不可用。
- MediaStore 无法可靠读出“未被媒体库索引的文件”；Termux local 通常无媒体权限。HarmonyOS NEXT 没有 Android MediaStore，不执行该查询。
- 删除共享媒体后 MediaStore 可能有延迟/幽灵条目；verify 以文件系统 stat 为准，并说明媒体库刷新可能滞后。交叉核对中的“索引有、扫描无”不能作为文件已删除的结论。

## 9. 真机验证清单

交付/使用前，至少在一台目标设备上确认：
1. adb shell ls /sdcard/Android/data 是否可读；能否读某个应用 cache；
2. adb shell ls /sdcard/Android/obb 是否可读；
3. pm list packages -u 输出是否包含已卸载包；
4. find -print0 + xargs + stat 组合是否可用（部分 ROM 的 toybox 版本差异）；
5. 微信/QQ 当前版本目录结构（MicroMsg/cache、image2 等）；
6. .trash/.trashed-*/.thumbnails 的实际位置；
7. root/su 下 /data/data/<pkg>/cache 是否可读；
8. 隔离区 /sdcard/.android-deep-clean 是否可创建、move 是否同文件系统；
9. /data/local/tmp 是否可写（内部隔离）；
10. 清理后存储可用空间变化是否符合预期。

## 10. 明确不做

- 不申请/不依赖 MANAGE_EXTERNAL_STORAGE；不引导用户关闭 scoped storage。
- 不自动 root；不修改系统分区；不 patch 系统服务。
- 不访问其他应用账号、数据库、密钥；不导出聊天记录。
- 不承诺覆盖所有 OEM 的私有目录。
