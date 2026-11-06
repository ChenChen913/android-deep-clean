# 故障排查与降级路径

## 1. ADB 相关

| 现象 | 可能原因 | 处理 |
|---|---|---|
| adb devices 为空 | USB 模式仅充电、未授权、驱动问题 | 切文件传输模式、重新插拔、确认手机 RSA 弹窗、安装驱动 |
| unauthorized | 未在手机确认调试授权 | 撤销 USB 调试授权后重新连接 |
| adb: command not found | Platform Tools 不在 PATH | 用 --adb-path 指定完整路径或加入 PATH |
| 多设备报错 | 未指定 serial | 用 --serial 指定 |
| 无线调试连不上 | 配对端口/调试端口混淆、网络隔离 | 重新配对；同一局域网；关闭 VPN |
| adb shell 被安全软件拦截 | 公司 MDM/安全软件 | 换电脑/数据线；不要绕过公司策略 |

## 2. 权限相关

| 现象 | 处理 |
|---|---|
| /sdcard/Android/data 不可读 | 报告限制；建议 Root/Shizuku 或系统设置清理；不得声称已扫描 |
| /data/data 不可读 | 无 root 的预期行为；跳过内部缓存，报告限制 |
| su 不可用但用户以为有 root | --root-mode su 会失败；回到 shell 模式并说明 |
| mock 下权限探测全通过 | 检查 caps.json 是否配置了 inaccessible/denied_ops；mock 是模拟，不代表真机 |
| 无法创建隔离区 | 检查 /sdcard 可写；不要改为直接删除 |

## 3. 扫描相关

| 现象 | 处理 |
|---|---|
| 扫描很慢 | 缩小 --roots；调低 --max-files；避免全盘重复扫描；用 --quiet |
| 扫描被截断 | 报告截断；增大 max_files/缩小根目录后重扫；不得说“全盘完成” |
| find/stat/xargs 不可用 | doctor 会报告；换 Shizuku/root 或改用其他设备；不要假装扫描 |
| 文件名含换行/特殊字符导致条目缺失 | 已知限制；报告中说明；不要手工猜测路径 |
| 空目录检测把应用占位目录列出 | CAUTION；review 时保留或不确定 |

## 4. 分析相关

| 现象 | 处理 |
|---|---|
| 没有候选 | 可能确实干净/权限不足/阈值过高；读 limitations；可调 config 阈值后重扫 |
| 残留检测为空 | pm 不可用或 Android/data 不可读；报告限制 |
| 重复检测为空 | 无 hash 工具或文件小于阈值；报告限制 |
| 候选类别不全 | 检查 generator 失败记录（limitations）；修正配置/路径规则 |
| 阈值太激进 | 用 config.json 覆盖，不要改产品默认值去迎合一次任务 |

## 5. 执行相关

| 现象 | 处理 |
|---|---|
| 无令牌/令牌错误 | 重新 select；不要伪造/复用令牌 |
| 用户拒绝 | 停止；不执行；保留报告 |
| plan_hash 不一致 | plan.json 被修改或选择过期；重新 plan/select |
| 文件大小/mtime/inode 变化 | 跳过并报告；重新 scan/plan；不得强行删 |
| 符号链接/路径穿越被拒绝 | 预期安全行为；不要绕过 |
| 删除/移动权限不足 | 报告失败；源文件保留；尝试其他用户授权方式需用户同意 |
| 隔离区空间不足 | 先释放其他空间；不要改为直接删除 |
| 部分失败 | status=partial；报告失败清单；不要重试为强删 |
| 设备断开 | 按 journal 恢复/verify；重连后继续 |

## 6. 恢复与释放

- restore 要求原路径不存在；若已被应用重建，先人工确认再处理，工具会拒绝覆盖。
- purge 必须使用 execution.json 中的 purge_token；token 错误拒绝。
- purge 后不可恢复；必须用户再次明确同意。
- 隔离文件仍在设备上时空间未释放，这是预期行为。
- 状态目录/journal 丢失时，不要凭记忆手工移动；重新扫描并人工核对。

## 7. Windows/编码

- 输出乱码：用 UTF-8 终端；CLI 已设置 stdout/stderr UTF-8。
- 路径含中文/空格：用引号包裹；CLI 内部 pathlib 处理。
- PowerShell 续行：注意命令换行；建议单行或使用变量。
- 长路径：Windows 260 限制可能影响状态目录；把 --state 放在较短路径。
- 防病毒软件锁文件：关闭对 .adc-state 的实时扫描或换目录。

## 8. 真机兼容性

- 不同 OEM 的 toybox find/stat/xargs 参数支持可能不同；doctor 探测 commands，失败时报告。
- 微信/QQ/抖音路径随版本变化；不要硬编码“某版本路径”作为唯一依据。
- 部分 ROM 的 Android/data 对 shell 不可读；以实际探测为准。
- Android 11+ 的 FUSE 放行是 AOSP 实现细节，不是 API 契约；OEM 可收紧。
- 所有待真机验证项见 docs/research-android-storage.md 第 10 节。
