# C1 接管证据：App 内“自动查找并切换对话”按钮路径（DeepSeek windows 接管）

- 任务：Team `task-20` / 看板 `TAKE-C1`；对应 Codex `C1`（14:51 前由 DeepSeek 临时接管，Codex 恢复后复核）
- 仓库：`G:\codex\ChatAssistant`，分支 `feat/windows-lite`，基线 **`a3b2994`**（Codex 0.5.8 未提交改动由 Lead 审查提交）
- 规范版本：**源码树 0.5.8（HEAD `a3b2994`）**；已安装 0.5.7 **不用于真实验收、不点击、不 kill**
- 真实窗口硬性前置：任何真实操作前检查 `discovery.window_status(hwnd) == "visible"`；非 visible 立即停止并报告，不自行 minimize/hide/move/restore 用户微信窗口
- 执行日期：2026-10-18；证据目录 `windows/lite/test-output/c1/`（仅指标，不含聊天正文/截图）
- 本任务不发送任何微信消息、不改真实 profile、不 commit/push

## 0. 结论状态（实时更新）

- [x] 只读分析：Codex resume 报告、0.5.7→0.5.8 diff、`wechat_find.py`、`ui.py` 动作路径、进程 DPI 实测
- [x] H3 修复：发送前标题校验改为规范化精确相等（拒绝“田子夜2”子串）
- [x] H4 修复：`find_wechat_conversation`/`send_reply_to_wechat` 的 work() 全包 try/finally（prepare 异常也恢复 busy/capture）
- [x] Codex “结果点击后不再按 Enter” 的 UI 路径回归断言
- [x] C1 失败路径 ESC 安全退出：UI 层对任何失败查找补发 ESC（仅前台、只一次；ESC 后 visible 检查/恢复，P2-10）
- [x] C1 候选修复（H1）：查询前等待“非空白/前台可采集”就绪（不移动窗口）+ 瞬态失败（search/result/capture）最多重试 1 次；可在测试/tools 中关闭以做 legacy A/B
- [x] 真实窗口 4 次 UI 运行（隔离 profile + 源码树 0.5.8/54436b6）：均未切换 → **失败分类为测试驱动层坐标/前台（按钮点击落到 QQ，find 未启动），不是产品 find 失败证据**；证据 JSON 已落盘，运行内 postflight 均 visible+rect 不变
- [x] **最小诊断（Lead 批准，真实窗口 1 次）通过**：隔离 0.5.8 源码实例 + `ValuePattern` 填目标 + `InvokePattern` 点“自动查找并切换对话” → `find_started=true`、`category=success/switched=true`、`find_duration=5.2s`；postflight visible+rect 不变、无发送；证据 `test-output/c1/c1_ui_acceptance_20261018-105003.json`
- [ ] native 鼠标点击路径未在本机验收（本次成功由 Qt 可访问性模式触发；native 点击在本机被 QQ/前台窗口抢占，属驱动层限制）
- [ ] A/B 差分：真实窗口 A/B 未完成（脚本崩溃，已离线加固 faulthandler/dry/增量落盘/`os._exit`；dry 通过），缺口
- [x] **Lead 裁定 C1 completed（带限定）**：产品 UI 动作路径真实成功；“native 鼠标驱动路径未完成、属测试器限制；A/B 差分未产出”作为明确缺口记录，不写成 native 鼠标已验证。

## 0.1 完成限定（最终，必须与“通过”一起引用）

- **已验通过**：源码树 0.5.8（`54436b6`）+ 隔离 `LOCALAPPDATA` 的独立 App 实例；**App 内“自动查找并切换对话”按钮槽**由 Qt 可访问性 `InvokePattern` 等价触发 → `find_started=true`、`status_category=success`、`switched=true`、`find_duration=5.2s`；无任何消息发送；同一 run 内 postflight WeChat `visible=true` 且 rect 不变。证据：`windows/lite/test-output/c1/c1_ui_acceptance_20261018-105003.json`、`_reports/c1_accept_final3.log`。
- **未完成/不写通过**：
  - **native 鼠标点击按钮路径未在本机验收**（本机 QQ 频繁前台/覆盖 + Qt UIA 坐标空间导致 native 点击被抢占，属测试驱动限制；产品槽本身已验证）。
  - **真实窗口 direct vs App 路径 A/B 差分未产出**（A/B 脚本真实运行崩溃；已离线加固但无真实结果；dry offscreen 通过）。
  - 停靠模式（dock_wechat=true）按钮路径不在本机真实验收范围（受“不得移动用户微信窗口”硬性规则约束）。
