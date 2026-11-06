# HarmonyOS 适配说明（Agent 参考）

本文件只描述 HarmonyOS 通路。Android 与鸿蒙兼容版仍走原有 adb / rish / local 流程。

## 1. 先识别，再决定能力

不要凭「华为品牌」或「鸿蒙」字样猜测。先运行 `doctor`，读取：

- `platform`：`android`、`harmonyos_compat`、`harmonyos_next`
- `platform_label`、`device.os_version`、`device.api_version`
- `capabilities`：能读什么、能写什么、支持哪些命令
- `limitations` 与 `unsupported_features`

判定规则（代码位于 `scripts/adc_lib/platform.py`）：

| 平台 | 依据 | 传输 |
|---|---|---|
| Android | 有 `ro.build.version.sdk`，无鸿蒙属性 | adb / rish / local |
| HarmonyOS 兼容版 1.x–4.x | 有 Android SDK，同时出现 `hw_sc.build.platform.version` 等鸿蒙属性 | adb / rish / local |
| HarmonyOS NEXT 5+ | 无 Android SDK，`platform=harmonyos_next` | hdc |

hdc 侧优先读取 `param get`：

```text
const.product.os.dist.name
const.product.os.dist.version
const.product.os.dist.apiversion
const.ohos.apiversion
const.product.model
const.product.manufacturer
```

## 2. 能力边界（必须如实告知用户）

### HarmonyOS 兼容版（1.x–4.x）

- 保留 Android 运行时，沿用 `/sdcard`、`Android/data`、`pm`、adb。
- 能力与 Android 相同，但仍受 scoped storage / ROM 限制。
- 报告 platform 为 `harmonyos_compat`，规则不另起一套。

### HarmonyOS NEXT（5+）

| 能力 | 状态 |
|---|---|
| hdc 设备发现、shell、file recv | 已实现；待真机验证 |
| 公共目录 `/storage/media/100/local/files` 扫描 | 已实现；待真机验证 |
| 媒体库 `/mnt/data/<uid>/media_fuse/Photo`（API 22+） | 已实现路径；待真机验证 |
| 公共目录文件隔离、恢复、永久删除 | 已实现；待真机验证 |
| 重复文件、旧 APK/压缩包、临时日志、空目录分析 | 已实现；仅限公共目录 |
| 其他应用私有缓存逐文件扫描 | **不支持**：沙箱不可读 |
| 用 `hdc shell -b <bundle>` 删除 debug 应用沙箱文件 | **禁止**，不实现 |
| `bm clean -c -n <bundle>` 清应用缓存 | **仅报告能力**，不自动执行；需开发者模式与真机验证 |
| `bm clean -d` 清应用数据 | **禁止** |
| Android APK 安装/运行 | **不支持** |
| adb / Shizuku / Termux | **不支持** |
| 手机端 Agent | **不支持**（无法运行 Android APK，暂无原生 HAP Agent） |

`bm clean -c -n` 来自华为官方 bm 工具文档，文档说明该命令在 user 版本开启开发者模式时可用；本版本不把它接入执行器，避免把「官方存在」误当成「已在目标设备验证可用」。

## 3. 电脑端 hdc 流程

1. 获取 hdc：HarmonyOS Command Line Tools 或 SDK 的 `toolchains` 目录，放到 PATH；也可以 `--hdc-path` 指定。
2. 手机：设置 → 关于手机 → 连点版本号开启开发者模式 → 开发者选项 → USB 调试；数据线连接并允许调试。
3. 确认连接：

```powershell
hdc list targets -v

python android-deep-clean\scripts\adc.py devices --transport hdc --hdc-path hdc
```

4. 体检：

```powershell
python android-deep-clean\scripts\adc.py doctor --state .adc-state --transport hdc --serial <connect-key>
```

5. 扫描 / 分析：

```powershell
python android-deep-clean\scripts\adc.py scan    --state .adc-state --transport hdc --serial <connect-key>
python android-deep-clean\scripts\adc.py analyze --state .adc-state --transport hdc --serial <connect-key>
```

默认扫描 `/storage/media/100/local/files` 与媒体库；也可显式指定：

```powershell
python android-deep-clean\scripts\adc.py scan --state .adc-state --transport hdc --serial <connect-key> --roots /storage/media/100/local/files/Docs/Download
```

6. 复核、计划、选择、执行：

```powershell
python android-deep-clean\scripts\adc.py plan    --state .adc-state --transport hdc --review .adc-state\analysis\review.json
python android-deep-clean\scripts\adc.py select  --state .adc-state --transport hdc --categories archives,temp_logs
python android-deep-clean\scripts\adc.py execute --state .adc-state --transport hdc --serial <connect-key> --token <TOKEN> --backup-to D:\phone-backup
python android-deep-clean\scripts\adc.py verify  --state .adc-state --transport hdc --serial <connect-key>
```

7. 恢复 / 永久删除：

```powershell
python android-deep-clean\scripts\adc.py restore --state .adc-state --transport hdc --all
python android-deep-clean\scripts\adc.py quarantine purge --state .adc-state --transport hdc --token <PURGE-TOKEN>
```

隔离区位于 `/storage/media/100/local/files/Docs/.android-deep-clean/quarantine/<execution-id>/`。如果没有公共目录写权限，execute 会直接拒绝。

## 4. 手机端 Agent 的现实情况

- HarmonyOS NEXT 不能安装 Android APK，因此 Termux、Shizuku 和现有 Android 端 Agent 都不可用。
- 原生 HAP 应用只能访问自身沙箱与用户通过 Picker/媒体库授权的文件，无法扫描其他应用缓存，也无法枚举全盘。
- 因此本 Skill 在 HarmonyOS NEXT 上只提供电脑端 hdc 通路；不虚构手机端入口。

## 5. 未验证项（必须保留标注）

以下能力只经过 mock 测试与官方文档核对，尚未在真实 HarmonyOS NEXT 设备上验证：

- hdc 在目标 ROM 上的授权、断连、超时行为。
- 公共目录的真实可写性与 `find/stat/xargs/sha256sum/df/rm/mv/rmdir` 差异。
- 媒体库路径的 uid（默认 100）与 API 版本差异。
- `bm clean -c -n` 在零售机开发者模式下的实际结果。
- 中文/特殊字符路径、超大目录扫描性能。

真机验证前，报告与对话中必须使用「待验证」，不得写「已支持」。

## 6. 官方资料

- hdc 调试命令：https://developer.huawei.com/consumer/cn/doc/harmonyos-guides-V13/hdc-V13
- bm 工具（clean）：https://developer.huawei.com/consumer/cn/doc/bm-tool-V5
- 访问设备文件（公共目录/沙箱）：https://developer.huawei.com/consumer/cn/doc/harmonyOS-guides/ide-device-file-explorer
- 应用沙箱目录：https://developer.huawei.com/consumer/cn/doc/development/harmonyos-guides-V2/app-sandbox-directory-0000001491863498-V2
- 二进制优化工具配置（明确 HarmonyOS 3.1/4.0 及以下用 adb，5.0 及以上用 hdc）：https://developer.huawei.com/consumer/cn/doc/AppGallery-connect-Guides/binary-optimization-tool-config-0000001919148894
- 影响应用兼容性的关键信息（API 版本查询）：https://developer.huawei.com/consumer/cn/doc/harmonyos-releases/app-compatibility-influence-factor
- toybox 工具：https://device.harmonyos.com/cn/docs/apiref/harmonyos-guides/toybox
