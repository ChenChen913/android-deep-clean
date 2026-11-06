# 清理类别、路径规则与误删风险

本文件帮助 AI 理解 analyzer 生成的类别，以及每一类为什么这样分级。路径随 Android 版本/ROM/应用版本变化，必须以扫描到的真实路径为准，不得照抄。

## 1. 类别总表

| 类别 | 典型路径 | 默认风险 | 安全前提 | 常见误判 |
|---|---|---|---|---|
| app_cache | /sdcard/Android/data/<pkg>/cache、/data/data/<pkg>/cache、code_cache | SAFE | 目录名确为 cache/code_cache；无离线包/下载；已安装应用 | 把 files/、databases/ 当缓存；离线地图/离线视频在 cache 中 |
| browser_cache | 浏览器包名下的 cache | SAFE | 包名确为浏览器 | 登录态/书签在 databases、files，不在 cache |
| chat_cache | /sdcard/tencent/.../cache、QQ 缓存目录 | CAUTION | 仅 cache/temp 子目录；不碰 image2/video2/voice2/emoji/Backup | 把聊天图片/语音/文件当缓存 |
| thumbnails | .thumbnails、thumbnails、.thumb | SAFE | 确为缩略图缓存目录 | 把用户自建 thumbnails 目录当缓存 |
| temp_logs | *.tmp、*.part、*.crdownload、*.log、*.dmp | SAFE | 扩展名与路径符合临时/日志；超过保留期 | 正在排障的日志；日志中包含用户数据 |
| trash | .trash、.trashed-*、.recycle | CAUTION | 确为回收站且文件超过保留期 | 用户最近删除、仍想恢复的文件 |
| duplicates | 同 size + SHA-256 相同 | CAUTION | 至少保留一份；逐组确认 | 仅同名/同大小/相似内容≠重复 |
| apk | *.apk、*.apks、*.xapk、*.apkm | CAUTION | 长期未修改；用户确认不需重装/降级 | 唯一离线安装包、工作用包 |
| archives | *.zip、*.7z、*.rar、*.tar、*.bak | CAUTION | 旧且大；用户确认非唯一备份 | 唯一照片/项目备份 |
| screenshots | Screenshot_*、截屏、屏幕截图 | CAUTION | 用户/内容确认；超过保留期 | 含票据/账号/工作资料的截图 |
| large_old | 任意大文件 > 阈值且 >365 天 | CAUTION | 用户确认用途 | 项目资料、镜像、离线资料 |
| media_old | DCIM/Pictures/Movies 中的照片视频 | CAUTION | 逐项 + 建议先备份；绝不自动全选 | 家庭照片、原片、重要视频 |
| download_old | /sdcard/Download 旧文件 | CAUTION | 用户确认 | 工作资料、待安装包、待上传文件 |
| ota | update*.zip、ota*.zip、firmware*.bin | CAUTION | 确认不需刷机/降级 | 救砖包、降级包 |
| empty_dirs | 长期为空的深层目录 | CAUTION | 只用 rmdir，非空失败；应用占位目录可能有用 | 应用依赖的固定目录结构 |
| residue | /sdcard/Android/data|obb|media/<uninstalled-pkg> | CAUTION | 包确实未安装；检查存档/下载/媒体 | 包名变化、多用户、工作资料/公司应用、重装后数据 |
| protected | 数据库、密钥、OBB、应用数据 | DANGEROUS | 永不选 | 任何“看起来像垃圾”的猜测 |

## 2. 应用路径参考

### 微信
- /sdcard/tencent/MicroMsg/<hash>/：账号数据根，风险极高。
- image2、video2、voice2、emoji、avatar、sns、Backup、WeChat Files：聊天媒体/备份/文件，默认保护。
- 只考虑 cache、temp、tmp、.thumbnails 等明确缓存子目录；即使如此仍是 CAUTION，因为微信目录语义随版本变化。
- /sdcard/Android/data/com.tencent.mm/cache：应用外部缓存，按 app_cache 处理。

### QQ / TIM
- /sdcard/tencent/QQ_Images、QQ_audio、QQ_video、QQfile_recv：用户文件/聊天媒体，保护。
- /sdcard/Android/data/com.tencent.mobileqq/cache：应用缓存，SAFE 候选。
- /sdcard/Tencent/QQfile_recv 等旧路径：保护。

