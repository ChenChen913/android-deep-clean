# Android 存储与权限研究报告：面向“AI 手机深度清理 Skill”的设计依据

> 研究日期：2026-11-06　|　范围：Android 5.0–16（API 21–36）　|　任务：Task-2（Team 安卓清理）
>
> 标注约定：**✅** = 已由官方文档 / AOSP 源码核实；**⚠️** = 依赖实现、OEM 或版本差异，**必须真机验证**。
> 取证方式：Google 官方文档经 developer.android.google.cn / source.android.google.cn 镜像读取（本机网络无法直连 developer.android.com，内容同源）；AOSP 源码经 aosp-mirror GitHub 镜像读取。所有 URL 访问日期均为 2026-11-06。

## 0. 结论先行

1. **只有四条真实技术通道**：SAF/MediaStore（普通应用）、MANAGE_EXTERNAL_STORAGE（all-files，Play 上架受限）、ADB shell（UID 2000）、root/Shizuku。除 root 外，没有任何通道能读 `/data/data`、`/data/media` 或他应用的私有目录。
2. **scoped storage 自 Android 11（API 30）起强制**：应用默认只能写自己的 `Android/data/<pkg>`、`Android/media/<pkg>` 与媒体集合；`Android/data`、`Android/obb` 已从 SAF 目录选择器中移除（✅ Android 11 文档）。
3. **FUSE 是 Android 11+ 的强制网关**：MediaProvider 逐次文件操作判定 allow / deny / redact（✅ AOSP scoped storage 文档）；Android 10 只对 MediaStore 路径生效，直连文件路径不受控（✅）。
4. **adb shell = UID 2000**，持有 Shell 应用权限与 sdcard_r / sdcard_rw 等补充组（✅ AOSP 源码）；AOSP FUSE 对 uid < 10000 直接放行（✅ FuseDaemon.cpp）。因此 Android 11+ 上 adb shell 通常可读写 `/sdcard/Android/data` 与 `/sdcard/Android/obb`；但这是**实现细节而非 API 契约**，OEM 可收紧（⚠️）。
5. **`/data/data`（他应用）adb shell 与 Shizuku 均不可读**：应用目录 0700 + SELinux 隔离；只有 `run-as`（仅 debuggable 应用）或 root 能读（✅ app-specific 文档；run-as 细节 ⚠️）。
6. **MANAGE_EXTERNAL_STORAGE 不等于万能**：可读写共享存储全部文件、`Android/media`、MediaStore.Files；但明确不能写 `/Android/data`、`/sdcard/Android`，也不能访问他应用 app-specific 目录（✅ manage-all-files 文档）。
7. **媒体权限逐版本收紧**：Android 13 起细分为 READ_MEDIA_IMAGES / VIDEO / AUDIO（✅）；Android 14 起 target 34 默认“部分照片”授权（READ_MEDIA_VISUAL_USER_SELECTED）（✅）；Android 15 未检索到共享存储权限变更（✅）；Android 16 仅有 MediaStore#getVersion 按应用隔离（✅）。
8. **真正可 SAFE 批量删除的类别很少**：可再生成缩略图（.thumbnails）、明确的临时/日志文件、已安装同版本及以上的安装包、系统回收站 .trashed-*、严格白名单下的空目录。微信/QQ 的 MicroMsg、files、emoji 语义混杂，必须逐项。
9. **绝不能碰**：`/data`、`/system`、`/vendor`、`/data/media`、`/mnt/media_rw`、`Android/obb`、他包 `Android/data`、`.nomedia` 所在目录、MediaProvider 内部目录（`.transforms`、`.picker_transcoded`）、`DCIM/Camera` 原片、数据库/密钥/账号目录（✅ AOSP FileUtils + 平台文档）。
10. **开源可借鉴对象**：SD Maid 2/SE（GPL-3.0，功能与特权分层最完整，代码不可闭源复用）、SpaceSweep（思路极佳，但仓库无 LICENSE，代码不可复用）、Android Device Optimizer（多为 ADB/root 调优而非清理，许可证各异）；Shizuku（Apache-2.0）可作为可选特权执行层。
11. **Skill 的胜负手不是“能删多少”**，而是：权限能力诚实上报、删除前可复核、失败/跳过有原因、不可恢复操作有明确警告、清理量可量化复测。
12. **所有“微信/抖音/QQ 路径”都随版本与 ROM 变化**，本报告给出候选路径；交付前必须真机确认（⚠️）。

## 1. 存储模型与版本演进

### 1.1 关键路径映射

