# Changelog

## v1.6.2 — 限定目录扫描不再稀释媒体匹配率

### Fixed
- 真机发现：只扫 `/sdcard/Download` 时，MediaStore 索引仍覆盖全盘，`match_ratio` 被算成 2.68%，看起来像索引大面积失效。现改为只按「扫描范围内的索引条目」计算，同场景为 100%。
- 新增 `crosscheck.index_in_scope` 与 `crosscheck.index_scope_ratio`，分别表示范围内的索引条目数与它占全部可解析索引的比例；报告同步展示，避免误读。
- 新增回归测试：限定根目录扫描时，范围内匹配率为 1.0，范围外条目计入 `index_only_outside_scope`。

### Verified
- 真机（HONOR HLK-AL00 / Android 10）限定根目录：
  - `scan --roots /sdcard/Download`：648 条目 / 588 文件 / 384.60 MB，覆盖率 good；
  - `analyze`：范围内索引 133 条，全部匹配（100%）；范围外 4,834 条；`index_scope_ratio=2.68%`；
  - 媒体索引仍覆盖全盘（4,967 条），符合设计，不是错误。
- 真机 `inspect --pull 2`：只读拉取 2 个缩略图预览到电脑并核对大小，随后已删除本地预览文件。
- 全量 235 passed, 1 skipped。

## v1.6.1 — 修复 recheck 误判环境变化

### Fixed
- 真机发现并修复：`recheck` 内部重建 `doctor` 字典时缺少 `platform` 字段，导致 `snapshot-meta.json` 的 `platform` 退化为 `android`；HarmonyOS 兼容版因此每轮都被目录档案判定为「环境变化」并重置基线。
- `Scanner` 写 `snapshot-meta.json` 时改为回退读取 `capabilities.platform`；`recheck` 构造 `doctor` 时也显式带上 `platform`。
- 新增回归测试：HarmonyOS 兼容版 `scan → analyze → recheck` 后 `environment_changed=false`、`baseline=false`、`environment.platform=harmonyos_compat`。

### Verified
- 真机（HONOR HLK-AL00 / Android 10）：修复后连续两轮 `recheck`，第二轮 `environment_changed=false`、`baseline=false`、平台正确为 `harmonyos_compat`。
- 同轮真机只读回归：`recheck`、`inspect`、`plan`、`select`、`archive diff/stats`、`export`、`scan --no-media --no-app-storage` 全部正常。
- 全量 234 passed, 1 skipped。

### Safety
- 本轮只读：未执行 `execute` / `purge` / `restore`，手机隔离区不存在，确认没有任何文件被移动或删除。
- 生成的 `select` 令牌未被使用；未产生 `execution.json`。

## v1.6.0 — 空间去向速览与候选解释

### Added
- `analyze` 报告开头新增「空间去向速览」：设备已用、文件扫描、基础 APK、应用外部目录、媒体索引分列，逐项标注统计口径与重叠关系，并给出「已知且不重叠合计」与「未解释差额」。
- `analysis-summary.json` / `analysis.json` 新增 `summary.space_story`，包含 headline、逐项口径、重叠关系、caveats。
- 每个候选新增 `explain` 三问：这是什么、为什么列出、删除影响；摘要、完整 JSON 与 Markdown 报告同步输出。
- `analyze` 人类输出新增一行空间概况。

### Changed
- 存储口径更明确：原「未由文件扫描解释」改名为「未由文件扫描解释（仅共享存储）」，避免与「未解释差额」（已扣除基础 APK）混淆。

### Safety
- 空间速览只读取已有扫描与辅助数据，不新增数据源、不生成候选、不改变风险/可选择性/文件清单。
- 明确标注应用外部目录与媒体索引包含在文件扫描内，禁止重复相加。

### Verified
- 真机（HONOR HLK-AL00 / Android 10）：设备已用 78.81 GB，文件扫描 11.08 GB（14.1%），加上基础 APK 后未解释 58.75 GB；报告区块与候选三问正常输出，未修改手机文件。
- 新增 5 个空间速览/解释测试；全量 233 passed, 1 skipped。

