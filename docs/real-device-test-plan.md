# 真机验收清单与发布门槛

本文件用于把「Mock 通过」升级为「真机可信」。当前状态：**尚未在真实设备完成端到端验收**，因此当前版本不能作为正式清理工具对外承诺。

## 1. 设备矩阵（最小集）

| 设备 | 主要验证 | 最低要求 |
|---|---|---|
| 普通 Android 手机 | 基础扫描、分析、隔离、恢复 | Android 10 以下或非 scoped storage 设备 |
| Android 11+ 手机 | Scoped Storage、Android/data 可见性 | 一台主流品牌 |
| 华为 HarmonyOS 4 手机 | ADB 兼容通路、Android 目录规则 | 保留 Android 运行时的鸿蒙版本 |
| 华为 HarmonyOS NEXT 手机 | HDC、公共目录、媒体库、隔离与恢复 | HarmonyOS 5/NEXT 真机 |

不需要一开始覆盖十几个品牌；四台完成后再扩品牌。

## 2. 每台设备必须走完的链路

连接 → doctor → scan → analyze → AI 复核 → plan → select → backup → execute（隔离）→ verify → restore → 再次 execute → purge。

每一步都要检查真实文件，而不是只看 CLI 返回码：

| 阶段 | 文件级检查 |
|---|---|
| 连接 | `adb devices -l` / `hdc list targets -v` 状态正确；多设备时 `--serial` 生效 |
| doctor | 权限级别与 `capabilities` 和手机实际目录一致；不可读目录出现在 limitations |
| scan | 在测试目录放一个可识别文件，确认快照里有它；不可读目录被跳过并记录 |
| analyze | 候选的路径、数量、大小与真实文件一致；受保护文件不进入候选 |
| 复核 | CAUTION 逐项 review；不把不确定项标成 trash |
| plan/select | 令牌生成；选择项与用户确认一致；DANGEROUS 不在清单里 |
| backup | 电脑备份目录存在，大小/哈希与源文件一致；断开备份目录时执行被拒绝 |
| execute | 源文件消失；隔离区出现同名文件；失败项源文件仍在 |
| verify | 源文件残留 0；隔离文件缺失 0；隔离模式 released_bytes=0 |
| restore | 源路径恢复、内容哈希一致；再次 restore 幂等 |
| purge | 使用 execute 输出令牌；隔离文件被删除；无关文件不受影响 |

测试文件建议：`/sdcard/Download/adc-test-<日期>.bin`（Android）或公共目录 `Docs/Download/...`（NEXT）。不要用唯一照片、聊天记录、数据库或工作文件做测试。

## 3. HarmonyOS 专项检查

| 项目 | 检查方式 | 当前状态 |
|---|---|---|
| hdc 授权与重连 | 拔插数据线、重开 USB 调试、执行 `hdc list targets -v` | 未验证 |
| 公共目录读写 | 创建测试文件、移动、恢复、删除 | 未验证 |
| 媒体库 uid 探测 | doctor 的 `harmony_photo_roots` 与手机实际 uid 一致 | 未验证（代码已改为动态探测） |
| toybox 命令 | doctor 的 find/stat/xargs/sha256sum/df/rm/mv/rmdir 全为 true | 未验证 |
| 断连中止 | 执行中拔线，状态应为 aborted，未执行项目保持原状 | 未验证 |
| 手机端 Agent | HarmonyOS NEXT 无法运行 Android APK，确认不做手机端承诺 | 文档已说明不支持 |

## 4. 发布门槛

满足以下条件之前，README、发布页和对话中不得写「鸿蒙已支持」「可放心清理」：

1. 四台设备全部完成第 2 节链路，且文件级检查通过。
2. Android 与 HarmonyOS 兼容版的 ADB 通路至少各一台真机通过。
3. HarmonyOS NEXT 的 hdc 通路至少一台真机通过，公共目录与媒体库分别验证。
4. 每台设备的测试记录包含：设备型号、系统版本、命令、报告路径、失败项与截图或文件清单。
5. 结果写入 `docs/test-report.md` 的「真机验证」章节，并与 CI/Mock 分开记录。

## 5. 当前结论

- 代码、Mock 与 CI：通过。
- 真实设备只读链路：HONOR HLK-AL00（HarmonyOS 3.0 兼容版 / Android 10）已通过 doctor、scan、analyze、inspect；local 与 USB+ADB 两条通路均验证，ADB 全盘扫描 125,855 条目 / 117,007 文件。
- 真实设备清理/恢复：隔离与恢复机制已在 1 台设备用自建测试文件演练通过（3 个文件，SHA-256 与 mtime 一致，隔离区残留 0）；**未对用户真实数据执行 execute/purge/restore，正式验收 0 台**。
- 真实 HarmonyOS NEXT：**0 台完成验收**。
- 当前准确定位：**只读分析可用于试用；清理与恢复仍未达到正式工具门槛**。