| 路径 | 实质 | 访问者 |
|---|---|---|
| `/sdcard` | 兼容符号链接；init 环境变量 EXTERNAL_STORAGE=/sdcard（✅ init.environ.rc.in） | 所有人 |
| `/storage/emulated/0` | 用户 0 的主共享存储（emulated volume） | scoped 规则下所有人 |
| `/storage/self/primary` | 进程视角的“当前用户主存储”，由 mount namespace 绑定（✅ AOSP traditional storage） | 每应用/用户隔离 |
| `/data/media/0` | 上述卷的真实后端目录，仅 root 可直读；FUSE 服务以 media_rw(1023) 运行并 `-l /data/media`（✅ AOSP storage config 示例） | root / system |
| `.../Android/data/<pkg>` | 应用**外部**私有目录，卸载一般随应用删除（✅ app-specific 文档） | 本应用 / shell(实现细节) / root |
| `.../Android/obb/<pkg>` | OBB 大文件目录，多用户间可共享（✅ traditional storage） | 本应用 / root；其他应用禁止 |
| `.../Android/media/<pkg>` | 共享存储的一部分（✅ all-files 文档） | 本应用无权限可写；all-files 应用可读写 |
| `/storage/XXXX-XXXX` | 可移动 SD 卡卷（portable / adoptable） | scoped 规则 |
| `/data/data`、`/data/user/0/<pkg>` | 应用内部私有存储（0700，Android 10+ 加密）（✅ app-specific 文档） | 仅本应用 / root |
| `/data/local/tmp` | shell 可写调试目录；应用受 SELinux 限制（⚠️） | shell / root |
| `/data/anr`、`/data/tombstones`、`/data/system/dropbox` | 系统诊断日志 | root（⚠️ 非 root 可用 `adb bugreport` / `dumpsys dropbox` 替代） |

### 1.2 版本 / 权限对照表（API 21–36）

| 版本 | API | 存储模型要点 | 对清理 Skill 的含义 |
|---|---|---|---|
| Android 5.0 / 5.1 | 21 / 22 | legacy 外部存储，安装时授予 WRITE_EXTERNAL_STORAGE 即近乎全盘 | 老机型可直接按路径清；现代设备已无此模型 |
| Android 6.0 | 23 | 运行时权限（READ/WRITE_EXTERNAL_STORAGE）；引入 adoptable storage（✅ AOSP storage） | 需动态申请；可合并 SD 卡为内部存储 |
| Android 7.0 / 7.1 | 24 / 25 | legacy 存储；file:// URI 共享受 FileUriExposed 限制 | 老路径仍可见（如 /sdcard/tencent） |
| Android 8.0 / 8.1 | 26 / 27 | SDCardFS 取代旧 FUSE（性能），Android 11 又回归 FUSE（✅ scoped 文档） | 路径语义不变 |
| Android 9 | 28 | 禁止应用把内部 data 目录设为 world-accessible（✅ Android 11 文档回溯） | shell/其他应用读取 app 内部数据更难 |
| Android 10 | 29 | 默认 scoped storage；可用 requestLegacyExternalStorage 临时退出；MediaStore.Downloads；app 目录加密（✅） | target 29 应用仍可能“全盘可写” |
| Android 11 | 30 | 强制 scoped；FUSE 全面接管；Android/data、Android/obb 从 SAF 选择器移除；all-files access；媒体可用直接路径；WRITE_EXTERNAL_STORAGE / WRITE_MEDIA_STORAGE 不再增益（✅） | **清理权限分水岭**；all-files 与 shell 成为关键通道 |
| Android 12 | 31 | 支持 FUSE passthrough（内核支持时，性能接近直读）（✅ AOSP）；未见新共享存储权限 | 行为与 11 基本一致 |
| Android 12L | 32 | 无存储模型变化 | 同 12 |
| Android 13 | 33 | READ_MEDIA_IMAGES / VIDEO / AUDIO 细分；READ_EXTERNAL_STORAGE 对他应用媒体失效（✅ behavior changes 13） | 读相册需分类申请；shell 不受影响 |
| Android 14 | 34 | Selected Photos Access（部分照片）；target 34+ 未适配则进入兼容模式（✅ behavior changes 14） | 普通应用“全相册清理”能力被削弱 |
| Android 15 | 35 | 官方 behavior changes 未检索到共享存储权限变更（✅ 页面检索） | 清理能力同 14；OEM 差异需验证（⚠️） |
| Android 16 | 36 | MediaStore#getVersion 改为按应用唯一（反指纹）；未见新存储权限或 ADB 收紧（✅ behavior changes 16） | 不要依赖 MediaStore 版本号做行为判断 |

### 1.3 scoped storage / SAF / MediaStore / all-files 要点

- **Android 10**：target 29 且未 opt-out 的应用只能看到自己的 app 目录、自己创建的媒体，公共集合需 MediaStore；`requestLegacyExternalStorage` 可临时退出，target 30 后被忽略（✅ Android 11 文档）。
- **Android 11+**：SAF 的 ACTION_OPEN_DOCUMENT / OPEN_DOCUMENT_TREE 不再能选中 `Android/data` 与 `Android/obb`（✅ 文档原文）；应用可对媒体使用直接文件路径（File API / NDK）（✅）。
- **All-files access**：MANAGE_EXTERNAL_STORAGE + ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION + Environment.isExternalStorageManager()；授予后可读写共享存储、`/sdcard/Android/media`、MediaStore.Files、OTG/SD 根目录；但写不到 `/Android/data`、`/sdcard/Android` 及其多数子目录；SAF 不会因此扩大（✅ manage-all-files 文档）。Play 上架需申报，可能影响发布（✅ Android 11 文档提示）。
- **MediaStore**：Images 来自 DCIM/Pictures；Video 来自 DCIM/Movies/Pictures；Audio 来自 Alarms/Audiobooks/Music/Notifications/Podcasts/Ringtones；Downloads 表仅 Android 10+；Files 表内容随 scoped 变化（✅ shared media 文档）。系统扫描与数据库是删除后“幽灵缩略图”的根源。
- **FUSE**（Android 11+）：MediaProvider 在用户态逐操作决定 allow / deny / redact，同时保留直接文件路径兼容性（✅ AOSP scoped 文档）；Android 12 起支持 passthrough（✅）。

