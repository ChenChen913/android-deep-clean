# 安全策略与风险模型

本文件是 SKILL.md 的强制补充。任何清理流程都必须遵守；与用户口头要求冲突时，以本文件为准。

## 1. 风险分级

| 等级 | 定义 | 默认行为 | 可选性 |
|---|---|---|---|
| SAFE | 有充分证据证明可再生或无用（例如明确的 cache 目录、临时文件、已失效缩略图） | 可由用户在明确授权后批量处理 | 可进入选择清单 |
| CAUTION | 存在不确定性或可能含用户数据（卸载残留、旧 APK、重复媒体、截图、旧压缩包、聊天缓存） | 只展示，必须 AI 逐项复核 + 用户逐项确认 | 复核 verdict=trash 后才可选 |
| DANGEROUS | 个人数据、应用数据、系统文件、数据库/密钥、已安装 OBB、未知用途 | 默认禁止删除，不提供执行入口 | 永不可选 |

风险只能保持或上调，不能下调。AI 不能把 CAUTION 改成 SAFE；review.json 中降低风险会导致 plan 直接报错。

## 2. 绝对保护清单

### 路径
- /system、/vendor、/product、/product_services、/odm、/oem、/apex、/boot、/init*
- /proc、/sys、/dev、/acct、/config、/efs、/firmware、/persist、/metadata
- /data/app、/data/apex、/data/misc、/data/system、/data/security、/data/tombstones、/data/anr、/data/dalvik-cache、/data/adb、/data/vendor、/data/backup、/data/ssh
- /data/data 下除 cache、code_cache 以外的内容（即使 root）
- /sdcard/Android/obb/<已安装应用>、/sdcard/Android/media/<已安装应用>
- /sdcard/Android/data 下已安装应用的非 cache/code_cache/tmp 目录
- /sdcard/DCIM、Pictures、Movies、Music、Documents 等个人目录中的原片与文档（只有重复/截图/旧媒体等候选，且必须逐项确认）
- 聊天应用目录中的 image2、video2、voice2、emoji、Backup、WeChat Files、Msg、database 等
- 本 Skill 自己的隔离区 /sdcard/.android-deep-clean
- 用户配置的 extra_protected_paths

### 文件名/类型
- *.db、*.sqlite、*.realm、*.mmkv、*-wal、*-shm
- *.keystore、*.jks、*.p12、*.pfx、*.pem、*.key、*.cer、*.crt、*.der
- shared_prefs、databases、files、no_backup、app_webview、sessions 目录
- .nomedia（删除会导致媒体扫描行为变化）
- 未知扩展名/无扩展名的未知文件：默认保留

### 行为
- 不跟随符号链接；不删除链接目标；不移动/删除挂载点。
- 不递归删除目录；只用 rm -f 单文件或 rmdir 空目录。
- 不删除整个用户目录；不按通配符批量删除；只处理候选清单中的精确路径。

## 3. 删除前强制检查（由 executor 执行）

1. 校验 selection 的 plan_hash、selection_hash 与 token。
2. 校验 plan.json 未被修改（重新计算 plan_hash）。
3. 校验每个选中项：risk 不是 DANGEROUS；CAUTION 有 review verdict=trash 和 reason；SAFE 可选。
4. 对每个文件重新 stat：
   - 必须是普通文件（不是目录、符号链接、设备文件）；
   - size、mtime、inode/dev 与计划时一致；
   - realpath 不指向其他位置（防符号链接父目录/重定向）；
   - 路径在允许的清理根内，且通过 guard_path 重检。
5. 任一项不通过：跳过该文件并写入失败记录，不“强行”删除。
6. 目标目录必须是目录且 realpath 正常；文件必须位于候选目标目录内。
7. 隔离目标不存在、位于本 Skill 隔离区、不会覆盖已有文件。
8. 默认 quarantine：move 到隔离区并校验大小；move 失败则不删除。
9. 永久删除：仅在 --mode delete + --allow-permanent-delete + 全部 SAFE 时允许。

## 4. 令牌与用户确认

- select 生成随机令牌（形如 ADC-XXXXXXXX），只对本次 selection 有效。
- 用户必须明确看到：路径、数量、大小、风险、隔离/永久模式。
- 用户拒绝、未回复、要求修改：不得 execute；修改范围后重新 select（旧令牌作废）。
- 不把令牌当作“用户已同意”的替代；令牌只是防误触/防篡改的技术闸门。
- purge 令牌在 execute 时生成，只有用户再次明确同意后才可使用。purge 不可恢复。

## 5. 隔离与恢复

- 外部存储文件隔离到 /sdcard/.android-deep-clean/quarantine/<exec_id>/files/sdcard/...
- 内部存储文件（root）隔离到 /data/local/tmp/.android-deep-clean/quarantine/<exec_id>/files/data/...
- journal.jsonl 逐条记录 source/quarantine path/size/item/status；execution.json 汇总。
- restore 按 journal 恢复；原路径已存在则拒绝覆盖并报告冲突。
- purge 只删除 journal 中 status=quarantined 且路径在隔离根内的普通文件；不递归。
- 隔离期间空间不会释放；向用户说明“已隔离 N MB，实际释放 0，purge 后释放”。

## 6. 失败处理

| 情况 | 行为 |
|---|---|
| 权限不足（读/删） | 跳过 + 报告；不重试，不用 root 绕过未经用户同意 |
| 文件变化（size/mtime/inode） | 跳过 + 标记 changed_since_plan；建议重新扫描/计划 |
| 目标目录非空 / 被占用 | rmdir 失败；不递归删除；报告 |
| 隔离区不可写/空间不足 | 执行前失败；不改为直接删除 |
| move 跨文件系统失败 | 报告失败，源文件保留 |
| journal 丢失 | restore 拒绝；不要按记忆手工恢复 |
| 设备断开 | 中止；已执行的文件在 journal 中；重连后 verify |
| 扫描截断/超时 | 报告不完整；不得声称已全盘扫描 |

## 7. 报告必须诚实

- 区分“逻辑大小”“已隔离”“实际释放”。
- 明确列出未能访问的路径和原因。
- 失败项目必须列出路径与原因。
- 不得用“清理完成”掩盖部分失败；状态用 completed / partial / failed。
- 不得把 DANGEROUS 项描述成“可清理”以诱导用户。
