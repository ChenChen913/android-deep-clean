# Changelog

## v1.2.2 — 修复 Linux CI 符号链接扫描与 Python 3.9 runner

### Fixed
- mock 传输的 `_to_device()` 不再用 `Path.resolve()` 跟随符号链接，避免链接项被登记成目标文件并覆盖真实条目；Linux 上扫描包含符号链接的目录时不再丢失候选。
- GitHub Actions 的 Ubuntu 作业改用 `ubuntu-22.04`，避免 `ubuntu-latest` 升级到 Ubuntu 26.04 后没有 Python 3.9 可用。
- 修正符号链接测试夹具：链接在 analyze/plan 之后创建，避免真实目标文件本身成为候选、被正常隔离后导致「目标应保留」断言失败。

### Changed
- 版本号升级到 1.2.2。

## v1.2.1 — 修复 Windows CI 大整数写库失败

### Fixed
- Windows runner 的 `st_ino` / `st_dev` 为无符号 64 位，超过 SQLite 有符号 64 位上限，扫描写库时报 `Python int too large to convert to SQLite INTEGER`。
- 新增 `sqlite_int64()`，对 mock 自体文件与设备 `stat` 输出统一做二补码归一化；inode/dev 仍保持区分度，不会因截断而全部相同。
- 新增回归测试：`2**63`、`2**64-1` 与正常整数，以及超范围 stat 行解析。

### Changed
- 版本号升级到 1.2.1。

## v1.2.0 — HarmonyOS 适配（兼容版 + NEXT）

### Added
- 平台画像与系统识别：`android` / `harmonyos_compat` / `harmonyos_next`，依据 Android SDK、`hw_sc.build.platform.version`、`const.product.os.dist.*` 等属性判定，不按品牌猜测。
- hdc 传输层：`devices --transport hdc`、`doctor/scan/analyze/... --transport hdc`，支持 `hdc list targets`、`hdc shell`、`hdc file recv`、多设备 `-t connect-key`。
- HarmonyOS NEXT 公共目录适配：默认扫描 `/storage/media/100/local/files` 与媒体库 `/mnt/data/100/media_fuse/Photo`；隔离区放在公共目录 Docs 下。
- HarmonyOS NEXT 路径守卫：仅公共目录/媒体库可处理，应用沙箱、`/data`、系统目录全部硬阻断。
- 规则降级：NEXT 只运行重复文件、旧 APK/压缩包、临时日志、大文件、空目录、受保护项分析；不运行应用缓存、卸载残留、聊天缓存、截图/媒体等 Android 路径规则。
- 报告增加 `platform`、`platform_label`、`supported/unsupported` 能力与 limitations。
- 新增 references/harmonyos.md、docs/harmonyos-support.md 与 HarmonyOS mock 全链路测试。

### Changed
- `plan_hash` / `selection_hash` 覆盖 `platform`；旧计划需要重新 analyze 和 plan。
- 执行报告与验证报告记录平台；HarmonyOS NEXT 的隔离目标按公共目录映射。
- 版本号升级到 1.2.0。

### Known limits（明确未支持/未验证）
- HarmonyOS NEXT 不支持 Android APK、adb、Shizuku、Termux；手机端 Agent 不提供。
- NEXT 应用私有缓存逐文件扫描不可行；`bm clean -c -n` 仅报告能力，未接入执行器。
- HarmonyOS NEXT 与兼容版的 hdc/adb 行为均未在真实设备验证；mock 通过不等于真机通过。

## v1.1.2 — 证据数字与真实清单对齐

### Fixed
- 修复候选证据中的文件数和逻辑大小按安全过滤前的清单统计，导致报告显示 4 个文件/900KB、实际只清理 2 个文件/800KB 的问题。
- 证据数字现在统一按最终写入 candidate_files 的清单生成，AI 复核不会再被虚高数字误导。

### Added
- 回归测试：缓存目录内的 `.db`、`.nomedia` 等受保护文件不计入证据和候选清单。
- 本地 mock 全链路演练：doctor → scan → analyze → 复核 → plan → select → execute（含电脑备份）→ verify → restore → purge。

## v1.1.1 — 安全修正与发布工程化

### Fixed
- 修复 Root 内部缓存路径（`/data/data/<pkg>/cache`）绕过敏感文件扩展名检查与 `extra_protected_paths` 检查的问题。
- 修复同尺寸重复文件超过 200 个、或同尺寸分组数达到上限时未在 limitations 中披露的问题。

### Changed
- `plan_hash` / `selection_hash` 现在覆盖每个候选的文件清单摘要（`files_digest`），execute 前会校验 analysis.db 清单与计划一致；旧计划需重新 analyze 和 plan。
- 移除 executor 中等价的重复分支。
- 版本号升级到 1.1.1。

### Added
- GitHub Actions CI：Ubuntu / Windows × Python 3.9 / 3.13 全量测试。
- `tools/build_dist.ps1`：从仓库根目录可复现生成 `.skill` 与项目 zip，并用根目录文档覆盖包内副本。

## v1.1.0 — 双通路扩展（USB + ADB 电脑端）

### Added
- 电脑端 USB + ADB 通路正式化：Windows 11 下由 AI Agent 通过 Platform Tools 调用同一 Skill。
- devices 命令：列出 ADB 设备、授权状态、序列号；空设备/未授权/离线/多设备均有明确处理提示。
- ADB 多设备保护：检测到多台已授权设备时必须 --serial；禁止自动挑设备。
- 设备身份绑定：doctor/scan/analysis/plan/selection/execution 携带 device_identity（serial+fingerprint+model+sdk 短哈希）；execute 前校验当前设备必须是计划设备。
- 设备断开保护：执行前/每个项目前 health check；中途断开立即 aborted，后续项目不执行。
- execute --backup-to DIR：清理前把选中文件复制到电脑并校验大小/SHA-256；备份失败则跳过清理；生成 backup-manifest.json。
- analyze --hash-mode pc：只把大小相同的重复候选通过 USB 拉到电脑本地计算 SHA-256，适用于设备缺少 hash 工具或希望用电脑 CPU。
- export 命令：analysis-items.csv / analysis-files.csv / execution-items.csv。
- history 命令：查看历史扫描快照；scan 自动归档上一轮快照到 history/scan-*（最多 10 份）。
- references/usb-adb-workflow.md：Windows 11 USB+ADB 完整工作流。
- references/dual-path-architecture.md：手机端/电脑端双通路共享规则与能力矩阵。
- README / SKILL.md / 安装使用 / 权限兼容文档更新。

### Changed
- plan_hash / selection_hash 现在覆盖设备身份，旧版本计划必须重新 scan/plan。
- 执行报告包含设备身份、传输方式、aborted 状态、电脑备份信息。
- 扫描历史与状态目录布局扩展；旧版本状态目录仍可读取，但建议重新 scan。
- 版本号升级到 1.1.0。

### Fixed
- 修复组级执行失败（目标目录被删等）静默返回成功的问题。
- 修复恶意 manifest 路径导致报告渲染崩溃的问题。
- 修复 selection 元数据篡改未拒绝、篡改 plan 可注入 DANGEROUS 的问题。
- 修复 restore 非幂等、/boot.img 未 hard block、空目录候选失效、聊天缓存 app 包为空等问题（v1.0.0 验证期间发现）。

## v1.0.0 — 初始版本

- 手机端本地/Shizuku Skill，只读扫描、AI 复核、风险分级、隔离执行、验证/复查。
- pytest mock 测试套件、独立测试报告、Android 存储/权限研究。
