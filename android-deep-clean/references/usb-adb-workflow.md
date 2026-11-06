# USB + ADB 电脑端工作流（Windows 11）

本文件只描述**电脑端通路**。手机端通路（Termux/local 或 Shizuku/rish）见双通路架构文档；两条通路共用同一套分类、风险、确认、执行和验证代码。

## 1. 电脑准备

1. 下载 Android 官方 Platform Tools（含 adb.exe），解压到例如 C:\platform-tools。
2. 加入 PATH，或用 --adb-path 指定 adb.exe：

~~~powershell
adb version
adb devices -l
~~~

不需要 Android Studio；只需要 Platform Tools。也支持用 winget/choco 安装 adb，但必须确认版本可用。

## 2. 手机连接与授权

1. 手机：设置 → 关于手机 → 连点“版本号”7 次开启开发者选项。
2. 开发者选项 → 打开“USB 调试”。
3. 用**支持数据传输**的 USB 线连接电脑（不是仅充电线）。
4. 手机解锁，出现“允许 USB 调试吗？”时勾选“始终允许此计算机”并允许。
5. 电脑执行：

~~~powershell
adb devices -l
~~~

状态必须是 device；如果是 unauthorized，去手机点允许；offline 重新插拔/重开 USB 调试；no permissions 检查驱动/数据线/USB 模式。

## 3. Skill 的设备发现与选择

~~~powershell
$Skill = "$env:USERPROFILE\.agents\skills\android-deep-clean"
$State = ".adc-state"

# 列出所有 ADB 设备与授权状态（不需要 --serial）
python "$Skill\scripts\adc.py" devices --adb-path adb

# 只有一个已授权设备时，doctor 会自动选它
python "$Skill\scripts\adc.py" doctor --state $State --transport adb

# 多台设备必须用 --serial，禁止自动挑一台
python "$Skill\scripts\adc.py" doctor --state $State --transport adb --serial <序列号>
~~~

安全约束：
- 检测到多个已授权设备但未指定 --serial：直接失败，并列出序列号。
- 设备未授权/离线/无权限：给出明确处理说明，不执行扫描或删除。
- 每次 scan/plan/selection/execution 都带设备身份（serial + fingerprint + model + sdk 的短哈希）；计划只能由同一设备执行。

## 4. 权限探测

~~~powershell
python "$Skill\scripts\adc.py" doctor --state $State --transport adb --serial <序列号>
~~~

重点看：
- 权限级别 full/standard/limited/blocked；
- /sdcard、Android/data、Android/obb、Android/media、/data/data 是否可读；
- 是否可写 /sdcard（quarantine 需要）；
- pm、find、stat、xargs、sha256sum、df、realpath 是否可用；
- limitations 中“未扫描”的目录。

报告只会按实际探测结果声称能力；ADB shell 通常能读写共享存储，但 Android/data 的可见性取决于 Android 版本/OEM，不能假设。

## 5. 扫描

~~~powershell
python "$Skill\scripts\adc.py" scan --state $State --transport adb --serial <序列号>
~~~

