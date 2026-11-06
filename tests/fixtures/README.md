# 测试 Fixture 模式（tests/fixtures）

本目录是 mock Android 设备的**模式说明与可复用静态样例**。真正的文件树由
`tests/adc_testkit.py` 在每个测试的 pytest 临时目录里动态生成，不提交大文件。

## 目录结构

测试运行时，临时目录布局如下：

```
<tmp_path>/
  phone/                      # --mock-root：模拟 Android 设备文件树
    sdcard/                   # /sdcard 与 /storage/emulated/0
      Android/data/<pkg>/...
      Android/obb/<pkg>/...
      Android/media/<pkg>/...
      DCIM/Camera/...
      Download/...
      tencent/MicroMsg/<hash>/cache/...
      ...
    data/                     # /data（含 /data/data、/data/local/tmp）
      data/<pkg>/cache/...
      local/tmp/...
    system/                   # /system 等其它挂载点（只用于越权反向测试）
  fixtures/
    config.json               # --config
    packages.json             # --mock-packages
    caps.json                 # --mock-caps
    device.json               # --mock-device
    review.json               # plan --review 用的 AI 复核结论
  state/                      # --state（doctor/ scan/ analysis/ plan/ ...）
```

## 设备路径 → 主机路径映射

| 设备路径 | 主机路径（`--mock-root` 下） |
|---|---|
| `/sdcard/...`、`/storage/emulated/0/...`、`/storage/self/primary/...` | `sdcard/...` |
| `/data/...` | `data/...` |
| `/system/...` 等 | `<第一段路径>/...` |

## 四类 JSON fixture

### config.json（极小阈值，让测试在秒级完成）

```json
{
  "thresholds": {
    "min_duplicate_bytes": 100,
    "large_file_bytes": 1000000,
    "old_file_days": 1, "apk_old_days": 1, "archive_old_days": 1,
    "temp_old_days": 1, "log_old_days": 1, "screenshot_old_days": 1,
    "media_old_days": 1, "trash_old_days": 1, "empty_dir_old_days": 1,
    "download_old_days": 1, "chat_cache_old_days": 1, "browser_cache_old_days": 1,
    "min_archive_bytes": 100, "min_download_bytes": 100, "min_media_bytes": 100,
    "large_cache_caution_bytes": 10000000
  },
  "scan": {"max_files": 100000, "max_hash_files": 500, "max_hash_total_bytes": 200000000,
           "max_candidates": 500, "max_duplicate_groups": 100, "deadline_seconds": 120},
  "report": {"sample_paths": 20}
}
```

文件年龄统一用 `os.utime` 设成 `NOW - 5 天`（阈值 1 天），
避免落在判定边界上；需要"新文件"时用 `age_days=0`。

### packages.json（应用安装状态）

```json
{"installed": ["com.installed.app"], "uninstalled": ["com.gone.app"],
 "system": ["android"], "available": true}
```

- `installed`：`pm list packages` 中的包；
- `uninstalled`：`pm list packages -u` 有记录但未安装 → 卸载残留依据；
- `available: false`：模拟 `pm` 不可用（能力降级）。

### caps.json（权限 / 能力）

```json
{
  "privilege": "shell",
  "inaccessible": ["/sdcard/Android/data"],
  "denied_ops": ["move:/sdcard/Download/blocked.apk", "delete:/sdcard/..."],
  "commands": {"find": true, "stat": true, "xargs": true, "sha256sum": true,
               "pm": true, "df": true, "realpath": true, "rm": true, "mv": true, "rmdir": true},
  "df": {"total_bytes": 128000000000, "used_bytes": 90000000000, "available_bytes": 38000000000}
}
```

- `privilege`：`shell` / `root` / `su`，影响 doctor 能力等级与是否扫描 `/data/data` cache；
- `inaccessible`：这些设备路径（含子路径）不可读 → doctor 降级、scan 记录 skipped、analyze 报告 limitations；
- `denied_ops`：模拟执行期权限失败，格式 `<op>:<设备路径>`，op ∈ {move, delete, rmdir, *}；
- `commands`：缺省为全部 true；置 false 模拟工具缺失（如无 sha256sum → 不做重复文件检测）。

### device.json（设备信息）

```json
{"serial": "MOCK-ADC-0001", "sdk": 34, "release": "14",
 "manufacturer": "MockCorp", "model": "MockPhone", "build_type": "userdebug",
 "abi": "arm64-v8a", "product": "mock_product", "fingerprint": "mock/..."}
```

## 生成 fixture 的代码方式

```python
def test_example(env):
    env.phone.file("/sdcard/Android/data/com.installed.app/cache/a.bin", size=300)
    env.phone.file("/sdcard/DCIM/Camera/old.jpg", content="same-bytes", age_days=5)
    env.phone.dir("/sdcard/Misc/EmptyDir")
    env.ok("doctor"); env.ok("scan"); env.ok("analyze")
    assert env.require_item("app_cache")["file_count"] == 1
```

`env.phone.file(...)` 默认内容由 `sha256(设备路径)` 派生（同大小不同文件也不会误判重复）；
需要构造重复文件时显式传 `content="..."` 让两份内容逐字节相同。

## 只读断言

```python
before = env.snapshot()          # {相对路径: {kind,size,mtime_ns,sha256}}
env.ok("doctor"); env.ok("scan"); env.ok("analyze")
env.assert_unchanged(before)     # doctor/scan/analyze 必须只读
```

## 反向（安全）fixture 模式

| 场景 | 构造方式 |
|---|---|
| 无令牌 / 错误令牌 | 不运行 select，或把 `--token` 写成 `ADC-WRONG` |
| 篡改 plan / selection | 直接改 `state/plan/plan.json`、`state/selection/selection.json` |
| 路径穿越 | 在 `state/analysis/analysis.db` 的 `candidate_files` 里注入 `/sdcard/../../etc/passwd` |
| 保护路径 | 注入 `.../cache/.nomedia` / `/system/bin/x` 到 candidate_files |
| 计划后文件变化 | 在 execute 前 touch / 改内容 / 删文件 / 删目标目录 / 重建同名文件 |
| 权限失败 | `env.set_caps(denied_ops=["move:<path>"])` |
| 扫描权限不足 | `env.set_caps(inaccessible=["/sdcard/Android/data"])` |
| 符号链接 | Windows 需开发者模式；`os.symlink` 抛 OSError 时测试自动 skip |

## 清理

所有产物都在 pytest 的 `tmp_path` 内，测试结束自动删除；不写仓库、不访问网络、不需要 adb。