### 1.4 六条通道能力矩阵

| 通道 | 读共享媒体 | 访问他应用 Android/data、obb | 读他应用 /data/data | 清他应用缓存 | 识别卸载残留 | 可用版本 |
|---|---|---|---|---|---|---|
| SAF / MediaStore | 部分（用户选择或媒体权限） | 否 | 否 | 否（只能跳系统界面） | 否 | 4.4+ |
| MANAGE_EXTERNAL_STORAGE | 全部共享存储（含 Android/media） | **否**（平台明确禁止写 /Android/data） | 否 | 只能删共享存储可见文件 | 部分（需包列表配合） | 11+ |
| ADB shell（UID 2000） | 全部共享存储 | 通常可以（FUSE uid<10000 放行，实现细节 ⚠️） | 否 | 可删外部缓存目录 | 可（`pm list packages -u`） | 全版本；无线调试需 11+ |
| Shizuku（非 root） | 同 ADB shell | 同 ADB shell | 否 | 可删外部缓存目录 | 可 | 6.0+；机内启动需 11+ |
| root / su | 全部 | 全部 | 全部 | 全部 | 全部 | 全版本（需解锁 BL 等） |
| 系统设置/应用内清理 | 用户手动 | 否 | 否 | 是（系统 API） | 部分 | 全版本 |

## 2. ADB shell（UID 2000）到底能访问什么

### 2.1 adb shell 的身份构成（关键）

- **UID/GID = 2000（shell）**：`Process.SHELL_UID = 2000`（✅ AOSP Process.java）。
- **补充组**：adbd 以 minijail 切到 AID_SHELL，并加入 AID_ADB、AID_LOG、AID_INPUT、AID_INET、AID_NET_BT、AID_NET_BT_ADMIN、**AID_SDCARD_R、AID_SDCARD_RW**、AID_NET_BW_STATS、AID_READPROC、AID_UHID（✅ adb daemon main.cpp，android-11 源码）。
- **Shell 应用权限**：com.android.shell 为 coreApp、sharedUserId=android.uid.shell，清单里声明 READ/WRITE_EXTERNAL_STORAGE、MANAGE_EXTERNAL_STORAGE（注释：供 FuseDaemonHostTest 存储测试）、DUMP、PACKAGE_USAGE_STATS 等（✅ Shell AndroidManifest）。
- **FUSE 放行**：`is_app_accessible_path()` 中 `if (uid < AID_APP_START || uid == MY_UID) return true;`（AID_APP_START=10000），即 root/system/shell 等特权 UID 不受 `Android/data|obb/<pkg>` 所有者校验约束（✅ FuseDaemon.cpp，AOSP main/master）。
- 结论：**adb shell 的能力 = UID 2000 + Shell 权限 + FUSE 放行 + SELinux 域**；它绕过 app 的 scoped 限制，但远小于 root（读不到他应用内部数据、/data/media、/data/anr 等）。

### 2.2 逐路径访问边界（Android 5–16）

| 路径 | Android 5–10 | Android 11–16 | 说明 |
|---|---|---|---|
| `/sdcard` 顶层与用户目录 | shell 可读写 | shell 可读写 | sdcard_rw 组 + FUSE；✅ |
| `/sdcard/Android/data` | 可读写（无专项限制） | **通常可读写** | AOSP FUSE 放行 uid<10000；⚠️ 实现细节，OEM 可能收紧 |
| `/sdcard/Android/obb` | 可读写 | **通常可读写** | OBB 需应用安装后才挂载/可见；⚠️ |
| `/data/data/<pkg>`、`/data/user/0` | 不可 | 不可 | 0700 + SELinux；`run-as` 仅 debuggable（⚠️） |
| `/data/local/tmp` | shell 可读写 | shell 可读写 | 应用访问受 SELinux 限制（⚠️） |
| `/data/media`、`/mnt/media_rw` | 不可 | 不可 | root 可访问 |
| `/data/anr`、`/data/tombstones`、`/data/system/dropbox` | 通常不可 | 通常不可 | 用 `adb bugreport` / `dumpsys` 间接获取（DUMP 权限，⚠️） |
| SD 卡 `/storage/XXXX-XXXX` | shell 可读写 | shell 可读写 | 应用侧受 scoped 约束 |
| `/system`、`/vendor`、`/apex` | 只读 | 只读 | 仅 root 可 remount（不建议） |

补充：**Shizuku/ADB 对 `/data/data` 的帮助有限**——它们能执行 `pm clear`、`appops`、`pm list packages -u` 等系统命令，但无法像 root 一样直接读文件内容。

### 2.3 adb root 与 su 的差别