- 真实窗口 4 次失败（`c1_ui_acceptance_20261018-1034*/1036*/1037*/1039*.json`）均为**驱动层**（点击未投递、find 未启动），不构成产品 `find_conversation` 失败证据；与最小诊断成功不矛盾。
- 最终离线回归：`tests.test_action_dock_ownership tests.test_find_pause_capture tests.test_wechat_find tests.test_wechat_find_foreground` → **Ran 37 tests / OK**（`_reports/c1_final_tests.log`）。

## 1. 只读分析与已确认差异

### 1.1 直接调用与 App 内路径的确定差异（代码级）

| 维度 | 直接调用（历史证据脚本） | App 内按钮路径 |
| --- | --- | --- |
| 进程 | 独立脚本进程（`test-output/find_conversation_real.py` 等）；会先 `app_foreground()` 再调用 | 源码/安装 ChatAssistantLite 进程 |
| 线程 | 脚本主线程 | `find_wechat_conversation()` 里 `threading.Thread(daemon=True)` 后台线程 |
| 采集 | App 若在运行则其 WGC 采集继续运行（直接调用不 stop） | 动作前 `stop_capture()`：终止采集子进程 + `undock()`（镜像模式下 detach 无窗口移动；停靠模式下会恢复几何） |
| 时序 | 调用前无 WGC 会话销毁 | 采集子进程被 `join(0.25)/terminate()/join(0.5)` 后立刻启动新 WGC 会话 |
| UI 定时器 | 无 | `poll` 200ms、`dock_timer` 450ms、`scan` 2.5s 仍在事件循环（0.5.8 已加 `wechat_busy` 防重停靠） |
| DPI | 实测：导入 `wechat_find` 后 `SetProcessDpiAwarenessContext(-4)` → 进程/线程 DPI 上下文 = Per-Monitor V2（awareness=2） | 实测 Qt 6 QApplication 创建即 PMv2（awareness=2）；worker 线程 import `wechat_find` 后仍为 2 → **DPI 上下文与直接调用一致，排除 H2** |

已排除/证据：
- 进程 DPI 差异：两种进程均为 Per-Monitor V2（本机实测 `GetProcessDpiAwareness=2`、`GetThreadDpiAwarenessContext` 解析 awareness=2）。
- `wechat_find` 搜索/结果定位、2 次交互重试、4 帧等待、严格标题校验、去 Enter：当前 `a3b2994` 代码已确认。
- Codex `resume_windows` 报告的结论：停靠重接管已加 `wechat_busy` 防重入；但 **A3 根因仍未证明**，需真实窗口差分。

### 1.2 当前候选根因（A/B 待验证）

- **H1（首要）**：动作前 `stop_capture()` 终止 WGC 采集子进程后立即启动新 WGC 会话，第一帧可能是黑帧/旧帧；而 `wechat_find.find_conversation` 只在“搜索框已找到”之后才做交互重试，**首次搜索框 OCR 没有重试** → 单个坏首帧就返回 `search_not_found`。预期 A/B 判据：App 路径/`direct_after_stop_capture` 的 capture 首帧 `std`/`dark_fraction` 异常且 OCR `rows=0/search_row=false`；`direct_main`（不停采集）正常。
- **H3（已修）**：发送前标题子串匹配（已改精确相等）。
- **H4（已修）**：prepare 异常路径卡死 busy/采集（已包 try/finally）。
- 其他待排除：WGC 首帧等待时长、前台在动作中被 App/其他进程抢回（A/B 记录 50ms 前台采样序列）。

## 2. 已落代码（本任务）

- `liteapp/ui.py`
  - 新增 `normalize_title_for_match()` / `titles_match()`：仅忽略空白与常见标点，**规范化后精确相等**；`send_reply_to_wechat` 用 App 自身 OCR（内存帧）读当前标题并 exact 校验，失败先取消、不进入发送。
  - `find_wechat_conversation` / `send_reply_to_wechat`：整个 `work()` 包 `try/finally`，`_prepare_wechat_for_action` 抛异常时仍经 finally 恢复 busy/采集；`finally` 的 `_finish_wechat_action` 再包一层 try/except，避免恢复失败掩盖结果。
  - C1 失败路径 ESC：`_escape_wechat_search()`；任何查找失败（含 search_not_found/result_not_found 这些 `wechat_find` 内部不发 ESC 的路径）由 UI 补发一次 ESC，保证安全退出搜索层，ESC 不会发送消息。
  - C1 候选修复（H1）：`_wait_wechat_render_ready()` 在查询前等待**非空白 WGC 首帧**（`std>5`，最多 `_wechat_find_ready_wait=2.5s`；只读采集，不移动/最小化/恢复窗口）；`_run_find_with_recovery()` 对 `search_not_found/result_not_found/capture_failed` 最多重试 1 次（重试前 ESC + 再等就绪帧），非瞬态失败不重试；两者可通过实例属性关闭以做 legacy A/B。
  - 说明：`wechat_find`（C4 所有）初次搜索框 OCR 没有重试，因此单个坏首帧会直接 `search_not_found` — 这是 H1 假设的代码依据。
