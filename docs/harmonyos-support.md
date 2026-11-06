# HarmonyOS 支持与兼容性

本项目在原有 Android 通路上增加 HarmonyOS 适配。原则是**先识别系统，再按真实能力降级**，不假设鸿蒙等于 Android。

## 1. 版本兼容性表

| 系统 | 版本 | 运行环境 | 电脑端连接 | 手机端 Agent | 清理能力 | 验证状态 |
|---|---|---|---|---|---|---|
| Android | 7+（建议 10+） | AOSP | adb | Termux / Shizuku / Root | 完整 Android 规则 | 已有 mock 全链路；真机待冒烟 |
| HarmonyOS 兼容版 | 1.x–4.x | 保留 Android 运行时 | adb | Termux / Shizuku 可用 | 与 Android 相同 | 官方文档确认 adb；mock 已验证识别，真机待验证 |
| HarmonyOS NEXT | 5.x 及后续 | 原生鸿蒙 | hdc | **不支持** | 公共目录 + 媒体库 | 已完成 mock 全链路；**真机未验证** |

官方资料明确：HarmonyOS 3.1/4.0 及以下使用 adb 调试，HarmonyOS 5.0 及以上使用 hdc；NEXT 不再兼容 Android APK。

## 2. 功能能力矩阵

| 功能 | Android | 鸿蒙兼容版 | HarmonyOS NEXT |
|---|---|---|---|
| 公共存储扫描 | 支持 `/sdcard` | 支持 | 支持 `/storage/media/100/local/files` |
| 媒体库扫描 | 支持 | 支持 | 支持 `/mnt/data/<uid>/media_fuse/Photo`（API 22+） |
| 重复文件 / 旧 APK / 旧压缩包 | 支持 | 支持 | 仅公共目录 |
| 临时日志 / 空目录 | 支持 | 支持 | 仅公共目录 |
| 应用外部缓存（Android/data） | 视权限 | 视权限 | 不适用 |
| 应用私有缓存逐文件扫描 | 需 Root | 需 Root | **不支持** |
| 卸载残留检测 | 支持 | 支持 | **不支持**（沙箱不可枚举） |
| 隔离、恢复、永久删除 | 支持 | 支持 | 仅公共目录；待真机验证 |
| 电脑备份 `--backup-to` | 支持 | 支持 | 支持（hdc file recv） |
| 手机端 Agent | 支持 | 支持 | **不支持** |
| `bm clean -c` 清应用缓存 | 不适用 | 不适用 | 仅报告能力，不自动执行 |

## 3. 电脑端 hdc 使用

1. 安装包含 hdc 的 HarmonyOS Command Line Tools 或 SDK toolchains，把目录加入 PATH。不需要完整 DevEco Studio。
2. 手机开启开发者模式与 USB 调试，数据线连接并允许调试。
3. 运行：

```powershell
hdc list targets -v
python android-deep-clean\scripts\adc.py devices --transport hdc
python android-deep-clean\scripts\adc.py doctor --state .adc-state --transport hdc --serial <connect-key>
python android-deep-clean\scripts\adc.py scan   --state .adc-state --transport hdc --serial <connect-key>
python android-deep-clean\scripts\adc.py analyze --state .adc-state --transport hdc --serial <connect-key>
```

后续 `plan → select → execute → verify → restore/purge` 与 Android 相同，只是把 `--transport adb` 换成 `--transport hdc`，`--serial` 使用 hdc 的 connect-key。

## 4. 能力降级与报告

`doctor` 会输出：

- `platform` / `platform_label`
- `public_read`、`public_write`、`photo_read`、`tmp_read`
- `supports_android_packages`、`supports_app_cache_files`、`supports_shizuku`、`supports_termux`、`supports_bm_clean`
- `limitations` 与 `unsupported_features`

无法执行的分类会自动跳过，并在报告中写明原因，不会为了「功能看起来一致」而调用未验证接口。

## 5. 当前验证状态

| 项目 | 状态 |
|---|---|
| 平台识别（三系统） | 单元测试通过 |
| HarmonyOS NEXT 路径守卫 | 单元测试通过 |
| HarmonyOS NEXT 公共目录扫描/分析/隔离/恢复 | mock 全链路通过 |
| Android 回归 | 184 passed / 2 skipped |
| HarmonyOS NEXT 真机 | **未验证** |
| HarmonyOS 兼容版真机 | **未验证** |
| `bm clean` 实际行为 | **未验证，未接入执行器** |

真机未验证前，不应对外宣称「已支持鸿蒙清理」。当前准确表述是：**已实现 HarmonyOS 识别、hdc 通路和公共目录清理的架构与 mock 验证，等待真机冒烟。**