- **adb root**：只有在 `ro.debuggable=1`（eng / userdebug）构建上才会保持 root；user 构建会提示 adbd cannot run as root in production builds。逻辑：`ro.secure` 默认丢弃权限，`ro.debuggable` + `service.adb.root=1` 才不丢；`adb unroot` 恢复（✅ AOSP adb daemon main.cpp 源码注释与实现）。
- **su**：需要 Magisk/KernelSU/APatch 等 root 方案（通常需解锁 bootloader），以 UID 0 新进程执行命令；可读 `/data`、`/data/media`、`/data/data`，风险与保修成本最高。
- **adb root 仍受 adbd 的 SELinux 域约束**，并非完整 root 环境（⚠️ 逐场景验证）。
- 对 Skill：优先 **ADB shell**（零设备风险、可审计），其次 **Shizuku**，root 仅作显式高阶通道并要求用户二次确认。

### 2.4 无线调试配对流程（Android 11+）

1. 设备：设置 → 关于手机 → 连点版本号 7 次开启开发者选项 → 开发者选项 → 无线调试 → 允许无线调试（可信网络可勾选“始终允许”）（✅ adb 文档）。
2. 设备：无线调试 → “使用配对码配对设备”，记录显示的 IP、端口、6 位配对码（✅）。
3. 电脑：`adb pair <ip>:<port>`，按提示输入配对码；成功后 `adb connect <ip>:<调试端口>`，再 `adb devices` 确认（✅）。
4. 版本要求：手机 Android 11+；TV / WearOS 需 Android 13+（✅ adb 文档）。Android 11 之前只能用 USB 连接电脑。
5. 注意：无线调试端口与配对端口通常不同；配对与 USB 调试授权是两套机制；OEM 可能追加“USB 调试（安全设置）”“无线调试需登录账号/验证码”等开关（⚠️）。

### 2.5 Shizuku 与 rish 的权限模型

- **原理**：Shizuku 用 app_process 以 **shell（adb）或 root** 身份启动 server 进程，普通应用通过 binder 把调用转发给系统服务，从而“借用”更高权限（✅ Shizuku README）。
- **权限等价物**：非 root 时能力 = UID 2000 + Shell 应用权限；不同 Android 版本 ADB 权限不同（Shizuku README 直接引用 Shell 权限清单）→ **Android 11+ 与 13+ 的 Shizuku 能力并不相同**（⚠️ 逐版本验证）。
- **启动成本**：root 模式服务自动启动；非 root 每次重启后需重新启动，Android 11 之前必须连电脑，Android 11+ 可用机内无线调试启动（✅ Shizuku-API README）。
- **rish**：通过 Shizuku/Sui 后端执行 shell 命令的工具，`rish -c 'ls'` 等价于远端 `/system/bin/sh -c 'ls'`；adb 后端可访问路径受限（✅ rish README）。
- 对 Skill：Shizuku 是“免 root 的高权限通道”，但**不能读他应用 /data/data**；可用于 `pm`/`cmd`/`appops` 类操作与更完整的共享存储访问（⚠️ 能力逐版本确认）。

### 2.6 Android 13/14/15/16 的变化（shell 视角）

- Android 13：媒体权限细分影响普通应用读相册；AOSP 主线 FUSE 仍对 uid<10000 放行，未见 shell 收紧（✅ 源码 + ⚠️ OEM）。
- Android 14：Selected Photos Access 影响应用相册读取；对 shell 无新增限制（检索范围内）。
- Android 15：官方 behavior changes 未检索到共享存储相关条目；清理能力按 14 对待（⚠️）。
- Android 16：MediaStore#getVersion 按应用隔离；未见对 adb shell 的新限制（检索范围内）；不要依赖 MediaStore 版本推断系统行为。
- 各 OEM（HyperOS/MIUI、ColorOS、OriginOS）对无线调试、后台 ADB、安装权限普遍有附加限制（⚠️ 必须真机验证）。

### 2.7 Skill 可用的关键 ADB / Shizuku 命令（只读优先）

| 目的 | 命令（示例） | 风险 |
|---|---|---|
| 查看剩余空间 | `adb shell df -h /data /storage/emulated` | 只读 |
| 系统磁盘统计 | `adb shell dumpsys diskstats` | 只读，字段随版本变化（⚠️） |
| 列出全部包（含保留数据卸载的包） | `adb shell pm list packages -u --user 0` | 只读；需 QUERY_ALL_PACKAGES 才能看到未安装包（✅） |
| 仅第三方包 | `adb shell pm list packages -3` | 只读 |
| 目录/文件大小 | `adb shell du -sh <dir>` | 只读但慢，注意超时与 IO |
| 媒体清单 | `adb shell content query --uri content://media/external/images/media --projection _id:_data:_size` | 只读；需媒体权限/版本差异（⚠️） |
| 删除前复核 | `adb shell stat -c '%n|%s|%Y' <path>` | 只读 |
| 精确删除（禁止通配递归） | `adb shell rm -f <单个文件绝对路径>` | 高危，必须先 dry-run + 复核 |
| 崩溃/系统日志读取 | `adb shell dumpsys dropbox --print` | 只读；依赖 DUMP 权限（⚠️） |
| 明确禁止 | `pm clear`（清应用数据）、`pm uninstall -k`（保留数据卸载制造残留）、对 /data 的写操作 | 会造成不可恢复的数据丢失 |

