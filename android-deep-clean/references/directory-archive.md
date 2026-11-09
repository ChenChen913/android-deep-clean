# 跨轮目录档案

> 适用于 v1.3.0+。档案在主机侧运行，不访问手机。

## 它解决什么

单次扫描只能看到“现在有什么”。跨轮档案记录每个目录上一轮的大小和活跃度，下一轮对比后能回答：

- 哪些目录是新出现的；
- 哪些目录明显变大或变小；
- 哪些目录本轮没有出现；
- 哪些目录删掉后又回来了；
- 哪些应用目录在包已卸载后仍然存在；
- 哪些目录还没有被 AI 或用户审查过。

## 命令

```powershell
python "$Skill\scripts\adc.py" scan --state $State --transport adb --serial <序列号>
python "$Skill\scripts\adc.py" archive diff --state $State
python "$Skill\scripts\adc.py" archive stats --state $State
python "$Skill\scripts\adc.py" archive mark --state $State --path /sdcard/DCIM/Camera --decision keep --note "用户相片目录"
```

`scan` 默认更新档案；`--no-archive` 跳过。首轮只建立基线，后续轮次显示变化。`recheck` 会复用主状态目录的档案，不会写进临时复查目录。

## 档案位置与结构

`$State/history/archive.db` 是紧凑 SQLite 档案，记录：

- `environments`：设备序列号、型号、指纹、SDK、平台、当前用户；
- `directories`：目录路径、直接/子树大小、文件数、最新 mtime、首次/最近记录、连续缺失轮次、复活次数、应用包名/版本、标记与备注；
- `packages`：包安装状态、版本变化、首次/最近出现；
- `scans`：每轮扫描的索引。

每轮差分写入 `$State/scan/directory-diff.json`。

## 差分分类

- `new`：上轮档案没有记录；
- `grew`：增量 ≥ `archive.grow_bytes` 且百分比 ≥ `archive.grow_percent`；
- `shrunk`：缩小 ≥ `archive.grow_bytes`；
- `vanished`：本轮完整扫描中未再出现；
- `resurrected`：曾连续缺失，本轮重新出现；
- `app_updated`：目录所属应用版本发生变化；
- `orphan_suspect`：Android/data、obb、media 下的包目录，包未安装或不对应已知包名；
- `unreviewed`：没有 keep/watch 标记的目录。

默认阈值在 `archive` 配置段：`min_dir_bytes`、`grow_bytes`、`grow_percent`、`max_entries`、`max_diff_items`。

## 防误判规则

- 扫描截断、根目录不可读、跳过路径、达到深度限制时，不做消失判断。
- 设备指纹、SDK、平台或当前用户变化时，重新建立基线，不把全部目录标成新增或消失。
- 应用版本变化只作为增长线索，不直接判成卸载残留。版本信息依赖 `pm list packages --show-versioncode`；设备不支持时该字段为空，不会做版本变化判断。
- 目录按路径识别；重命名会显示为“未再出现 + 新增”。
- 档案达到条目上限时，本轮不做消失判断。

## 安全边界

- 档案只写主机侧状态目录，不改手机文件。
- `archive mark` 只有 keep/watch/neutral，只是分析提示。
- 标记不改变候选风险、不生成候选、不进入 plan/select/execute。
- 未在真机完成验收前，档案变化只能作为线索，不能写成“已确认残留”或“已删除”。