## v1.5.0 — 应用级存储（部分口径）

### Added
- `scan` 新增只读应用级存储采集：通过 `pm list packages -f` 获取基础 APK 路径，再用批量 `stat` 读取大小；结果写入 `scan/aux/app-storage.json` 与 `snapshot-meta.json` 的 `aux.app_storage`。
- `analyze` 新增 `summary.app_storage` 与 `analysis.db` 的 `app_storage` 表：按包汇总基础 APK、外部 data/media/obb、外部 cache、已知合计、未安装应用遗留外部目录；新增「应用级存储（部分口径）」报告区块。
- 应用类候选（app_cache / browser_cache / chat_cache / residue）证据新增包级 APK 与外部目录统计；仍然只作为证据。
- `scan` / `recheck` 新增 `--no-app-storage`；配置新增 `app_storage.enabled/timeout_seconds/max_packages/top_apps`。

### Safety
- 应用级采集只读：`pm list packages -f` + `stat`，不读取应用内部 data/cache，不使用 Root，也不使用会写系统缓存的 `dumpsys diskstats`。
- 不生成候选、不改变风险/可选择性/文件清单；应用统计不参与 `plan/select/execute`。
- 内部 data/cache 与 split APK 明确标记为未知；报告说明“已知合计不是应用总占用，不能与设备已用空间相加”。

### Verified
- HONOR HLK-AL00（HarmonyOS 3.0 兼容版 / Android 10 / SDK 29）只读验证：293 个已安装应用，基础 APK 8.98 GB；49 个应用有外部 data，合计 1.92 GB；未安装应用遗留外部目录 1 个（109 B）；外部 media/obb 为 0。
- 内部 data/cache 在该 shell 下不可读；未执行任何清理命令。
- 新增 6 个应用级存储测试；全量 228 passed, 1 skipped。

### Limitations
- StorageStatsManager 的应用内部 data/cache 统计在当前 ADB shell 权限下不可用；需要辅助 APK、特殊授权或 Root，本版本不做。
- 只统计基础 APK；split APK 未计入，APK 大小也不能代表应用总占用。

## v1.4.0 — MediaStore 只读辅助索引与交叉验证

### Added
- `scan` 新增 MediaStore 辅助采集：通过 `content query` 只读查询 `images`、`video`、`audio`、`downloads`，支持完整/安全/最小三级字段投影自动降级。
- 新增主机侧 `scan/aux/sources.json` 与 `scan/aux/media-index.jsonl`；记录集合状态、条目、声明大小、路径可解析数、trashed/pending 与限制。
- `analyze` 新增 `summary.media_index` 与 `analysis.db` 的 `media_items` / `source_status` 表，逐路径输出 `matched`、`size_mismatch`、`index_only`（区分扫描范围内/外）、`path_unknown` 与 `scan_only`。
- `report-before.md` 新增「媒体索引核对（第二数据源）」区块与差异样本；候选证据为媒体类补充 MIME、尺寸、时长、trashed/pending、大小不一致与未索引提示。
- `scan` / `recheck` 新增 `--no-media`；配置新增 `media_store.enabled/collections/timeout_seconds/max_items/max_output_bytes`。

### Safety
- MediaStore 只读，不生成候选、不改变风险等级/可选择性/文件清单，不进入 `snapshot.db` 的 `entries` 或 `history/archive.db`，也不参与 `plan/select/execute`。
- 索引不可用、部分集合失败、路径未知、HarmonyOS NEXT、Termux 无权限时如实降级并写入限制；媒体索引大小不计入文件扫描合计。
- 新增 11 个 MediaStore 测试（解析、路径推导、投影降级、去重、大小写路径、交叉核对、候选不变、`--no-media`、不可用降级、HarmonyOS NEXT）；全量 222 passed, 1 skipped。