- 默认 /sdcard；Root 下追加 /data/data/*/cache。
- 可选 --roots、--max-depth、--max-files、--no-internal。
- 电脑端可利用更大的内存/磁盘；大目录可分根扫描，或提高 max_files。
- 扫描只读，不会写手机。

扫描会自动把上一轮快照归档到状态目录 history/scan-*（最多 10 份），方便对比：

~~~powershell
python "$Skill\scripts\adc.py" history --state $State
~~~

## 6. 分析（含电脑本地哈希）

~~~powershell
# 设备端 toybox 有 sha256sum 时（默认）
python "$Skill\scripts\adc.py" analyze --state $State --transport adb

# 设备缺少 hash 工具，或希望由电脑计算重复候选哈希
python "$Skill\scripts\adc.py" analyze --state $State --transport adb --hash-mode pc
~~~

--hash-mode pc 只把“大小相同的重复候选”通过 USB 拉到电脑，用电脑 CPU 计算 SHA-256；不会把全部文件内容拉过来。分析结果会记录 hash_mode。

## 7. AI 复核与计划

~~~powershell
python "$Skill\scripts\adc.py" inspect --state $State --transport adb --serial <序列号> --id <候选ID> --pull 3
python "$Skill\scripts\adc.py" plan --state $State --transport adb --review review.json
~~~

复核规则与手机端完全一致：CAUTION 必须逐项 verdict，DANGEROUS 不可选，不凭文件名/年龄猜测。

## 8. 用户选择

~~~powershell
python "$Skill\scripts\adc.py" select --state $State --transport adb --all-safe
# 或 --categories app_cache,thumbnails / --ids ... / --app-kinds chat
~~~

selection 会显示目标设备身份、文件路径/数量/大小/风险/隔离模式，并生成确认令牌。没有用户明确确认，禁止执行。

## 9. 执行（可备份到电脑）

~~~powershell
# 默认隔离（设备内可恢复）
python "$Skill\scripts\adc.py" execute --state $State --transport adb --serial <序列号> --token <TOKEN>

# 清理前额外备份到电脑（推荐对 CAUTION/个人文件使用）
python "$Skill\scripts\adc.py" execute --state $State --transport adb --serial <序列号> --token <TOKEN> --backup-to D:\phone-backup
~~~

--backup-to 行为：
- 每个文件清理前复制到 <backup-to>/<execution_id>/sdcard|data/<相对路径>；
- 校验大小；设备端 hash 可用时校验 SHA-256；
- 备份失败 → 该文件不清理，源文件保留，记录失败原因；
- 备份成功 → 再执行 quarantine 或 delete；
- 输出 execution/backup-manifest.json。

设备断开：
- 执行前检查身份与健康状态；
- 每处理一个项目前检查连接；
- 单个文件操作中检测到断开，立即停止后续项目；
- execution 状态为 aborted，已处理项目保留 journal，未执行项目标记 aborted。

## 10. 验证与报告

~~~powershell
python "$Skill\scripts\adc.py" verify --state $State --transport adb --serial <序列号>
python "$Skill\scripts\adc.py" export --state $State --out D:\phone-report
~~~

export 生成：
- analysis-items.csv：候选组汇总；
- analysis-files.csv：每个文件的完整路径/大小/角色；
- execution-items.csv：执行结果（若有）。

Markdown 报告在状态目录各阶段生成；CSV 适合 Excel/审计。

## 11. 恢复与释放

~~~powershell
# 从手机隔离区恢复
python "$Skill\scripts\adc.py" restore --state $State --transport adb --all

# 确认不再需要后永久释放隔离区
python "$Skill\scripts\adc.py" quarantine purge --state $State --transport adb --token <PURGE_TOKEN>
~~~

电脑备份（--backup-to）是独立副本，不受手机 purge 影响；需要时从备份目录复制回手机。

## 12. 断连/重连步骤

1. 立即停止操作（Skill 会 aborted）。
2. 重新插拔/授权，运行 devices 确认同一序列号。
3. 重新 doctor/scan/analyze/plan/select；不要复用旧令牌或旧计划。
4. 如果已经隔离部分文件，verify 后决定 restore/purge。

## 13. Windows 常见问题

| 现象 | 处理 |
|---|---|
| adb 不是命令 | 安装 Platform Tools，PATH 加目录，或 --adb-path |
| 无设备 | 数据线/授权/USB 模式/驱动；换线换口 |
| unauthorized | 手机解锁，重新允许 USB 调试；撤销授权后重连 |
| 多设备 | devices 查序列号，--serial 指定 |
| Android/data 不可读 | scoped storage/OEM 限制；报告 limitations，不假扫描 |
| 备份很慢 | 只对需要的项目备份；大文件备份占电脑磁盘 |
| 执行中断 | 重新连接同一设备，verify/restore/purge；不要复用旧 token |