- `tests/test_find_pause_capture.py`：+17 项回归（共 **19 项**）
  - H4：find/send 的 prepare 异常 → `wechat_busy=False`、capture 恢复（`connect_item`）、无查找/发送调用。
  - H3：标题精确匹配单元（“田子夜2”/x 前缀/前缀缺失/空均拒绝；空白/标点容忍）；发送前子串标题必须取消且不触达发送；精确标题允许发送。
  - 去 Enter：UI 路径跑真实 `find_conversation`（FakeBackend/FakeReader）成功切换，断言 keys 中无 0x0D、未调用 `send_message`。
  - ESC / P2-10：失败查找补发 ESC 且恢复 busy/capture；成功查找不发 ESC；**非前台不按 ESC**（避免打到别的窗口）；ESC 后仍 visible 不再操作、若被自己 ESC 变成 hidden 则 `SW_SHOWNOACTIVATE` 恢复；重试 + 外层最终失败**只 ESC 一次**（去重）。
  - 修复路径：空白首帧被跳过、等待超时 fail-open 且不调用 restore、瞬态失败重试一次后成功、非瞬态失败不重试、两次瞬态失败仍有界并映射正确提示。
- `tools/c1_find_ab_diag.py`：App 内路径 vs 直接调用的 A/B 差分（隔离 LOCALAPPDATA；仅记录 frame 统计/OCR 行数/search 命中/坐标/前台角色序列/DPI/window rect/reason/steps；无截图/正文）。变体：`direct_main`、`ui_path_legacy`（等待/重试关闭）、`ui_path`（修复路径）、`ui_path_without_stop_capture`（WGC 竞争对照）、`direct_thread`、`direct_after_stop_capture`、`direct_after_stop_settle`、`ui_path_with_settle`。
- `tools/c1_ui_acceptance.py`：隔离 LOCALAPPDATA 启动**源码树 0.5.8 独立实例**，真实鼠标/键盘走“输入目标→点击自动查找”，只记录 PID/version/commit/profile/hwnd/布尔状态类别；不点击发送；**只终止本脚本自己 spawn 的 PID 树**（Toolhelp 父链校验窗口 owner 属于 child 后代；其它 0.5.8 实例只扫描记录、绝不 kill；用户 0.5.7 PID 5652/23020/34388 明确排除）。新增 `find_started/find_finished/find_duration/status_category` 与截图/坐标修正点击（`click_control_in_window`/`click_button_candidates`），失败时记录 `click_not_delivered` 而不是误判产品失败。

## 3. 定向测试真实输出

完整日志（本机 `_reports/`，git 忽略）：
- `_reports/c1_tests_run3.log`：`python -m unittest tests.test_find_pause_capture -v` → `Ran 10 tests ... OK`（H3/H4/ESC/去 Enter）
- `_reports/c1_tests_run4.log`：同一命令（含就绪/重试用例）→ `Ran 15 tests ... OK`
- `_reports/c1_targeted_run2.log`：
  `python -m unittest tests.test_find_pause_capture tests.test_auto_ui tests.test_action_dock_ownership tests.test_wechat_find tests.test_wechat_actions tests.test_ui tests.test_remote_reading_ui -q`
  → **Ran 78 tests in 8.710s / OK**（exit 0）

## 4. 真实窗口 A/B 与 UI 验收（10:30–10:50 窗口，C3 让出；C4 已提交 54436b6）

### 4.0 前置阻断与恢复（重要事实）

- 10:29:50 第一次 preflight：`hwnd 394618` 满足 `IsWindowVisible=true / IsIconic=false`，但 `GetWindowRect=[-24000,104,-22761,898]`（**屏幕外**），`discovery.window_status(394618)` 返回 `"hidden"`；另一 Weixin 窗口 2626248 为 hidden。按 Lead 硬性规则**立即停止，未移动/恢复/最小化/隐藏**任何窗口；此状态非本任务脚本造成（此前从未执行真实窗口脚本）。
- 10:30:34 Lead 恢复后 preflight：`discovery.window_status(394618)=="visible"`，rect [144,104,1383,898]，与 Lead 报告一致。随后才开始 C1 真实操作。
- 旧 0.5.7 实例 PID 5652/23020/34388 在运行；本次不点击、不 kill、不交互。
- 源码树版本：`0.5.8`，commit **`54436b6`**（C4 修复提交），隔离 `LOCALAPPDATA=%TEMP%\c1-harness-profile-*`；无真实 profile 修改。