### Fixed
- 真机发现并修复：MediaStore 日期列名为 `datetaken`（原写成 `date_taken`），部分 ROM 不支持 `media_type`，现已移除；修正后图片/视频/音频使用完整投影成功。
- 真机发现共享存储路径大小写不一致（MediaStore `tencent` / 文件系统 `Tencent`），交叉核对改为大小写不敏感，并单独统计 `case_mismatch`，避免误报“索引有、扫描无”。

### Verified
- HONOR HLK-AL00（HarmonyOS 3.0 兼容版 / Android 10 / SDK 29）只读真机验证：doctor、scan、analyze 成功；4,965 条媒体索引 / 7.35 GB，4,964 条匹配，8 条仅大小写不同，1 条在扫描范围外，范围内缺失 0 条，匹配率 99.98%。该 ROM 的 `downloads` 集合不可用，按部分可用降级。
- 未执行 select/execute/purge/restore，未修改任何手机文件。

### Limitations
- `content query` 的可用性受 shell 权限、OEM、Android 版本影响；已通过 1 台真机验证，更多机型仍需验证。
- 不覆盖其他用户、工作资料、隐藏目录、应用私有媒体或所有 SD 卡卷；索引延迟可能造成“索引有、扫描无”的差异，不能据此认定文件已删除。

## v1.3.0 — 跨轮目录档案与扫描覆盖率

### Added
- `scan` 新增 `dir_stats` 表：记录每个目录的直接大小、完整子树大小、文件数和最新 mtime，供报告与跨轮档案复用；旧快照没有该表时自动降级。
- `scan` 新增覆盖率报告：根目录请求/实际扫描状态、跳过路径、截断、深度限制、逻辑大小与按 `(dev, inode)` 去重后的大小，并写入 `snapshot-meta.json` 与 `scan-summary.md`。
- 新增 `history/archive.db` 目录档案：首轮建立基线，后续记录新增、增长、缩小、本轮未再出现、删后复活、疑似卸载残留、应用版本变化和未审查目录。
- 新增 `archive diff|stats|mark` 命令；`mark` 只支持 keep/watch/neutral，备注仅作为分析证据，不是删除授权。
- `analyze` 报告新增「存储空间与扫描覆盖」「跨轮目录变化」区块；候选证据增加跨轮档案与变化线索；`recheck` 增加目录变化区块并复用主状态目录的档案。
- 配置新增 `archive.min_dir_bytes`、`grow_bytes`、`grow_percent`、`max_entries`、`max_diff_items`；`scan`/`recheck` 支持 `--no-archive`。

### Safety
- 目录档案只写主机侧状态目录，不访问设备，也不进入候选清单、计划或选择令牌。
- 扫描不完整、权限缺口、环境变化时不做消失判断；档案标记不改变任何风险等级。
- 新增 16 个目录档案/覆盖率测试；全量测试 211 passed, 1 skipped。

## v1.2.8 — 异常时间戳不再参与候选筛选

### Fixed
- 异常 mtime（早于 2000-01-01 或晚于当前时间 24 小时）不再只是提示：`_age_days()` 对其返回空，所有按年龄筛选的生成器不再把它当成「很旧」；APK、压缩包、截图、Download、大文件、旧媒体、`.trashed-*`、空目录的 SQL 也增加了时间下界/上界。
- 与 1980 年占位时间戳同组的正常旧文件仍会正常进入候选，异常文件只是被排除；证据中不再出现由异常时间算出的年龄。
- 回归测试改为验证「1980 时间戳不能单独生成旧文件候选」，并覆盖异常文件与正常文件同目录的混合场景。

## v1.2.7 — 修复 HarmonyOS 兼容版执行前的设备身份误判

