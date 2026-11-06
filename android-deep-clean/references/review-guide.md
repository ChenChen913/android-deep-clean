# AI 复核指南

Analyzer 只提供候选和证据；是否真正无用由 AI 结合上下文复核。目标不是“多删”，而是“找到真正无用且不会后悔的文件”。

## 1. 复核输入

优先读取：
- analysis-summary.json：小、适合直接阅读，含 review_items、items、limitations。
- review-template.json：把 decisions 中每个空 verdict 填上。
- report-before.md：人类可读，适合向用户转述。

按需读取：
- analysis.json：完整证据（大）。
- analysis.db：完整文件清单；AI 一般只看 sample_paths。
- inspect --id <ID>：查看候选全部路径、实时目录树、拉预览。

## 2. 逐组决策流程

1. 读 title、category、risk、file_count、total_bytes、evidence、reasons、keep_suggestion。
2. 判断证据是否自洽：
   - 路径是否真属于该类别（cache 目录 vs files 目录）；
   - 包是否确实未安装（residue）；
   - 年龄/大小是否达到阈值；
   - 文件数/大小是否与预期一致；
   - 是否在受保护路径/用户个人目录。
3. 对以下情况必须 inspect：
   - total_bytes ≥ 500MB 或 file_count ≥ 1000；
   - risk=CAUTION 且涉及个人数据（media/screenshots/residue/chat）；
   - evidence 含“未知”“不对应任何已知包名”“可能包含”；
   - 路径/应用与用户当前目标相关。
4. 需要看内容时：
   - inspect --id <ID> --pull 3 拉取前几个文件到本机 preview；
   - 图片可用图像查看能力；不要用文件名判断照片内容；
   - 文档/压缩包不要解压后删除原文件；只读预览。
5. 给出 verdict：
   - trash：有充分证据，清掉不会影响用户数据/功能；
   - keep：明确有保留价值或证据不足；
   - uncertain：不确定，默认保留。
6. 写 reason：说明“检查了什么、为什么”。至少包含证据要点，不能只写“缓存可删”。
7. 风险：只能保持或上调。DANGEROUS 禁止 trash。

## 3. 类别判定模板

### app_cache / browser_cache / thumbnails
trash 条件：
- 路径确实位于 cache/code_cache 或 .thumbnails；
- 不包含 databases/files/shared_prefs；
- 不是用户手工创建的目录；
- 大缓存已 inspect，未发现离线包/重要下载。
reason 示例：已检查 cache 目录与 sample_paths，均为可再生缓存文件，不含离线视频/账号数据；用户确认可清。

### chat_cache
trash 条件：
- 路径仅为 tencent/QQ/微信目录中的 cache/temp/tmp；
- 不含 image2/video2/voice2/emoji/Backup/WeChat Files；
- 用户明确接受清理聊天缓存。
默认 CAUTION；不要因为“微信缓存大”就全选。

### residue
trash 条件：
- 包不在已安装列表（pm list packages）；
- 已 inspect 目录内容，没有游戏存档、下载、聊天备份、工作文件；
- 用户确认不重装该应用。
否则 keep/uncertain。多用户/工作资料/公司应用尤其谨慎。

### duplicates
trash 条件：
- SHA-256 相同；
- 已确认保留建议的那份存在且可访问；
- 用户确认删除多余副本。
不要删 keep 项；不要按相似名字判重。

### screenshots / media_old
默认 keep/uncertain，除非：
- 用户明确要求清理且已看内容；
- 或确认为重复副本（duplicates 类）。
没有图像查看能力时，不得凭文件名把个人照片标 trash。

### apk / archives / ota
trash 条件：
- 长期未修改；
- 用户确认不需要离线重装/降级/备份；
- 不是唯一备份。
默认 keep/uncertain。

### temp_logs / trash / empty_dirs
- temp/log：确认是临时扩展名/日志，且不是正在排障的日志。
- trash：确认用户不再需要恢复。
- empty_dirs：确认不是应用依赖的占位目录；rmdir 非空会失败。

### large_old
trash 条件：
- 用户确认用途；
- 文件不是项目资料/唯一副本/系统镜像。
仅凭“大+旧”不能 trash。

## 4. review.json 模板

~~~json
{
  "schema": 1,
  "analysis_id": "analysis-xxxxxxxxxx",
  "decisions": [
    {
      "id": "app_cache-xxxxxxxxxx",
      "verdict": "trash",
      "risk": "SAFE",
      "reason": "已检查 cache 目录及 sample_paths，均为可再生缓存；不含离线包；用户同意清理",
      "evidence_checked": ["path", "sample_paths", "size", "package"]
    },
    {
      "id": "screenshots-xxxxxxxxxx",
      "verdict": "keep",
      "risk": "CAUTION",
      "reason": "截图内容未确认，可能含个人信息；保留",
      "evidence_checked": ["path", "sample_paths"]
    }
  ]
}
~~~

## 5. 常见错误

- 只按“缓存/旧/大”批量 trash，不看路径；
- 把 CAUTION 改成 SAFE 以绕过限制；
- 对未安装包残留直接全选；
- 把微信 image2 当缓存；
- 把重复照片的“保留建议”删掉；
- 不确定时填 trash 而不是 uncertain；
- 不写 reason 或写空洞理由；
- 用户拒绝后继续尝试 execute。

## 6. 与用户的沟通

向用户展示时使用：
- “准备处理：N 组 / M 个文件 / X GB（逻辑大小估算）”
- “其中 SAFE：…；CAUTION：…（需要你逐项确认）；DANGEROUS：已保护，不会动”
- “默认隔离，可恢复；实际释放需后续 purge”
- “以下文件我不会动：个人照片原片、聊天记录、数据库、已安装 OBB、未知文件”

不要用“已深度扫描全盘”这种无法证明的说法；用 doctor 的实际权限与 skipped 路径说话。