## 3. 常见清理目标的真实路径与误删风险

分级：**SAFE** 可批量；**CAUTION** 必须逐项确认；**DANGER** 禁止或需强确认。

| 类别 | 真实路径（候选） | 访问方式 | 风险与结论 | 级别 |
|---|---|---|---|---|
| 微信缓存 | `Android/data/com.tencent.mm/` 下 cache、temp、emoji 等子目录 | ADB shell / Shizuku / all-files | cache/temp/emoji 多为可再生成；但 MicroMsg、files 含聊天记录与附件，**删了不可恢复**；路径随版本变化（⚠️） | CAUTION（仅白名单子目录） |
| 微信聊天数据 | `Android/data/com.tencent.mm/MicroMsg/`（旧版可能在 `/sdcard/tencent/MicroMsg`） | 同上 | **DANGER**：数据库/附件/语音；绝不做“清理缓存”处理 | DANGER |
| QQ 文件 | `Android/data/com.tencent.mobileqq/Tencent/QQfile_recv`（腾讯客服官方公告，✅） | ADB shell / Shizuku | 内含用户接收文件；只能按“最旧/最大/已下载”逐项 | CAUTION |
| 浏览器缓存 | `Android/data/<浏览器包名>/cache`；`Download/` | ADB shell / SAF | cache 可清；书签/历史/密码库在 `/data/data`，禁止触碰 | SAFE（缓存）/ DANGER（数据） |
| 抖音缓存 | `Android/data/com.ss.android.ugc.aweme/cache`、`files`；作品保存常在 `DCIM/Camera`（⚠️） | ADB shell | cache 可清；files 可能含草稿/下载；保存目录疑似原片 | SAFE（cache）/ CAUTION（files） |
| 快手缓存 | `Android/data/com.smile.gifmaker/cache` 等（⚠️） | ADB shell | 同抖音策略 | SAFE（cache）/ CAUTION（files） |
| 卸载残留 | `Android/data/<pkg>`、`Android/obb/<pkg>`、`Android/media/<pkg>`，包名来自 `pm list packages -u` | ADB shell / Shizuku | MATCH_UNINSTALLED_PACKAGES 语义：仅在“保留数据卸载”后出现，需要 QUERY_ALL_PACKAGES 才能看到（✅ AOSP PackageManager）；必须与已安装包名对账，否则误删在用应用数据 | CAUTION |
| APK 安装包 | `Download/*.apk`、`Android/data/*/*.apk` | SAF / ADB shell | 仅当已安装版本 ≥ APK 版本才可清（SpaceSweep 规则，可借鉴）；保留最新安装包 | SAFE（有版本比对）/ CAUTION |
| 缩略图 | `Pictures/.thumbnails`、`Movies/.thumbnails`、`Music/.thumbnails`（✅ AOSP FileUtils 保护规则）；`DCIM/.thumbnails`（⚠️） | SAF / ADB shell | 系统会重建，**SAFE**；但不要删除 `.thumbnails` 之外的 DCIM 原片 | SAFE |
| 日志 / ANR / tombstone | `/data/anr`、`/data/tombstones`、`/data/system/dropbox`；应用日志在 `Android/data/<pkg>/files/logs` | root；非 root 用 bugreport | 系统日志仅 root 可删；应用日志可能用于排障/崩溃上报 | CAUTION（系统侧 DANGER for 非 root 误操作） |
| 空目录 | 共享存储用户目录下的空文件夹 | SAF / ADB shell | 删除前复查；`Android/`、隐藏目录、标准目录（DCIM/Download/Music 等）永不判定为空目录 | SAFE（严格白名单） |
| 系统回收站 | 以 `.trashed-<时间戳>-原名` 命名的文件（✅ AOSP FileUtils）；可能在 `.trash` 目录（⚠️） | SAF / ADB shell | 是用户可恢复的兜底，默认**不清**；清除前提示不可恢复 | CAUTION |
| 压缩包 / 备份 | `Download/`、`Documents/`、应用备份目录 | SAF | 可能含用户备份/工作文件；只按时间+大小建议，不自动删 | CAUTION |
| 下载目录大文件 | `Download/` | SAF / MediaStore.Downloads | 用户主动下载，误删代价高；优先“重要文件提示”而非清理 | CAUTION |
| OTA 包 | `/data/ota_package`（root，⚠️） | root | 退役/已升级设备才可清；误删影响系统升级 | CAUTION（root） |
| 重复照片/视频 | 全盘媒体集合（MediaStore / 文件系统） | SAF+MediaStore / ADB shell | 必须全内容哈希 + 保留每组至少一张 + 保留元数据 + 同步删除 MediaStore 记录，否则出现“幽灵”条目；误删原片风险高 | DANGER（自动）/ CAUTION（逐组确认） |
| 截图 | `Pictures/Screenshots`、`DCIM/Screenshots`（OEM 差异，⚠️） | SAF | 可能是用户刻意保存的信息（付款码/地址）；按“最旧 N 天”逐项 | CAUTION |