### Fixed
- 修复 HarmonyOS 兼容版真机上 `execute` 必然报「当前连接的设备与计划设备不一致」的问题：`doctor` 会先做平台识别再计算设备身份，而执行前的二次校验直接用 `device_info()`（platform 为空）计算，同一台手机被算成两个 id。新增 `transport_device_identity()`，执行前置校验与执行中校验统一使用平台识别后的身份；新增回归测试。
- 真机隔离/恢复演练通过：HONOR HLK-AL00（HarmonyOS 3.0 兼容版 / Android 10），自建 3 个测试文件（78 B），quarantine 执行 3/3 成功、电脑备份 3 个、verify 3/3 通过、restore 3/3 成功，恢复后 SHA-256 与 mtime 完全一致，隔离区残留 0；测试文件与隔离目录已删除。

## v1.2.6 — 修复 ADB 全盘扫描只返回根条目

### Fixed
- 修复 USB+ADB 下扫描 `/sdcard` 只得到 1 个条目：Android 的 `/sdcard` 是指向 `/storage/self/primary` 的符号链接，`find` 默认不跟随命令行起始链接。改为 `find -H`（只跟随起始链接，不跟随遍历中遇到的链接），`listdir` 同步修正。新增回归测试。
- 真机 ADB 只读验证通过：HONOR HLK-AL00（HarmonyOS 3.0 兼容版 / Android 10），全盘 125,855 条目 / 117,007 文件 / 23.37 GiB 扫描 47.8 秒，59 个候选组；未执行任何清理动作。

## v1.2.5 — 真实设备只读验证与时间戳异常提示

### Added
- 分析证据新增 `mtime_anomaly`：早于 2000 年或晚于当前时间的文件会被标注「时间戳异常，年龄不可作为判断依据」，避免 ZIP 内 1980 年时间戳被当成 45.9 年的老文件。
- 真机只读验证记录：HONOR HLK-AL00（HarmonyOS 3.0 兼容版 / Android 10 底座），local 通路 doctor、scan、analyze、inspect 全部通过；652 条目 / 593 文件；受保护 `.db`/`.key` 未进入候选。

### Fixed
- 1980 年时间戳的候选现在会在证据中明确提示异常，而不是只显示夸张年龄。

## v1.2.4 — 修复真实手机扫描阻断与 proot 误判

### Fixed
- 修复 `find` 排除组缺少空格导致生成 `\(-path`、真实设备 scan 全量失败的问题；新增回归测试。
- 修复 local/proot 环境中 uid=0 被误判为 Android Root、tier 虚报为 full 的问题；`uname -o` 非 Android 时强制 shell。
- 修复 `getprop` 不在容器 PATH 时设备型号/系统版本为空的问题，回退到 `/system/bin/getprop`。
- 修复 doctor 的 `capabilities.transport` 始终显示 adb、与实际 local 不一致的问题。
- 真实设备环境补充限制说明：容器可读 Android/data 不代表 scoped storage 下普通应用可读。

## v1.2.3 — 风险收紧、空间账目与鸿蒙媒体库探测

### Changed
- 通用应用缓存与浏览器缓存默认从 SAFE 提升为 **CAUTION**，需要 AI 逐项复核；缩略图与明确临时日志仍为 SAFE。可通过 `safety.generic_cache_risk` 调整。
- 缓存证据新增应用是否已安装的状态，避免只凭目录名判断。
- 执行报告新增「空间账目」：发现、本次选择、已处理、未处理、已实际释放、隔离区占用，并明确隔离区不是电脑备份。
- HarmonyOS 媒体库不再固定 uid 100：doctor 探测 `/mnt/data` 下的数字 uid，扫描与隔离目标按探测结果生成，100 仅作回退。
- 安装命令改为 `scripts/install_skill.ps1`：先备份旧目录、校验关键文件、失败自动回滚，不再直接删除旧 Skill。
- 测试报告重构为「当前版本摘要 + 历史版本」，明确区分 Mock、CI 与真机；新增真机验收清单与发布门槛。

### Fixed
- 修复 README/SKILL 安装命令可能删除用户在 Skill 目录中保存的配置或文件的问题。

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
