# facts.md

> README 的唯一事实来源。A 部分由 AI 扫描生成后人工核对；B 部分必须手写。

## A. 可自动提取的事实

1. **项目类型**：Python CLI + AI Agent Skill（CLI 工具 / AI 项目）
2. **主语言 / 运行时 / 最低版本**：Python 3.9+；产品代码只用标准库；测试使用 pytest
   - 来源：`android-deep-clean/SKILL.md`（“需要 Python 3.9+，只用标准库”）
3. **安装命令**：
   - `powershell -ExecutionPolicy Bypass -File .\android-deep-clean\scripts\install_skill.ps1`
   - 来源：`android-deep-clean/scripts/install_skill.ps1`
4. **运行命令**：`python android-deep-clean/scripts/adc.py --help`
   - 来源：`android-deep-clean/scripts/adc.py`
5. **测试命令**：`python -m pytest tests -q`
   - 产生数字的命令：`python -m pytest tests -q` → 257 passed, 1 skipped（2026-11-09，v1.9.4）
   - 收集数量命令：`python -m pytest tests -q --collect-only` → 258 tests collected
6. **构建命令**：无（纯 Python，无打包/编译步骤）
7. **目录结构**（三层内）：
   - `android-deep-clean/` — Skill 本体（`SKILL.md` + `scripts/` + `references/`）
   - `android-deep-clean/scripts/adc.py` — CLI 入口
   - `android-deep-clean/scripts/adc_lib/` — 传输层、扫描、分析、计划、执行、报告、平台识别
   - `android-deep-clean/references/` — Agent 参考文档（安全策略、路径规则、复核指南、USB/ADB、鸿蒙等）
   - `tests/` — pytest 用例与 mock 测试夹具（不需要手机）
   - `docs/` — 人类可读文档（安装、CLI 契约、鸿蒙支持、真机验收、测试报告等）
   - `tools/` — 辅助脚本
   - `.github/workflows/ci.yml` — GitHub Actions（ubuntu-22.04 / windows-latest × Python 3.9 / 3.13）
8. **主要依赖**（最多 10 个，含用途）：
   - 无第三方运行时依赖；仅 Python 标准库（argparse、sqlite3、hashlib、subprocess 等）
   - 开发/测试依赖：pytest
9. **环境变量 / 配置项**：
   - `ADC_STATE` · 否 · `.adc-state` · 状态目录默认值；来源 `scripts/adc_lib/state.py`
   - `--config <JSON>` · 否 · 内置默认值 · 覆盖分类阈值、扫描上限、安全策略；来源 `scripts/adc_lib/config.py`
   - 主要阈值（默认值，来源 `scripts/adc_lib/config.py`）：
     - `thresholds.large_file_bytes` = 104857600（100 MiB）
     - `thresholds.old_file_days` = 365
     - `thresholds.apk_old_days` = 180
     - `thresholds.archive_old_days` = 180
     - `thresholds.temp_old_days` = 30
     - `thresholds.log_old_days` = 90
     - `thresholds.screenshot_old_days` = 180
     - `thresholds.media_old_days` = 730
     - `thresholds.download_old_days` = 30
     - `safety.generic_cache_risk` = `"caution"`
     - `archive.min_dir_bytes` = 10485760；`archive.grow_bytes` = 52428800；`archive.grow_percent` = 20
     - `media_store.enabled` = `true`；`media_store.collections` = images/video/audio/downloads；`media_store.timeout_seconds` = 60；`media_store.max_items` = 200000
     - `app_storage.enabled` = `true`；`app_storage.timeout_seconds` = 30；`app_storage.max_packages` = 1000；`app_storage.top_apps` = 15
10. **CLI 子命令**（`python adc.py --help` → 15 个）：
    `devices`、`history`、`export`、`doctor`、`scan`、`analyze`、`inspect`、`plan`、`select`、`execute`、`verify`、`quarantine`、`restore`、`recheck`、`archive`
    - 全局/通用参数必须写在子命令之后：`--state`、`--transport {adb,hdc,rish,local,mock}`、`--serial`、`--adb-path`、`--hdc-path`、`--rish-path`、`--config`、`--json`、`--quiet`
11. **已有文档**：
    - `docs/install-and-usage.md`、`docs/cli-contract.md`、`docs/harmonyos-support.md`
    - `docs/permissions-and-compatibility.md`、`docs/limitations-and-roadmap.md`
    - `docs/real-device-test-plan.md`、`docs/test-report.md`、`docs/research-android-storage.md`
    - `android-deep-clean/references/*.md`（safety-policy、android-access、path-rules、review-guide、workflow、report-format、installation、troubleshooting、usb-adb-workflow、dual-path-architecture、harmonyos、directory-archive）

## B. 需人工补充（必填）

- **一句话描述**（< 120 字符）：面向 Android 与 HarmonyOS 的 AI 深度清理 Skill：只读扫描、证据复核、隔离清理与恢复。
- **为什么做这个项目**：普通清理工具按目录名、年龄或体积判断，容易误扫聊天图片、卸载残留中的存档、离线安装包，也不解释原因。本项目把清理拆成可审计的步骤：只读扫描、证据、AI 复核、用户选择、令牌确认、隔离执行、验证与恢复。
- **目标用户 / 前置知识**：调用 Skill 的 AI Agent 及其操作者；需要会用终端、了解 USB 调试与基本 PowerShell/Bash 命令。目标读者为 AI Agent 与操作者（用户 2026-11-06 确认）。
- **与同类方案的差异**：证据驱动、AI 逐项复核、未复核 CAUTION 不可选、确认令牌、设备身份绑定、默认隔离可恢复、执行前逐文件 stat 复核、双通路共用同一套规则。
- **已知限制 / 明确不做的事**：见 README「已知限制」及 SKILL.md；未完成真实用户数据清理验收，HarmonyOS NEXT 真机验收为 0，手机端 Termux/Shizuku 未真机验收，MediaStore 只在 1 台 HarmonyOS 3.0 兼容版真机验证且该 ROM 的 downloads 集合不可用；应用级存储只统计基础 APK 与外部目录，内部 data/cache 不可读，隔离不释放空间，不做相似图片视觉比对，不覆盖系统级清理。
- **演示素材路径**（截图 / GIF）：无截图；首屏使用真实终端节选 + Mermaid 流程图（用户 2026-11-06 确认）。
- **目标读者画像**：AI Agent 与操作者（用户确认）。
- **期望读者读完能做什么**：能判断该用哪条通路，按顺序完成 doctor → scan → analyze → 复核 → plan → select → 用户确认 → execute → verify，并知道何时 restore 或 purge。
- **主通路选择**：两条并重（用户 2026-11-06 确认）。
- **完成度表述**：如实标注（用户 2026-11-06 确认）。

## 核对记录

- [x] 所有命令实际执行过（`--help`、`--version`、`pytest`）
- [x] 所有环境变量在代码中能搜到（`ADC_STATE`）
- [x] 目录结构与实际一致
- [x] B 部分已由用户确认定位（目标读者、主通路、完成度、演示素材）
