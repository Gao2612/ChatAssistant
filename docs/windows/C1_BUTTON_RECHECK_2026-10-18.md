# C1 应用内按钮复核（任务消息 #32）

2026-10-18；分支 `feat/windows-lite`，起点 HEAD `f3f277d`，源码 APP_VERSION `0.5.20`。本轮未提交、未推送、未构建或发布。保留原有 C8-FIX 工作区改动，未读取真实凭据。

## 结论与边界

**C1 尚不能签收。** 本轮真实 UI 工具前置检查返回 `no_wechat_window`，未启动按钮查找，未操作、恢复或发送任何微信消息。证据为 `windows/lite/test-output/c1/c1_ui_acceptance_20261018-201346.json` 与本机 `_reports/c1_ui_current.log`。当前执行环境未枚举到微信，不据此判断用户桌面是否运行微信。

旧交接记录的 UIA InvokePattern 成功属于旧源码与旧运行环境证据，不能替代本轮真实验收。新增 Qt 回归使用离屏控件、真实按钮事件与真实后台线程，但窗口、OCR 和输入后端为合成夹具，**不是隔离桌面中的真实微信切换验收**。

## 本轮定位及修复

`find_wechat_conversation.work()` 原先在 `_finish_wechat_action()` 之前发出跨线程完成信号。UI 可以先处理信号，清除 busy 并恢复采集/停靠，此时后台线程尚未完成窗口还原。新增测试用还原阶段的受控延迟和主线程事件处理稳定观察到 `resumed → restored`，成功与标题不匹配两个路径均失败。

修复仅将 find 完成通知移到还原逻辑之后，先保存结果/错误，再在 finally 中还原窗口并通知 UI，保证 `restored → resumed`。没有放宽标题匹配，没有新增发送路径。这个恢复竞态是本轮已证明的问题；**尚未证明它就是旧 `search_not_found/result_not_found` 的全部根因**。

新增 `windows/lite/tests/test_find_button_lifecycle.py`：通过 `QTest.mouseClick` 点击应用实际按钮，经现有信号连接启动真实线程；在查找暂停点观察 `wechat_busy=True` 与“正在查找微信对话”提示（测试中的 find_started=true 判据）。使用真实 finder 和合成 OCR：目标标题精确相等时成功，近似标题多一个字符时返回 `title_mismatch`，两条路径均验证窗口还原先于采集恢复、busy 清除、无发送调用、无 Enter。

## 验证

工作目录 `windows/lite`，Python 为 `G:\codex\wechat-lite-venv\Scripts\python.exe`。

- 先红：`-m unittest tests.test_find_button_lifecycle -v`，2 项失败，均因恢复顺序颠倒；日志 `_reports/c1_button_red.log`。
- 修复后：`-m unittest tests.test_find_button_lifecycle tests.test_find_pause_capture tests.test_wechat_find tests.test_wechat_find_foreground tests.test_action_dock_ownership -v`，39 项通过，39.421s；日志 `_reports/c1_targeted_current.log`。覆盖失败原因映射、ESC/前台保护、标题严格匹配、有界重试、采集恢复及不发送。
- 修改前全量：`-m unittest discover -s tests -t . -q`，891 项，10 失败、39 错误、39 跳过，124.048s；日志 `_reports/c1_full_before.log`。这是本轮 C1 修改前、保留 C8-FIX 改动的同环境工作区基线，不是 a7c5ea0/C8-FIX 的历史基线。
- 修改后同命令全量：893 项，10 失败、40 错误、39 跳过，132.206s，exit 1；日志 `_reports/c1_full_after.log`。新增 C1 两项均通过，**全量未通过**。
- 逐测试名对比：原有 10 失败/39 错误全部仍在，没有消失项；新增错误仅 `tests.test_single_instance.CrossProcessSingleInstanceTests.test_lock_holder_without_instance_info_fails_closed`。该测试在 tearDown 删除临时 `profile/instance.lock` 时遇到 WinError 32（文件占用），不是 C1 断言失败；单独复跑该项 1 项通过、0.480s、exit 0，日志 `_reports/c1_lock_recheck.log`。不能据单次复跑排除所有时序关联，未改动无关单实例实现/测试。完整比较 `_reports/c1_full_comparison.json`。
- 基线多个错误/失败栈含 DPAPI/Windows 密钥库不可用；这里仅认定它们在本轮 C1 修改前已经出现，没有将全部问题泛称为环境错误，也没有给 C8-FIX 作历史基线归因。
- `git diff --check` 通过。

