# Changelog

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