### 浏览器
- Chrome：/sdcard/Android/data/com.android.chrome/cache、/data/data/com.android.chrome/cache。
- 夸克/UC/三星/Edge/Firefox：对应包名下 cache。
- 浏览器下载目录通常在 /sdcard/Download，按下载/大文件候选；书签、登录态在 files/databases，保护。

### 短视频/视频
- 抖音 com.ss.android.ugc.aweme、快手 com.smile.gifmaker：cache 可清，离线缓存/草稿/作品目录必须保护；不要把整个应用目录当缓存。
- B 站/腾讯视频/爱奇艺/优酷：cache 可清；离线下载目录保护或逐项。

### 卸载残留
- 判断：pm list packages 已安装集合中不存在该包；pm list packages -u 可能仍列出残留记录。
- 位置：/sdcard/Android/data/<pkg>、/sdcard/Android/obb/<pkg>、/sdcard/Android/media/<pkg>、/data/data/<pkg>（仅 root）。
- 外部残留可能含游戏存档、下载、聊天备份；CAUTION，逐包确认。
- 内部残留即使 root 也只清 cache/code_cache；整目录删除过危险。

### 旧安装包
- /sdcard/Download/*.apk、/sdcard/Download/*.apks、/sdcard/backup/app/*.apk。
- 检查包名/版本；长期未修改且用户确认不需要重装/降级才清。
- 同版本/更高版本已在系统中安装时，旧 APK 价值低；但工作/离线场景仍可能保留。

### 重复照片/视频
- 完全重复：同大小 + SHA-256 相同；analyzer 会给出保留建议（优先 DCIM/Camera、原路径、较旧时间）。
- 相似但非完全相同：本 Skill 不自动判重；AI 可用 inspect --pull 查看，但仍必须用户确认，不得批量。
- 截图重复：同样只删多余副本；保留一份。

### 空目录
- 只对 /sdcard 下深度≥3、超过 30 天、guard 允许的目录执行 rmdir。
- 非空目录 rmdir 会失败；executor 将其记为失败，不会递归。
- Android/data 内的应用缓存空目录通常不作为独立候选，由 cache 候选处理。

## 3. 高风险误判示例（必须避免）

1. 把 /sdcard/Android/data/com.foo/files/ 当缓存删除 → 可能是账号/离线数据。
2. 把微信 image2 当“图片缓存”批量删 → 聊天图片丢失。
3. 把 DCIM 里的旧照片按“大/旧”自动全选 → 家庭照片丢失。
4. 把 .nomedia 当垃圾 → 媒体扫描行为改变，隐藏文件暴露。
5. 删除整个 Android/data/<pkg> → 应用配置/存档/下载全部丢失。
6. 只按文件名含 cache 就删 → 用户自建 cache 目录。
7. 按 atime 判断“未使用” → Android 常不更新 atime。
8. 把同名不同内容的文件当重复 → 必须哈希。
9. 把应用占位空目录删除 → 应用启动异常。
10. 把 /sdcard/Download 的 .apk 自动删 → 用户可能正准备安装。

## 4. 建议阈值（可在 config.json 覆盖）

| 阈值 | 默认 | 说明 |
|---|---|---|
| min_duplicate_bytes | 1MB | 小于此不参与重复检测 |
| large_file_bytes | 100MB | 大文件候选 |
| old_file_days | 365 | 大文件“长期未用” |
| apk_old_days | 180 | 旧 APK |
| archive_old_days | 180 | 旧压缩包/OTA |
| temp_old_days | 30 | 临时文件 |
| log_old_days | 90 | 日志 |
| screenshot_old_days | 180 | 截图 |
| media_old_days | 730 | 旧媒体候选 |
| trash_old_days | 30 | 回收站 |
| empty_dir_old_days | 30 | 空目录 |
| download_old_days | 180 | 旧下载 |
| large_cache_caution_bytes | 1GB | 大缓存上调 CAUTION |

阈值只是筛候选，不是删除依据；最终必须结合路径、包状态、内容和用户选择。
