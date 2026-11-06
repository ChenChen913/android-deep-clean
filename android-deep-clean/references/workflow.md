# 四种模式的完整工作流

所有命令中的 $Skill 指向技能目录，$State 指向可写状态目录。通用参数写在子命令之后。

本文件以电脑端 ADB 为例；手机端只需把 --transport adb --serial <序列号> 换成 --transport local（Termux）或 --transport rish --rish-path rish，其余规则/命令不变。电脑端完整说明见 usb-adb-workflow.md，架构见 dual-path-architecture.md。

## 通用前置

~~~powershell
$Skill = "$env:USERPROFILE\.agents\skills\android-deep-clean"
$State = ".adc-state"
python "$Skill\scripts\adc.py" doctor --state $State --transport adb
~~~

doctor 后先判断能力：
- blocked：停止，提示 ADB/授权问题；
- limited：明确告诉用户 Android/data 或内部缓存不可见；
- standard/full：继续。

## 模式 A：全盘体检（只读）

目标：给用户一份“手机里到底有什么”的报告，不做删除。

~~~powershell
python "$Skill\scripts\adc.py" scan --state $State --transport adb
python "$Skill\scripts\adc.py" analyze --state $State --transport adb
~~~

然后：
1. 读 analysis-summary.json 和 report-before.md；
2. 向用户总结：空间占用、候选类别、最大项目、风险、权限限制；
3. 明确“未删除任何文件”；
4. 询问是否要继续深度处理。用户只要报告就到此为止。

## 模式 B：深度清理（核心）

1. 体检 + 扫描 + 分析（同上）。
2. AI 复核：
   - 读 review_items；
   - inspect 大/风险/模糊项目；
   - 写 review.json；
   - 只把有证据的标 trash。
3. 计划：
   ~~~powershell
   python "$Skill\scripts\adc.py" plan --state $State --transport adb --review "$State\analysis\review.json"
   ~~~
4. 展示计划。建议按风险分组：
   - SAFE 缓存/临时/缩略图：可在用户确认后批量；
   - CAUTION：逐项确认；
   - DANGEROUS：明确不会动。
5. 用户给范围后 select：
   ~~~powershell
   python "$Skill\scripts\adc.py" select --state $State --transport adb --all-safe
   # 或 --ids / --categories / --app-kinds
   ~~~
6. 展示 selection 清单 + 令牌；问“是否确认执行隔离清理？”
7. 用户明确同意后：
   ~~~powershell
   python "$Skill\scripts\adc.py" execute --state $State --transport adb --token <令牌>
   ~~~
8. verify；报告；询问 restore / purge。
9. 可选 recheck。

用户拒绝的对话：
- “好的，不做任何删除。以下报告已保存：… 需要时再叫我。”禁止继续调用 execute。

## 模式 C：专项清理

先完整 scan/analyze，再按目标筛选。示例：

- 微信/QQ 缓存：select --categories chat_cache（需复核）
- 卸载残留：select --categories residue（需复核）
- 旧 APK：select --categories apk
- 重复照片：select --categories duplicates
- 旧大文件：select --categories large_old,media_old
- 浏览器缓存：select --app-kinds browser
- 指定目录：scan --roots /sdcard/Download 后 analyze

专项模式也必须：doctor → scan → analyze → review（CAUTION）→ plan → select → 用户确认 → execute → verify。不能因为“只清一类”就跳过令牌。

## 模式 D：清理复查

~~~powershell
python "$Skill\scripts\adc.py" recheck --state $State --transport adb
~~~

1. 读 report-recheck.md：新增/仍存在/已消失；
2. 对新增候选重新 analyze/review/plan/select/execute；
3. 旧令牌不得复用；
4. 说明“复查也是只读，不会自动删除”。

## 状态机与失败恢复

~~~text
doctor → scan → analyze → [review] → plan → select → (用户确认) → execute → verify
                                                        ↘ 用户拒绝：停止
execute 失败 → 查看 execution.json/report → 重新 scan/analyze/plan
隔离后后悔 → restore
确认无用 → quarantine purge（不可恢复）
~~~

- 任何一步失败：不要跳过检查继续清理；先读报告。
- 设备断开：已执行部分由 journal 记录；重连后 verify。
- 状态目录丢失：不能凭记忆 restore；重新扫描/计划。
- plan 后文件变化：execute 会跳过；重新 scan/plan。

## 用户对话模板

开始：
“我先只读检查手机存储和权限，不会删除任何东西。检查后给你一份带路径、理由和风险等级的报告，再由你决定。”

权限不足：
“当前只能读取共享存储，Android/data 或应用内部缓存不可见。因此报告会缺少这部分，我不会假装已经全盘扫描。”

选择：
“以下 N 组 / M 个文件 / X GB 可以处理。SAFE 类可批量；CAUTION 类我逐项说明。你希望只清 SAFE，还是也看 CAUTION？”

执行：
“你确认后我会先移动到隔离区，可恢复；现在存储不会立刻释放，验证后再决定是否永久删除。”

拒绝：
“收到，不执行任何删除。报告保存在 …，需要时再继续。”

完成：
“已隔离 N 个文件 / X MB；失败 M 个；实际释放 0（隔离模式）。验证结果：… 要恢复还是永久释放？”