**通用红线（任何版本、任何权限都不删）**：`/data`、`/system`、`/vendor`、`/data/media`、`/mnt/media_rw`、`/sdcard/Android/obb`、他包 `Android/data`、`.nomedia` 所在目录、`.transforms`、`.picker_transcoded`、`DCIM/Camera` 原始照片、`Android/data/<pkg>` 下的数据库（.db/.sqlite/-wal）、密钥/证书、微信 MicroMsg、正在下载的 `.crdownload/.part`、`Android/` 根层非 data/obb/media 内容。

## 4. 开源项目调研

### 4.1 对比表（元数据核对日期 2026-11-06）

| 项目 | 仓库 | 许可证 | 形态/技术栈 | 核心能力 | 技术路径 | 可借鉴 | 不可借鉴 / 不兼容 |
|---|---|---|---|---|---|---|---|
| SD Maid 2/SE | github.com/d4rken-org/sdmaid-se | **GPL-3.0**（仅代码；图标/文档/翻译不在内） | Kotlin Android，约 7,996 stars | CorpseFinder、AppCleaner、SystemCleaner、Scheduler、AppControl、StorageAnalyzer、Deduplicator、Media Squeeze | root + ADB（Shizuku/Porter）+ 可访问性服务降级；SAF/MediaStore | 模块化清理域、特权分层与降级、不可访问项提示、调度 | GPL 代码不可并入闭源产品；可访问性模拟点击不建议 |
| SpaceSweep | github.com/Dhiva-Labs/SpaceSweep | **无 LICENSE**（GitHub license API 404；根目录无 LICENSE、README 无声明）→ 默认版权保留，**代码不可复用** | Flutter/Dart + Kotlin，2026-10 创建，2 stars | 重复文件、应用缓存、Junk（.thumbnails/APK/temp/.trashed-*/空目录）、大文件、存储环 | MANAGE_EXTERNAL_STORAGE、PACKAGE_USAGE_STATS、QUERY_ALL_PACKAGES；无 root/Shizuku；不碰 Android/data 与 obb | **最佳安全设计样本**：删除前复核、保留一份、空目录复查、改 MediaStore、不越界 | 无许可证不可抄代码；all-files 在 Play 上架受限；桌面双端无关 |
| Android Device Optimizer（A） | github.com/rakarmp/Android-Device-Optimizer | **MIT** | Magisk 模块（Shell），2 stars，2025-02 后停更 | root daemon 自动清内存/缓存、调度重启 | root only | 定时维护理念 | 常驻 root daemon、自动重启、盲清缓存，风险不可控 |
| Android Device Optimizer（B） | github.com/icaroharry/android-device-optimizer | **无 LICENSE**（仅 README/SKILL.md/references）→ 不可复用 | Agent Skill（ADB） | 去膨胀、禁服务、内存治理，SAFE/CAUTION/DANGEROUS 分级 | ADB shell | 三级安全分级、每批用户确认、恢复点、受保护包清单 | 非存储清理；无许可证 |
| Android Device Optimizer（C） | github.com/ITquanh/android-device-optimizer | **MIT** | Python 脚本 + Agent Skill，1 star | Android 5–16，性能/音质/电池/屏幕/网络/存储六维 | ADB + root 双通道 | 只读体检→快照→确认→渐进执行→量化对比 SOP | BMS 校准/强制高刷/墓碑等高风险 tweak；范围过宽 |
| Shizuku（依赖层） | github.com/RikkaApps/Shizuku | **Apache-2.0** | Kotlin，约 32,875 stars | 以 shell/root 身份代理系统 API；rish | ADB/root 启动 server + binder | 免 root 高权限通道、成熟权限引导流程 | 非 root 能力=shell；需每次开机重启；不能读他应用 /data/data |

检索说明（2026-11-06）：GitHub API 搜索 “SpaceSweep” 共 28 个仓库，唯一符合“Android 存储清理、开源”描述的是 Dhiva-Labs/SpaceSweep（其余为游戏、Linux 桌面工具或同名异类）；搜索 “Android Device Optimizer” 共 8 个仓库，与本任务相关的为上述 3 个（另有越南语 AI 优化项目等，无存储清理内容）。未发现第二个可信的“SpaceSweep Android 清理”开源项目。Play 商店另有同名应用 com.spacesweep.mobile.storage.cleaner.qmv，非该 GitHub 项目，是否闭源/许可未知（⚠️），勿混淆。

### 4.2 SD Maid 2/SE 关键事实

- LICENSE 文件为 GPL-3.0 全文（✅ 仓库 LICENSE）；README 明确代码 GPLv3，但排除图标/营销素材、动画、文档、Play 截图与翻译。
- 官方 FAQ：可访问性服务用于清缓存；推荐 Shizuku（或 Porter，维护中的 Shizuku 分支）以获得比可访问性服务“更彻底的缓存清理与文件访问”（✅ FAQ）。
- 设计启示：清理域拆分（残留/缓存/系统垃圾/分析/去重）+ 特权能力检测与降级 + 明确告知“哪些没清到”。

### 4.3 SpaceSweep 关键事实（最值得抄“安全策略”）