### 4.1 前置记录

- 版本/commit：`0.5.8` / `54436b6`（源码树，含 C4 finder 提交）；源码 App 窗口 PID 29636/34788/16212/26552/26524，hwnd 每次不同（隔离实例），profile=`%TEMP%\c1-accept-profile-*\ChatAssistantLite`；旧 0.5.7 实例（窗口 hwnd 29426838, PID 5652 等）未交互。
- 每次运行前 preflight：WeChat `status=visible`；**运行内** postflight：`visible=true`、rect 不变（4 份 JSON 一致）。
- **窗口几何在 10:30 后曾被外部改变**（非本任务脚本）：10:30:34 [144,104,1383,898]（Lead 恢复）→ 10:34:54~10:41:58 [180,130,1729,1122] → 10:42:23 [115,83,1106,718]。C1 脚本自身从未移动/最小化/恢复窗口。

### 4.2 4 次真实 UI 运行（均未切换）失败分类

证据：`test-output/c1/c1_ui_acceptance_20261018-1034{54,610}.json`、`...-103716.json`、`...-103948.json`；日志 `_reports/c1_accept_run{2,3,4,5,6}.log`。

| # | 文件时间 | 到达阶段 | 失败类别 | 事实 |
| --- | --- | --- | --- | --- |
| 1 | 10:34:54 | 连接成功 → settings 打不开 | `settings_page_not_found` | App 被 WeChat/QQ 覆盖，App 非前台时 native 点击无效；未点击查找按钮 |
| 2 | 10:36:10 | 连接成功 → settings 打不开 | `settings_page_not_found` | 同上（导航点击/回退都不生效） |
| 3 | 10:37:16 | settings 打开、按钮找到、目标已输入、**点击按钮** | `other_failure`（脚本误判） | 点击后 0.45s 就命中全窗口通用词“失败/无法”，脚本立即结束；**未等 find 结果** |
| 4 | 10:39:48 | 同上 | `unknown`（等待 76s） | `find_clicked` 后前台=**QQ(1574036, pid 21056)**；76s 内无任何 find 状态词；`find` **未真正启动** |

补充（最小诊断，10:50:03，**成功**）：同一套隔离源码树 0.5.8 + 隔离 profile，改用 Qt 可访问性模式驱动：
- 目标输入用 `ValuePattern.SetValue`（`target_entered_matches=true`）；按钮用 `InvokePattern.Invoke`（`clicked=true`）→ `find_started=true` → `after_find: category=success, switched=true, find_duration=5.2s`；postflight `visible=true`、同一 run 内 rect 不变；无发送。
- 证据：`test-output/c1/c1_ui_acceptance_20261018-105003.json`（`status_category=success/switched=true/find_started=true`）、`_reports/c1_accept_final3.log`。
- 说明：`title_seen=false` 因页面已切走后再读标签；成功判据为 App notice“已切换到目标对话”+ finder 内部严格 header 标题验证。

直接原因（驱动层，非产品 find 失败证据）：
- `uiautomation` 读取 Qt 控件的 `BoundingRectangle` 坐标空间不可靠 + 用户机 QQ 窗口频繁前台/覆盖；旧代码对“查找按钮/目标输入框”用原始 UIA rect 的 native click 会落到 QQ 上。
- 证据链：run4/5 stage 顺序 `app_activated(fg=App) → settings_open(ok) → find_button_found → target_entered → find_clicked`，但 `find_clicked` 后 fg=1574036(QQ)，且始终未出现“正在查找微信对话/已切换/失败”等动作状态；`find_started` 未记录（旧脚本无此字段）。
- run3 的 `other_failure` 是脚本把窗口内任意“失败/无法”文本当动作结果导致的**误分类**，不代表 find 返回；后续已改为只认 `_wechat_reason_text` 的具体文案。
- **改用 ValuePattern/InvokePattern 后一次成功**，证明产品 find 路径正常、失败在驱动点击投递层；native 鼠标路径仍未经本机验收（驱动层证据：App 窗口被 QQ 覆盖时 native 点击不可靠）。

### 4.3 A/B 差分（未完成，缺口 + 离线修复完成）