## 待验收步骤

在能够枚举到可见微信主窗口的桌面会话，用隔离 LOCALAPPDATA 启动当前源码实例，运行 `tools/c1_ui_acceptance.py`。必须记录当前版本/源码/PID/profile、find_started=true、finder 严格标题验证成功、窗口与采集恢复状态。不得操作真实 profile 或发送消息。还需覆盖原生鼠标点击、实际搜索失败恢复与真实桌面焦点干扰。当前未把合成测试的“成功”当作真实联系人已切换。

## Codex 复验补充（20:31）

- 本轮修复后定向复跑：`test_find_button_lifecycle` + `test_find_pause_capture` + `test_wechat_find` + `test_wechat_find_foreground` + `test_action_dock_ownership`，**39 项通过**（日志 `windows/lite/test-output/c1/reopen-20261018-final-targeted.log`）。新增按钮生命周期用例先红后绿，严格标题拒绝、真实 Qt 按钮/后台线程路径、恢复顺序、不 Enter/不发送均覆盖。
- 同命令全量当前工作区：**906 项、42 skipped、1 error**；唯一错误仍是 `test_lock_holder_without_instance_info_fails_closed` 在 teardown 删除锁文件 WinError 32。隔离解包 `a7c5ea0` 基线运行相同命令：**889 项、37 skipped、同一测试名同一错误**。当前单测独立复跑有一次通过、一次失败；基线独立复跑失败，显示为不稳定的旧锁测试问题，根因未查明。未将其归咎于 C1，也未更改单实例测试。
- 全量日志：`windows/lite/test-output/c1/reopen-20261018-final-full.log`、`baseline-a7c5-full.log`。当前源文件/文档 `git diff --check` 通过。以上不改变 C1 的真实桌面切换未复验状态；按互审消息 #38/#46，等待 C13 与候选版本后再操作用户微信。

## 当前源码 0.5.21 隔离桌面复验（20:47）

- 用户选择先关闭现有助手。已正常关闭可见悬浮窗与主窗；应用主窗已不再枚举，遗留无界面进程未强杀。验收脚本使用独立 `LOCALAPPDATA`，设置 `auto_analysis=false`、`auto_send_allowed=false`，只启动/清理自己的进程树；未保存截图，未发送消息。
- 当前 UI 确认自动连接到可见微信 (`hwnd=394618`, rect `[350,138,1475,913]`)，0.5.21 源码实例找到按钮并输入测试目标，点击后 `find_started=true` 且 `find_finished=true`。
- **未通过**：最终状态 `not_foreground`，`switched=false`、`title_seen=false`；微信窗口结束时仍可见且 rect 与开始完全一致。安全元数据报告：`windows/lite/test-output/c1/c1_ui_acceptance_20261018-204758.json`。该报告未写联系人名称/聊天文本/截图。
- 已向 DeepSeek 发会审更正 #82/#84：定位并修复真实 UI 的前台丢失问题，诊断只保留失败阶段/枚举原因，不记录任何聊天文本；禁止提交/推送。完成后再重跑同一隔离验收。当前只能确认 UI 触发与结果回调，不可把 C1 标为完成。
- 最终全量回归：**909 项通过、42 项 skipped、exit 0**；`git diff --check` 通过。该结果覆盖工作区现有代码；真实 UI 点击仍以本节的失败结果为准。
- DeepSeek Harness 本轮两次创建本地提交 `a4dfc79` 与 `551db38`，违反“不提交”约束；两次均以 `git reset --mixed f3f277d` 回退，改动内容保留在工作区。当前不含新提交、推送或发布。
- 独立代码审查（未改代码/未操作微信）：`not_foreground` 仅可能来自 `wechat_find.py` 的第 291/294/323 行检查；本次报告不足以定位哪一处。审查认为 OCR 期间前台丢失最可能，其次是微信所属弹窗与主窗口 HWND 不同。结束时前台恢复不是反证，因为失败清理会重新激活微信。下一步先记录失败阶段、当时前台 HWND/PID/status 与 owner（纯元数据），再基于证据修复；不应泛化同进程窗口或对所有失败重试。审查还指出 `_escape()` 发送全局 ESC 前需验证目标仍在前台。建议合成测试覆盖 OCR 后失焦、搜索点击后 HWND 变化、结果点击前失焦，以及失败时不粘贴/不点结果/不 Enter/不向其他窗口发送 ESC。审查结论已通过会审 #86 交给 DeepSeek。