- 能力与权限自述：MANAGE_EXTERNAL_STORAGE（Android 11+）/ 存储权限（8–10）、PACKAGE_USAGE_STATS、QUERY_ALL_PACKAGES；**无网络权限**；README 称已验证于 Android 17 模拟器（✅ README）。
- 删除安全机制：删除瞬间复核文件大小/修改时间/符号链接；重复组保留至少一份且保留项未变才删；空目录删除前复查；隐藏目录默认不扫；`Android/data`、`Android/obb` 永不触碰；删除媒体后同步 MediaStore（✅ README）。
- 许可现实：无 LICENSE → 只能学习行为与算法思路，不能复制代码/资源。

### 4.4 Android Device Optimizer 三个候选的真实差异

- rakarmp：Magisk/KernelSU 模块（module.prop + install.sh + system/bin），**root 常驻**，自动清缓存/内存并调度重启；与“存储深度清理”目标部分重合但不可用于免 root 场景。
- icaroharry：面向 Agent 的 ADB 去膨胀 Skill（SKILL.md 6 阶段流程：Discover→Analyze→Backup→Batch→Confirm→Recover），核心价值是**分级 + 批确认 + 恢复流程**；无存储清理与许可证。
- ITquanh：中文“全场景调优 Skill”，ADB 与 root 双轨，强调“初始快照 / 一键回滚”和 6 步 SOP；存储只是六维之一（多为 FSTRIM/缓存类），并含高风险 tweak，**不建议整体借鉴**。

## 5. 对“AI 手机深度清理 Skill”的 12 条设计建议

1. **四档能力探测，先报告再干活**：普通应用（SAF/MediaStore）→ all-files（MANAGE_EXTERNAL_STORAGE）→ ADB shell/Shizuku → root。启动时探测并输出“当前可清理 X 类 / 受限 Y 类 / 需要 Z 权限”，绝不把“没权限”包装成“已清理”。
2. **按证据分级，不按目录名猜**：SAFE = 可再生成缓存、明确临时/日志、已安装同版本 APK、`.thumbnails`、严格白名单空目录；CAUTION = 微信/QQ/浏览器/短视频的 files/data、Download、备份、截图、`.trashed-*`；DANGER = 数据库、MicroMsg、Android/obb、DCIM 原片、系统目录。默认只勾 SAFE。
3. **删除前“三重复核”**：路径白名单（前缀 + 非符号链接 + 同挂载点）→ 扫描时记录 size/mtime/inode，删除瞬间再核对 → 批量删除前生成 dry-run 清单（路径/大小/数量/预计释放量）。SpaceSweep 的“变化即跳过”是最低标准。
4. **绝不跟随符号链接、绝不跨挂载点、绝不用通配符递归删**：显式路径 + `-maxdepth`/白名单目录；对 `Android/data` 他包目录只允许“按包名精确删除”，且必须与已安装列表对账。
5. **微信/QQ 只按“可再生成”口径处理**：把 MicroMsg、files、emoji、cache 分开建模；emoji/cache/temp 可候选，MicroMsg 与数据库一律 DANGER；推荐引导用户使用应用内清理/系统“清除缓存”，**禁止 `pm clear`**（会清数据）。
6. **卸载残留用 `pm list packages -u`，且必须包名对账**：AOSP 语义为“保留数据卸载”（DELETE_KEEP_DATA），需要 QUERY_ALL_PACKAGES 才能列出（✅）；只删与未安装包名精确匹配的 `Android/data|obb|media/<pkg>`，并排除系统保留/共享 UID 包。
7. **媒体类清理走 MediaStore + 内容哈希，不靠文件名**：重复照片/视频必须全内容哈希（先 size 分组、采样哈希、再全量），保留每组至少一张；删除后同步 MediaStore，避免幽灵条目。普通应用需按 Android 13/14 申请细分媒体权限。
8. **不可恢复操作全部二次确认，且给“反悔期”**：优先移动到本应用可恢复区（自建 .trash 或应用私有目录）而非直接 unlink；记录 undo 日志（原路径/大小/时间/操作）；对确实不可恢复的项（.trashed-*、系统日志）单独红色警告。
9. **权限不足时给可执行路径，而不是空话**：无 all-files → 给“设置 → 所有文件访问”跳转与说明；无 ADB → 给无线调试配对步骤（见 2.4）；无 root → 明确列出“本机不可清理项”，并给出替代（应用内清理、系统存储设置）。
10. **拒绝可访问性模拟点击与“静默清理”话术**：SD Maid 的可访问性降级是合规灰区；AI Skill 应只用官方 API/ADB 命令，并把每条命令与预期效果写进报告，便于审计与复用。
11. **清理量必须可量化复测**：清理前后用 `df`、`dumpsys diskstats`、StorageStatsManager（或 SAF 统计）对比；报告“实际释放/预计释放/失败项/跳过原因”，失败项不得计入成果。
12. **许可证与合规**：GPL 代码（SD Maid）只学设计不复制；无 LICENSE 的仓库（SpaceSweep、icaroharry Skill）代码不可复用；可依赖 Apache-2.0 的 Shizuku。对外发布时标注“灵感来源”，避免把他人设计包装成原创实现。