- `tools/c1_find_ab_diag.py` 两次真实启动均在 20–60s 内异常退出（无 JSON、日志为空）——**真实窗口 A/B 证据未产出**，不写成已验。
- 离线修复（已完成，待真实窗口复跑）：
  - `faulthandler.enable()`：硬崩会留下栈；
  - `C1_DRY=1`（offscreen、不操作真实微信/窗口）+ 分阶段 `STAGE`、逐变体 `run_start/run_end/run_exception`；
  - **每个变体后立即原子写 JSON**（`_write_report`），并写入 finally 早于 Qt teardown；结束用 `os._exit` 绕过疑似解释器/Qt 关闭崩溃；
  - `C1_VARIANTS=direct_main,ui_path_legacy` 可只跑指定变体以适配 3 分钟窗口；
  - dry 验证通过：`imports_ok → harness_created → bind_wechat(bound=True) → dry_mode_complete`，证据 `test-output/c1/c1_find_ab_20261018-104734.json`；真实-Qt + MainWindow + Reader 同进程探针也通过（排除“Qt+OCR 必崩”假说），崩溃点仍待真实窗口 faulthandler 日志定位。
- 历史对照仍有效：`docs/windows/WINDOWS_LITE_ACCEPTANCE_MATRIX.md` 记录直接代码调用 `find_conversation` ok=true；C4 提交 `54436b6` 已把 finder 改为前台窗口屏幕采集并带 foreground guards（`tests.test_wechat_find` 16 项 OK），这是 A3 的可信根因方向（旧 WGC 窗口采集看不到搜索下拉 + 无前台校验）。

### 4.4 已判定的修复落地（离线，不依赖真实窗口）

- H3 精确标题、H4 work try/finally、失败 ESC 安全（仅前台、只一次、ESC 后 visible 检查/恢复）、`not_foreground` 映射、查询前就绪等待 + 瞬态失败一次重试：均已写入 `liteapp/ui.py` 并有 `tests/test_find_pause_capture.py` 19 项回归。
- 定向测试：`tests.test_find_pause_capture` **Ran 19 OK**；更广 UI 子集此前 78/92 项 OK（C4 提交后 `tests.test_action_dock_ownership` 中 1 项因 C4 finder 前台校验未更新 FakeBackend 而红，属 C4 测试作用域，非本任务改动）。

## 5. 限制与风险

- **孤儿实例清理（10:42:23 误杀 C3 实例后的修正；重要教训）**：C1 早先版本按“窗口标题含 0.5.8 + owner 镜像是 python.exe”清理遗留实例，误杀了 takeover-wintest 的 C3 隔离实例 PID 34372（其 command line/profile 属 C3）。自那以后 C1 工具规则改为：
  1) **只 kill 本脚本自己 `subprocess.Popen` 的 `child.pid` 进程树**；且窗口 owner 必须通过 Toolhelp32 父链校验为 `child.pid` 的后代（multiprocessing spawn 的 GUI 子进程）才 kill，否则只记录；
  2) **启动前扫描改为只记录、绝不 kill**（`scan_stale_source_instances` 将 pid/owner/image 写入 `stale_instances_seen`），清理统一由 Lead 协调；
  3) finally 仅清理自己的 pid 树 + 只记录其它 0.5.8 实例；明确排除用户旧实例 PID 5652/23020/34388；
  4) 当前离线扫描到 foreign 0.5.8 实例（PID 33708，codex-runtimes python，疑似 C3）——**未触碰**。
  该教训写入证据：同机多 agent 会同时运行 0.5.8 source-tree 隔离实例，**绝不能按窗口标题清理**。
- **真实窗口使用窗口重叠风险**：10:30–10:42 期间用户机 QQ（hwnd 1574036, pid 21056）频繁前台/覆盖，native 点击会被 QQ 抢走；WeChat 几何也被外部多次改变。驱动必须每次点击前 activate、点击后验证 `find_started`/focus，且 postflight 只保证同一 run 内 visible+rect 不变。
- 真实窗口操作受 Lead 硬性规则约束：不得移动/最小化/恢复用户微信窗口；因此 **停靠模式（dock_wechat=true）下的按钮路径不在本机真实验收范围内**。
- 用户机 QQ 窗口频繁前台：native 点击会受前台/坐标影响；后续驱动必须使用坐标修正 helper + 每次点击前 activate + 点击后验证 `find_started`。
- WeChat 窗口几何被外部多次改变，跨 run 对比 rect 时只能比较“同一 run 内 postflight 不变”，不能假设跨 run 恒定。
- A/B 崩溃原因未明；离线先用 dry/offscreen 模式定位（不占真实窗口）。
- 已安装 0.5.7 实例可能仍连接微信并持有采集；本次未 kill/未交互。
- 聊天内容/截图不落盘；证据只含计数/坐标/布尔/时间与 reason/steps。