## 6. 参考来源（访问日期均为 2026-11-06）

**Android 官方文档**
- Android 11 存储更新：https://developer.android.com/about/versions/11/privacy/storage
- Android 10 隐私变更（scoped storage）：https://developer.android.com/about/versions/10/privacy/changes
- 管理所有文件访问（all-files）：https://developer.android.com/training/data-storage/manage-all-files
- 应用专属存储：https://developer.android.com/training/data-storage/app-specific
- 共享媒体访问 / MediaStore：https://developer.android.com/training/data-storage/shared/media
- SAF 文档：https://developer.android.com/guide/topics/providers/document-provider
- Android 13 行为变更（媒体权限）：https://developer.android.com/about/versions/13/behavior-changes-13
- Android 14 行为变更（Selected Photos）：https://developer.android.com/about/versions/14/behavior-changes-14
- Android 15 行为变更：https://developer.android.com/about/versions/15/behavior-changes-15
- Android 16 行为变更（MediaStore 版本隔离）：https://developer.android.com/about/versions/16/behavior-changes-16
- adb 文档（无线调试/配对）：https://developer.android.com/tools/adb

**AOSP 平台文档**
- 存储总览：https://source.android.com/docs/core/storage
- Traditional storage（/sdcard 多用户解析）：https://source.android.com/docs/core/storage/traditional
- Scoped storage（FUSE 网关）：https://source.android.com/docs/core/storage/scoped
- FUSE passthrough：https://source.android.com/docs/core/storage/fuse-passthrough
- 存储配置示例（/data/media 与 sdcard 服务）：https://source.android.com/docs/core/storage/config-example

**AOSP 源码（GitHub 镜像）**
- Shell 应用权限清单：https://github.com/aosp-mirror/platform_frameworks_base/blob/main/packages/Shell/AndroidManifest.xml
- adb 守护进程（UID/组/adb root 逻辑）：https://github.com/aosp-mirror/platform_system_core/blob/android-11.0.0_r1/adb/daemon/main.cpp
- FUSE 访问判定 `is_app_accessible_path`：https://github.com/aosp-mirror/platform_packages_providers_MediaProvider/blob/master/jni/FuseDaemon.cpp
- Process.SHELL_UID=2000：https://github.com/aosp-mirror/platform_frameworks_base/blob/main/core/java/android/os/Process.java
- MATCH_UNINSTALLED_PACKAGES 语义：https://github.com/aosp-mirror/platform_frameworks_base/blob/main/core/java/android/content/pm/PackageManager.java
- FileUtils（.trashed-*、.thumbnails、.transforms、.picker_transcoded）：https://github.com/aosp-mirror/platform_packages_providers_MediaProvider/blob/master/src/com/android/providers/media/util/FileUtils.java
- init 环境变量 EXTERNAL_STORAGE=/sdcard：https://github.com/aosp-mirror/platform_system_core/blob/main/rootdir/init.environ.rc.in

**开源项目与第三方**
- SD Maid 2/SE：https://github.com/d4rken-org/sdmaid-se ；FAQ：https://sdmse.darken.eu/faq
- SpaceSweep：https://github.com/Dhiva-Labs/SpaceSweep
- Android Device Optimizer：https://github.com/rakarmp/Android-Device-Optimizer
- android-device-optimizer（Agent Skill）：https://github.com/icaroharry/android-device-optimizer
- android-device-optimizer（Python Skill）：https://github.com/ITquanh/android-device-optimizer
- Shizuku：https://github.com/RikkaApps/Shizuku ；API：https://github.com/RikkaApps/Shizuku-API ；rish：https://github.com/RikkaApps/Shizuku-API/tree/master/rish
- 腾讯客服：QQ 文件本地存储路径变更公告：https://kf.qq.com/touch/sappfaq/200302myY7ve200302iuUviY.html

## 7. 仍需真机验证清单（⚠️）

1. adb shell 对 `/sdcard/Android/data/<pkg>` 与 `/sdcard/Android/obb/<pkg>` 的读取/写入在 Android 11/12/13/14/15/16 各版本与各 OEM 上的实际表现。
2. 小米 HyperOS / Redmi、OPPO ColorOS、vivo OriginOS 对无线调试、USB 调试（安全设置）、adb 安装的附加开关与限制。
3. 微信当前版本（候选路径 `Android/data/com.tencent.mm/{MicroMsg,files,cache,temp,emoji}`）与 QQ 当前版本的真实目录结构与大小构成。
4. `.trash` 实际目录位置与系统回收站行为；`.thumbnails` 在 DCIM 是否存在。
5. 非 root 下 `/data/anr`、`/data/tombstones`、`/data/system/dropbox` 是否可读；`dumpsys dropbox` 可用性。
6. OTA 包实际路径与可删除性（`/data/ota_package`）。
7. Android 15 / 16 的 OEM 是否新增共享存储限制或 MediaStore 行为差异。
8. Shizuku 在 Android 15/16 上的启动方式与可用系统命令集合。
9. 媒体删除后 MediaStore 同步效果（幽灵条目）与相册刷新时延。
10. 各 ROM 的“空目录”判定差异（是否包含隐藏目录、Android/ 根层、标准目录）。
