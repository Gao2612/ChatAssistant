# Windows Lite 整改执行进度

## 2026-10-18 C1 应用内查找按钮复核（任务消息 #32）

- **实现**：修复 `find_wechat_conversation.work()` 的完成通知时序：先执行窗口还原，再通知 UI 清除 busy 并恢复采集，避免 WGC/停靠重接管与微信窗口恢复竞态。新增 `test_find_button_lifecycle.py`，用 `QTest.mouseClick` 触发真实 Qt 按钮/后台线程及合成 OCR，覆盖精确目标成功、近似标题拒绝、恢复顺序、无 Enter/发送。经 DeepSeek 确认 C1 write scope 后，更新 `tools/c1_ui_acceptance.py`：从 `build_info.APP_VERSION` 生成窗口标题筛选条件；只接受本次启动 PID 树内的当前版本窗口，移除旧版本硬编码与过期 PID 表。
- **定向实测**：`python -B -m unittest tests.test_find_button_lifecycle tests.test_find_pause_capture tests.test_wechat_find tests.test_wechat_find_foreground tests.test_action_dock_ownership -v` → **39 项通过**。修复前生命周期 2 项稳定复现恢复顺序错误，修复后单模块 2 项通过。
- **工具回归**：新增 `test_c1_ui_acceptance_driver.py`，先因缺少当前版本识别失败，再通过；当前模型版本匹配与旧版拒绝 **2 项通过**。验收脚本与测试模块 `py_compile`、`git diff --check` 通过。实机工具未执行，因为按互审约定需等待 0.5.21 且不得与测试进程/当前用户微信窗口争用。
- **真实 UI 证据**：旧的隔离源码实例通过 UIA `InvokePattern` 调用应用按钮，记录 `find_started=true`、`switched=true`、标题由 finder 严格核验、WeChat 可见且同 run 矩形未变；该证据是 0.5.8 旧运行环境，不等于本轮当前候选复验。当前 UI harness 在另一执行会话报告 `no_wechat_window`；本轮遵照互审建议不再操作用户微信窗口，等待 C13 屏外恢复修复/合适桌面窗口后重验。
- **全量实测与限制**：改后当前工作区全量 **906 项、42 项跳过**；唯一错误为 `test_single_instance.CrossProcessSingleInstanceTests.test_lock_holder_without_instance_info_fails_closed` 的 tearDown 删除 `instance.lock` 遇到 WinError 32。按互审 #30 用同命令在 `a7c5ea0` 独立临时副本对照：基线 **889 项、37 项跳过**，同一测试名出现同一错误。单测独立复跑不稳定：当前分支一次通过、再次失败，基线独立复跑也失败；所以只能确认错误已在基线出现，不能称单跑稳定通过或断言根因已知。未发现 C1 新增的全量失败。日志 `windows/lite/test-output/c1/baseline-a7c5-full.log`、`reopen-20261018-final-full.log`。修改前运行出现的两个 C1 顺序失败在本次修改后消失；本轮不提交、不推送、不发布。

## 2026-10-18 续跑（显式历史资料、导入隔离与 0.5.18 安装候选）

### 实现

- 历史页面新增 app-owned「微信资料」选择器和新建资料入口。新资料使用随机 account ID；已有未归属/混合历史继续显示为旧/未区分，未根据联系人昵称、微信窗口标题、PID 或 HWND 自动拆分。
- 导入前捕获资料 ID 与实时 Scope；导入期间切换资料时，完成结果仍回到原目标资料，但不会覆盖当前选择，也不把导入联系人误绑定给新的实时会话。通过选择修订序号避免“切走再切回来”掩盖变更。联系人 profile 查询限定在当前资料下。
- 版本更新为 0.5.18。没有更改微信登录/扫码实现，也没有访问私有 token 或自动发送消息。

### 验证

- 全量 `python -m unittest discover -s tests -t . -q`：**885 项通过、37 项安全跳过，exit 0**（约 124 秒）。含资料切换、导入回调防串档与版本一致性回归。
- `git diff --check`：通过。
- Python 3.12.14 / PyInstaller 6.22.3 构建便携版与安装器；`verify_build_info.py` 返回 `ok=true`、159 个源码文件、dirty=true；便携 `--version-json` 与 `--smoke` 退出码 0。安装器 SHA-256：`08A87DA3BEF85A846EE3FA11DE911A12A60EC65102F768173F870E72525661F4`。
- `package.ps1 -VerifyInstall` 的隔离安装和安装版 self-test 均 exit 0；卸载步骤被 Windows 应用控制策略阻止启动临时目录的 `unins000.exe`，因此生命周期验证整体失败，且没有绕过策略。未覆盖当前安装。

### 未完成/未测

- 真实微信中央区域被其他窗口遮挡尚未解决；当前仅有遮挡诊断/恢复提示。置顶、多显示器、IME、扫码/模态弹窗、最小化与多开仍需实机验收。
- app-owned 资料不是微信登录账号的稳定身份映射。用户需自行显式选资料；同窗账号切换不一定可检测，不能保证仅凭系统窗口信号实现严格账号隔离。
- 真实 PDF/ExportWeChat HTML 样本、真实 ChatGPT/Codex/本地模型授权与回合、覆盖升级及干净 VM 未测。隔离卸载被系统策略挡住，尚未完成卸载验证。未提交、推送或发布。


## 2026-10-18 续跑重新审查（0.5.8 工作区，未提交）

本节优先于下方旧任务表中的中间状态。用户要求重点重审 `WINDOWS_LITE_UNFINISHED_2026-10-18.md`，并补充模型名称不受服务商支持、多个下拉框缺失的问题。本轮采用 3 个子智能体并行核查，主智能体独占 `ui.py` 集成；无 commit/push/发布，无真实聊天上传、无微信发送、无私有令牌读取。

### 实现与定向实测

- 模型目录：已复现并修复“显示名称当请求 ID 保存”、ChatGPT 目录残留到兼容 API、切厂商/地址/Key 后旧目录仍可选、异步旧接口覆盖新接口、Codex 检测误填视觉 API、Codex 路径变化后的晚到结果、空目录保留旧选择、当前模型提示仍显示旧 ID；`GET /models` 只接纳明确 `id`，不从展示名称猜 ID。Codex 原选择已失效时要求用户重新选择，不静默替换。13 项目录回归已通过（分批先红后绿）。真实运营商支持情况仍需真实 Key/额度验证，目录返回也不保证图像能力。
- 下拉框：读取模型从纯文本框恢复为可编辑下拉框，新增独立读取列表按钮；Codex 图像读取目录过滤 `input_modalities`；所有已有 `NoWheelComboBox` 显式绘制箭头，修复 QSS 下箭头消失的视觉问题。离屏合成设置截图已确认箭头可见；该离屏环境中文字体显示为方框，**不能作为中文字体或真实 DPI 验收证据**。
- OCR 自动分析：已复现“旧远程读取完成被丢弃、最新 OCR 文本相同所以没有新远程任务、分析门控永久未确认”。现仅在无更新待发远程读取且 OCR 帧仍新鲜时，明确回退最新本地 OCR；旧远程结果始终拒绝。`test_remote_reading_stall` 先失败后通过；与自动链路/远程读取/目录组合 24 项通过。读取解析器拒绝空标题及未知消息归属并触发现有回退，相关 33 项通过。
- 历史隔离：修复检索构建索引后被另一线程替换，导致 A 证据配 B 文本的竞态；锁覆盖检索全程。修复同名联系人选择首项误选、解除联系人后残留旧报告/趋势/统计。3 项回归先失败后通过，检索 7 项、当前联系人/历史 UI 9 项通过。已有当前联系人 HTML/PDF 导入入口保留，不重复实现。
- 微信操作：删除查找结果点击后的无条件 Enter，避免焦点回到已有草稿时发送消息；窗口动作期间 `sync_dock` 不重接管。新 2 项回归先失败后通过；查找 5 项、停靠/遮挡 42 项、动作/UI 集成 7 项通过。上述为合成窗口/假后端测试，**A3 设置页真实按钮偶发失败根因仍未证明，仍待验收**。
- 用户新增桌面问题：原生停靠确会按中间开口调用 `set_pos` 改变微信尺寸；两处缺少 `dock_wechat` 的旧配置回退也曾默认为开启。本轮统一默认/缺省为镜像模式，UI 显示清晰的“只读镜像”提示。中央预览新增 适应区域/100%/150%/200% 缩放和滚动；可保留完整窗口图像，不挤压或缩放正常微信。`test_preview_zoom`、`test_mirror_preview`、`test_ui`、`test_ui_design_system`、`test_window_manager_ui` 合计 41 项通过（全合成 UI，未驱动真实微信）。离屏截图确认箭头与缩放入口可见，但中文字体缺失于此运行环境，不能作为字体实测。
- “合成聊天/合成弹窗”桌面窗口来自全套测试的真实 Win32 fixture；已停止那次测试，默认运行现在在启动前 SkipTest；只有显式设置 `CHATASSISTANT_NATIVE_DESKTOP_TESTS=1` 才会开桌面夹具。窗口团队用假启动器证明默认不会 Popen；原生测试模块 112 项、37 项被安全跳过、exit 0，期间未创建桌面窗口。Codex app-server 和 Git 版本探测子进程也加 `CREATE_NO_WINDOW`；2 项启动参数测试通过。

### 恢复与未测

- 本机未发现可调用的 deepseekharness 命令、工具或插件；未安装或调用未知工具。后续若可用，按用户授权用于边界明确的编码并独立审查。
- 已通过自动任务工具恢复 `automation` 为 ACTIVE，保留每小时尝试、无变化安静、额度不能绕过、全部验收后暂停的边界，并加入本次模型/下拉框审查重点。
- 真实 Key/ChatGPT 额度、真实视觉/API/Codex 回合、人工扫码/输入法/弹窗、双微信 30 分钟、干净 VM、真实升级仍未完成；未将旧 0.5.7 测试冒充本轮证据。
- 首次全套回归 690 项中有一项暴露窗口标题仍写 0.5.7；标题已同步 0.5.8。之后安全夹具默认跳过，全量 **696 项通过、37 项跳过，exit 0**（`test-output/resume-0.5.8-tests.log`）。
- 当前候选构建已完成：0.5.8；PyInstaller 6.22.3/Python 3.12.14；source digest `e61444cd3d5a322927d210e649c14167caa295b3b0816a2638a9305d7532b9ee`，137 个源码文件，分支 `feat/windows-lite`，dirty=true（本轮改动未提交）。portable smoke exit 0；安装器 `dist-release/ChatAssistantLite-Setup.exe` 编译成功，SHA-256 `C3E34C98B3663E90BF747B28D73B6791A27108A70EEE5EBAF8C7FF5B862E00E3`；build manifest 检查 `ok=true`；隔离安装/自检/卸载及保留隔离目录通过（所有 exit 0，files_removed/work_dir_preserved/ok=true，日志 `test-output/resume-0.5.8-package.log`）。
- 用户新截图仍没有缩放控件，因此它显示的是旧版 0.5.7；0.5.8 候选包尚未覆盖安装到用户目录。安装器目前仅做临时目录生命周期验证，未触碰实际安装目录。
- 账户配置的原始 DPAPI 文件中 `dock_wechat` 已是 false（只检查/写回这个布尔字段，不输出或读取任何凭据字段）；未覆盖安装。0.5.7 仍为当前安装版本。

### 候选包与看板

- 可审查候选安装包：`windows/lite/dist-release/ChatAssistantLite-Setup.exe`；覆盖安装/正式卸载/真实微信画面比例和实机字体仍需后续人工确认。没有提交/推送/发布。
- 按 `AGENTS.md` 在 Team Board 完成 Codex 接入验收 C0：Codex-only `show`/认领/进度/完成均写到 source=codex，DeepSeek 任务未更改。发现该 CLI `progress` 子命令不接受示例 `--source codex` 参数；用 `TEAM_BOARD_SOURCE=codex` 成功写入，C0 100%。

审查报告（本机忽略目录）：`_reports/resume_pipeline_2026-10-18.md`、`resume_windows_2026-10-18.md`、`resume_history_2026-10-18.md`。

计划：`WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md`。设计：`WINDOWS_LITE_COMPLETION_DESIGN_2026-10-17.md`。分支 `feat/windows-lite`。用户于 2026-10-17 授权按方案整改，补充要求文字读取和对话分析分别选择本地 OCR、多模态模型、ChatGPT/API、Codex 或已运行的本地模型服务。

**2026-10-18 更新**：读取/分析双后端已落码，86 项单测通过；真实 Codex 免费 RPC 验证通过。详见 [`_reports/backend_read_analysis_split_report.md`](../../_reports/backend_read_analysis_split_report.md) 与 [`WINDOWS_LITE_ACCEPTANCE_CHECKLIST_2026-10-18.md`](WINDOWS_LITE_ACCEPTANCE_CHECKLIST_2026-10-18.md)。

**2026-10-18 多智能体实施更新**：用户点1（外部 API 厂商/地址/Key、读取模型列表、当前生效模型显示、保存入口）与点4（ChatGPT 绑定与模型记忆持久化）已由 Lead 完成（T0a/T0b）；T1（契约+单实例）由队友 contracts 并行实现中。基线 commit `558d705`；后续分阶段 commit 到 `feat/windows-lite`（不 push）。本地看板：http://127.0.0.1:8790/ ，共享任务板见 `team_task_*`。

## 恢复入口

每小时自动任务已创建（id：`automation`），在当前聊天继续。若额度限制导致中断，下一次先读本文件和实际源码，再从未完成项目继续，不重新设计或重复已完成测试。只有实际验证过的项目才标记完成。

## 执行决定

- Ruling：在当前 feature 分支原目录继续。现有 0.2 Windows 工程尚未提交；创建新 worktree 不会带入这些文件，且用户已授权在本项目继续整改。保留全部既有修改，不 reset、不清理，不自动 commit/push。
- Ruling：按用户“额度快没了”采用单智能体顺序实现；先处理自动链路与独立后端，再逐步完成 T1–T12。此前规划子智能体均遭遇用量限制。
- Ruling：读取和分析分为两套配置，允许选择不同服务/模型。远程视觉读取会发送所选微信画面；本地模型通过本机兼容服务接入，不自动下载权重。
- Ruling：Codex 后端使用官方 app-server，支持图像输入、模型和账号状态 RPC；不读取桌面私有凭据。选择模型不等于复制当前聊天模型，实际可用能力需请求验证。
- Ruling：先实现用户实际阻塞的回归修复，再完成基础契约、账号服务、模型队列和历史全量分析。计划的依赖接口仍保持。

## 任务状态

| 项目 | 状态 | 证据/下一步 |
| --- | --- | --- |
| 每小时继续 | 已创建 | heartbeat，当前聊天，每小时尝试；额度限制时保存进度 |
| T0a 外部 API 配置（点1） | ✅ 2026-10-18 已落码并 commit `ffbe6ce` | 厂商预设 + `analysis.list_models`（GET /models）+ 自动读取 + 当前生效后端/模型显示 + 卡片内保存；`test_analysis_models.py` 等 |
| T0b ChatGPT 绑定记忆（点4） | ✅ 2026-10-18 已落码并 commit `ffbe6ce` | 诊断：凭据与 refresh token 持久化/刷新正常，根因是模型记忆与提示；新增 `chatgpt_model` 等分后端字段、自动恢复、授权状态与「断开授权」；重启不再要求浏览器授权 |
| T1 契约与单实例 | ✅ 2026-10-18 完成，commit `4f3655b` | `liteapp/contracts.py`、`single_instance.py`、`state.py` 作用域/摘要、`main.py` 单实例；定向 55 项、全套 137 项通过；双进程激活与 smoke 实测 |
| T2 账号注册与授权生命周期 | ✅ 后端完成，commit `a06f9a7`；UI 拆到 T2-UI | `account_store.py` 单写者/原子保存/刷新 CAS/权限状态/退出；settings/subscription 路由；定向 32 项、相关回归 144 项通过 |
| T2-UI 集成本地账号管理界面 | ✅ 2026-10-18 完成，commit `239545e` | 账号列表/切换/重新授权/刷新模型/退出（AccountStore.sign_out）；AuthAttempt 连接新账号；4 项 UI 测试；0.4.0 | 设置页多账号列表/新增/切换/重新授权/退出/刷新模型；用 AuthAttempt 非阻塞 + Signal 回主线程；不把 subscription=None 当断开 |
| T3 通用模型层/取消/调度 | ✅ 2026-10-18 完成，commit `e66cb5d` | `inference.py`/`inference_schemas.py`/`inference_queue.py` + analysis/subscription 路由；定向 41 项、相关回归 158 项、全套 376 项通过 |
| T3-UI 接推理队列 | ✅ 2026-10-18 完成，commit `3841d94` | 自动分析统一走 InferenceQueue（全局 1 并发/同源最新/额度暂停/恢复）；5 项 UI 队列测试；旧同步路径测试已适配 | ui.py 自动分析改 `queue.submit + set_callback + Qt dispatch`；切会话 `cancel_scope`；额度错误 pause 提示 |
| T4 原生停靠/焦点/恢复 | ✅ 2026-10-18 完成，commit `645144a` | `window_manager.py`、`recovery_guard.py` + 合成窗口 fixture；discovery/window_dock/worker/main 加固；定向 100 项、排除 WIP 线 315 项通过 |
| T4-UI 接 WindowManager | ✅ 2026-10-18 完成，commit `403c526` | ui.py 全面接入 WindowManager：set_view/健康只读回退/record_* 新鲜度/detach；R-07 修复；26 项新增、200 回归、520 全套 |
| T5 历史 V2/迁移 | ✅ 2026-10-18 完成，commit `8dfcecc` | `history_models.py`、`history_migrations.py`、V2 `history.py`；强制 ScopeKey/版本语义/事务迁移/DPAPI；历史定向 38 项、排除 T4 线的全模块 199 项通过 |
| T6 导入器 | ✅ 2026-10-18 完成，commit `214b6fe` | importers/ + import_jobs.py + history_ui 最小导入 UI；pypdfium2 固定版本与许可入库；定向 58 项、全套 376 项通过；ExportWeChat 无真实样本 |
| T7 全量历史模型分析 | ✅ 2026-10-18 完成，commit 待记 | history_jobs/prompts/analysis_store；定向 37、全套 459 项通过；合成真实 job 覆盖率 1.0/7 证据回读；设置模型 V4.1-Flash-1017 被 DeepSeek 400 拒绝（真实分析前需改选 deepseek-flash 等） |
| T8 档案/趋势/统计/任务界面 | ✅ 2026-10-18 完成，commit `0000cd4` | history_statistics/profile_ui + history_ui 五标签；定向 41/68 项、真实 T7 联调通过；PySide6 关闭期崩溃已修；DPI 真实屏幕留 T12 |
| T9 记忆检索与实时建议整合 | 🟡 UI 集成完成（今日提交）：会话↔联系人绑定 + 历史参考注入 + “参考历史 N 条”入口；真实模型长上下文/引用展示实机验收留 T12 | 未绑定不检索；切换会话/窗口失效；434/634 全量通过；真实模型回合与 T12 集中验收待做 |
| T10 多微信来源调度 | 🟡 代码完成（registry/scheduler/background monitor + 15 项测试，UI 开关默认关闭、最多 2 个后台来源）；真实双窗口 30 分钟与 CPU/句柄趋势留 T12 | active 优先、后台默认关闭、最小化暂停、帧未变不重复 OCR、单源关闭不影响其他；后台不移动/不键入窗口 | `source_registry.py`/`capture_scheduler.py` |
| T11 构建追溯与安装生命周期 | ✅ 代码/构建/隔离安装已验证（干净 VM 归 T12）；0.4.1 已重建并验证（build_info 摘要 clean、中文安装器、隔离 install/self-test/uninstall 保留数据 ok=true、安装版 version/self-test/smoke×3 exit 0）；干净 VM 与真实升级留 T12 | 候选包 0.5.15 SHA256 `AD7F3DDF…C92375DA`（0.4.x/0.5.0–0.5.14 为中间包）；安装版 0.5.15 已覆盖安装到 `E:\ChatAssistantLite` |
| T12 集中实机验收 | 🟡 自动项已通过（3×644 回归、单实例、版本无泄露、自检 fail-closed、隔离安装生命周期）；真实微信场景待人工；候选包 0.4.1（SHA256 `F8EC437C…2DC69E`）已装；自动项（version/self-test/smoke×3、隔离 install 生命周期）通过，11 项真实场景逐项进行中 | 面板 S-V01…S-V11，做一项同步一项；人工扫码/输入法/弹窗/双窗口等待用户在场 |
| S-安装包中文化 | ✅ commit `f9e7c6d` | 内置简体中文语言文件（向导/卸载/任务/启动描述），ISCC 编译通过；随下一版安装包生效 |
| S-回复风格扩充 | ✅ commit `f9e7c6d` | 9 个预设 + 可自定义、禁滚轮、自定义文本保留；16 项 UI 定向通过 |
| S-微信隐藏/防重复启动 | 🟡 windows 队友实现中（task-17） | 发现托盘/隐藏/最小化窗口、显式恢复 API、已有进程不重复 Popen；UI 接线待 Lead |
| S-HTML/PDF 专属风格 | 🟡 history 队友实现中（task-18） | `style_profile.py`：筛选“我”的发言→本地特征→中文风格说明→加密派生记忆；UI 接线待 Lead |
| P1 测试进程退出崩溃 | 🟡 contracts 排查中 | 单模块通过、组合模块在 “Ran OK” 后 0xC0000409；不修完不把全量回归当 T12 证据 |
| S-DOCK 镜像预览/停靠开关 | ✅ 完成 | 默认镜像只读预览，不改原生微信大小/位置；停靠改为默认关闭的显式开关；应用 resize 只缩放预览；3 项新测试，全量 625 通过 |
| S-自动查找微信对话 | ✅ 真实验收通过（仅切换） | 恢复托盘窗口→OCR 搜索“田子夜”→点击结果+Enter→顶部 OCR 命中；未发送消息 |
| P1 测试退出崩溃 | ✅ commit `4d8642d` | tests/__init__ runner 后清理顶层 QWidget；28/60→0/60；全量 622 项 `discover -s tests -t .` exit 0 |
| S-TRAY-ACT 托盘隐藏下自动找对话/发送 | 🟡 找对话实测通过；发送方案同构待授权实测 | 临时恢复窗口→OCR 找对话→恢复托盘；发送需标题校验+焦点探针+双条件验证；UIA 实测不可用 |
| S-STYLE-UI 专属风格设置页接线 | ✅ 完成 | 生成/预览/选用双层风格；prompt_style 写入风格设置；无联系人/样本不足给出可操作提示；全量 627 通过 |
| S-AUTO-UI 自动找对话/发送接入 UI | ✅ 完成（待打包后实机点击验收） | 设置目标联系人+自动查找；建议旁“发送到微信”需勾选允许并确认；标题校验→OCR 定位发送按钮→双条件验证；托盘隐藏时临时恢复再隐藏；631 测试通过 |
| D-UI 按设计 Skills 重构 UI | ⏳ 用户新增，排在功能与验收之后 | frontend-design / ui-ux-pro-max / impeccable / web-design-guidelines / theme-factory / canvas-design / web-artifacts-builder / brand-guidelines / react-best-practices / webapp-testing；PySide6 桌面 UI 按设计原则重构，React/Next 条目仅取可迁移原则 |
| 设置下拉禁滚轮 | ✅ commit 29678b1 | 所有设置下拉 NoWheelComboBox，滚轮不改选择，弹窗最多 20 行 |

| REVIEW 独立代码审查 | ✅ 完成：P1 三项均为待做 UI 接线；P2 R-04/05/06 与 R-09 已派 contracts（task-15）；P3 记录/择机 | REVIEW_report.md：383 基线、9/9 变异捕获；R-01/02/03 = T2/T3/T4-UI；R-07 在 T4-UI 修 |
| P2 修复 | ✅ 2026-10-18 完成，commit `4cb2268` | R-05 SSE 有界（16.9MB→0.43MB）、R-06 证据强制、R-04 超时页重试、R-09 自检隔离、HTTP400 可操作提示；124 项回归通过 |
| OCR 后自动分析 | 回归修复已落码，单测通过 | `state.py` 修订语义 + `ui.py` 重检恢复；`test_auto_pipeline.py` 4 项。0.2 安装包不包含该修复，需安装 0.3 包 |
| 读取/分析独立配置 | ✅ 2026-10-18 已落码，单测通过 | `settings.py`（读取键 + DPAPI `protected_reading_key`）、`backends.py`、`reading.py`、`codex_backend.py`、`worker.py`、`ui.py`；86 项测试通过 |
| 读取后端：本地 OCR | 默认保留，完全离线 | `worker.py`；原有验证 |
| 读取后端：多模态模型 | ✅ 已落码（OpenAI 兼容视觉接口） | `reading.vision_read`；仅假 HTTP 服务端测试，真实接口未验证 |
| 读取后端：Codex | ✅ 已落码；真实免费 RPC 验证 | `codex_backend.read_image`；账号 Plus、7 个模型均 text,image；真实图像回合未执行（额度） |
| 分析后端：ChatGPT 套餐 | 保留原有 | `subscription.py`；10-17 真机验证 |
| 分析后端：兼容 API | 保留原有 | `analysis.py`；无真实第三方 API 调用证据 |
| 分析后端：本地模型服务 | ✅ 已落码（默认 `http://127.0.0.1:11434/v1`，可无 Key） | 本机无本地模型服务，未实测；假服务端测试 |
| 分析后端：Codex | ✅ 已落码；真实免费 RPC 验证 | `codex_backend.analyze`；真实文本回合未执行（额度） |
| 中央遮挡/当前联系人导入 | 未修复 | 根因与目标已写入设计和 T4/T8/T9 |
| T1–T12 | T1–T6 完成；T7–T12 进行中（Lead） | 逐任务更新，不把旧单元通过计为新任务完成；T2-UI/T3-UI/T4-UI 为 Lead 的 UI 集成 |
| 0.3 安装包 | ✅ 0.3.1 已构建并覆盖安装；T1–T6 合并后需重建 0.4.x | 安装器 99,456,186 B，SHA-256 `6A35A86AD8BD4C1F48C877566BB8F578ABFCDE58DA7282766F1DF2FD7DA0F987`；卸载注册表 0.3.1；安装后 `--smoke` 退出码 0 |

## 最新验证

- 2026-10-18：**T1–T6 全部完成并分阶段提交**：T1 `4f3655b`、T2 后端 `a06f9a7`、T3 `e66cb5d`、T4 `645144a`、T5 `8dfcecc`、T6 `214b6fe`；T11 工具 `11ba51f`、T12 矩阵 `b15194f`。全套测试 **376 项通过**（68–70s）。T2-UI/T3-UI/T4-UI、T11 main.py 接线与 T7–T12 由 Lead 继续。
- 2026-10-18：**0.3.1 已构建并覆盖安装**：安装器 SHA-256 `6A35A86AD8BD4C1F48C877566BB8F578ABFCDE58DA7282766F1DF2FD7DA0F987`；卸载注册表版本 0.3.1，安装目录 `E:\ChatAssistantLite`，桌面快捷方式指向该 exe；安装后 `--smoke` 退出码 0。
- 2026-10-18：T0a/T0b 落码后 `python -m unittest discover -s tests -q` → **137 项通过**（含 T1 队友并行新增的 contracts/single_instance 用例与 Lead 新增的 `test_analysis_models.py`）。T0a/T0b 单独覆盖：模型列表读取、厂商预设、分后端模型记忆、重启恢复、断开授权、ChatGPT 有效模型回退。
- 2026-10-18：真实 ChatGPT 凭据诊断：`protected_subscription` 与 refresh_token 存在；access token 过期后 `subscription.access_token()` 刷新成功并回写 DPAPI（不消耗模型额度）。
- 2026-10-18：`python -m unittest discover -s tests -q` → 86 项通过（T1 队友用例加入前）。
- 2026-10-18：真实 Codex app-server 免费 RPC（initialize / account/read / model/list）通过：账号 `chatgpt` / `plus`，7 个模型均支持 `text,image`，关闭进程干净退出；未发送聊天内容、未消耗模型额度。
- 2026-10-18：源码 `QT_QPA_PLATFORM=offscreen python main.py --smoke` 退出码 0；PyInstaller 重建便携版并通过 `--smoke`。
- 2026-10-17：基线 46 项通过；新增自动链路回归先失败 3 项，修复后 51 项通过。
- 2026-10-17：真实微信停靠/采集/OCR 与 ChatGPT 授权证据见 `WINDOWS_LITE_VERIFICATION_2026-10-17.md`，本轮未重复实机测试。

## 续跑注意

- 真实微信扫码、登录和最终发送由用户完成；合成数据用于依赖调试。外部 model 调用只在明确选择的后端执行。
- Codex 真实模型回合会消耗用户额度：先点设置里“检测 Codex 账号与模型”做免费验证；不要用真实对话测试额度。
- 读取/分析双后端的安全边界：Codex 只读沙箱 + 审批 `never` + 拒绝服务端工具请求；远程读取只发送当前画面；失败回退本地 OCR；OCR 在远程确认后不覆盖远程转写。
- 留意代理环境影响本地 HTTP 回环测试，不用失败的回环连接推断远端模型不可用。
### T12 收尾状态（2026-10-18）

- 自动可做项已完成：构建/版本追溯/单实例、隔离安装生命周期、全量回归、真实窗口发现与镜像、连接与 OCR 读取、托盘隐藏读取、冷启动 ChatGPT 授权保留、模型选择持久化、自动找对话代码级真实查找
- 仍待用户（不是代码阻塞）：
  1) 本机兼容 API Key 是早期测试写入的占位值 `test-key`，真实分析会授权失败；ChatGPT 套餐返回额度不可用 → 无法完成真实模型回合
  2) T12 人工场景需要人在场/真实操作：应用内点击自动查找、发送确认与双条件验证、真实扫码登录、输入法/弹窗/双窗口 30 分钟
- 已交付 0.5.7 安装包与 [T12 人工交接](T12_MANUAL_ACCEPTANCE_HANDOFF_2026-10-18.md)；收到用户反馈后继续修复或签收
### 未完成事项清单（唯一入口）

所有未完成/待用户验收项已汇总到 [WINDOWS_LITE_UNFINISHED_2026-10-18.md](WINDOWS_LITE_UNFINISHED_2026-10-18.md)：
阻塞凭证（API Key 占位 / ChatGPT 额度）、S-V01/S-V03/S-V05–S-V11 人工场景、T9/T10 真实回合与本次已知限制。
### 悬浮窗与布局/托盘/模型修复（2026-10-18 12:15）

- 新增悬浮窗（置顶/无边框/可拖动）：只监控原生微信窗口，提供会话状态、分析状态、“分析当前对话”和“主界面”；主窗隐藏后采集/分析继续，设置/历史/AI 建议仍在主界面。
- 预览默认“适应区域”居中；AI 建议面板切换不再调整窗口大小，最大化下不会跑到屏幕外。
- tray_capture 补 64 位 GDI argtypes，cloaked/offscreen 判定改为 lifecycle status；“微信已隐藏到托盘，暂时无法读取画面”修复。
- 真实 profile 模型从显示名 `DeepSeek-V4.1-Flash` 规范为 `deepseek-flash`，合成文本真实分析返回 3 条建议（HTTP 400 结束）。
- 0.5.12 已安装（SHA `4C63636F…E724A7BB`）；全量 753 OK/37 skipped。
- Codex 14:51 后任务：C6 悬浮窗真实化、C7 托盘读取可靠性、C8 布局/模型迁移验证（看板 source=codex）。
### 悬浮球主界面（2026-10-18 13:05）

- 圆形置顶悬浮球：点开为 3 条 AI 建议（复制/发送）+ 分析当前对话 + 设置/退出；球可拖动、收起。
- 球模式下主窗口只保留设置/历史，聊天页与右侧 AI 面板隐藏；主窗关闭回到球，球“退出”结束进程；`floating_ball` 持久化。
- 0.5.13 已安装（SHA `FD749F54…1373939`）；全量 755 OK/37 skipped。
- C6 已更新为“悬浮球真实化/不确定行为”，C7 托盘读取、C8 布局/模型迁移继续排给 Codex 14:51。
### 好感度/固定联系人/专属档案（2026-10-18 13:30）

- `affection.py`：参考 crush-monitor 6 维本地估算（权重 15/20/20/15/20/10，边界封顶 25）；悬浮球显示分数环/等级/维度，标注“本地估算”。
- `favorite_contacts`：悬浮球“固定”按钮；只自动分析固定联系人，非固定需手动按钮。
- `contact_profile.py`：DPAPI 加密 `contact_profiles.dat`；生成专属风格时保存联系人档案；悬浮球“档案”打开历史页。
- 0.5.15 已安装（SHA `AD7F3DDF…C92375DA`）；全量 799 OK/37 skipped。
- 遗留：真实 settings 被合成配置覆盖，DeepSeek API Key 需重填（路径/模型/账号已恢复）；模型化好感度/导入自动档案/同名匹配交 Codex C9-C11。
## 2026-10-18 19:30：模型逐句好感度 + 导入自动档案（0.5.19）

- 合并 Codex 0.5.18 未提交工作（历史分析任务/联系人上下文/模型目录/窗口诊断等）与 DeepSeek 新模块。
- `model_affection.py`：分块逐句模型判断 emotion/intent + 6 维 affinity（权重 15/20/20/15/20/10，0-4，边界封顶 25），聚合/重试/partial/cancel/next_offset/progress；大文件不再整篇塞 prompt。
- `auto_profile.py`：导入后自动本地基础档案；`history_ui.import_completed` → MainWindow 自动启动模型档案（可配置 `auto_model_profile`）；同名联系人手动关联/新建/取消。
- 悬浮球：模型档案字段展示、模型分析按钮/进度；固定联系人严格门控自动分析。
- 全量 888 tests OK/37 skipped；0.5.19 构建/安装待执行。真实模型需用户重填 Key 后验证。
### 0.5.20 最终集成（2026-10-18 19:44）

- 合并 Codex 0.5.18 未提交改动（历史分析任务/当前会话 Scope/模型目录/窗口诊断）与 DeepSeek 模型模块；首次提交 `1ead87b`（888 tests），点击修复 `a7c5ea0`（889 tests）。
- `model_affection.py` 分块逐句 emotion/intent + 6 维 affinity，失败重试/partial/cancel/next_offset/progress，解决大文件一次塞爆；`auto_profile.py` 导入自动基础档案，`history_ui.import_completed` 触发 MainWindow 自动模型档案；档案字段 relation/topics/preferences/taboos/reply_style/important_dates/persona；同名手动确认。
- 悬浮球模型档案字段/进度/“模型分析档案”按钮；BallFace 点击穿透修复。
- 0.5.20 已安装（SHA `5A5DE2A8…E8CCA4E`）；全量 889 OK/37 skipped。
- 真实模型未测：DeepSeek API Key 仍需用户重填；导入后自动模型分析将在 Key 就绪后运行，大文件按块进度显示。
### 0.5.21（2026-10-18 20:45）：用户 #41 反馈 + Codex C8-FIX/C13/C1

- 合并 Codex 未提交工作并提交 `a4dfc79`：完整模型 ID、屏外微信窗口识别/恢复、C1 find 完成信号竞态、版本感知 C1 driver；全量 909 OK/42 skipped。
- 关联当前会话：未选档案时按会话标题查找/新建联系人，绑定后显示联系人名并跳“档案”tab，创建加密基础档案。
- 新图标（exe/安装器/桌面/开始菜单）与左侧“对话/档案/设置”文字导航；悬浮球奶油内核+渐变环+固定心形。
- 0.5.21 已安装（SHA `35A293EA…89AD6A5`）。
- 新登记 C14 悬浮球/图标视觉复核、C15 设置页功能分区、C16 绑定→名字+档案（已实现待真实复验）；C1 真实 UI 未签收，C6/C7/C11/C12 待复核；DeepSeek API Key 仍需用户重填。

### 2026-10-18 20:50：C1 当前版本真实 UI 复验结果

- **实现**：当前分支源码为 0.5.21；自动查找按钮能在隔离用户目录中实际触发，后台任务正常开始并完成，运行期间微信窗口始终可见、矩形前后不变；未发送消息、未保存截图。
- **实测失败**：真实 UI 报告 `windows/lite/test-output/c1/c1_ui_acceptance_20261018-204758.json` 显示 `find_started=true`、`find_finished=true`，最终失败分类 `not_foreground`，`switched=false`、`title_seen=false`。说明按钮接线/完成回调已验证，但微信前台保持/恢复链路尚未验收通过。已在看板会审 #82 请 DeepSeek 定位修复并增加不含聊天内容的安全诊断；等待其修复后复验。
- **代码回归**：`python -B -m unittest discover -s tests -t . -q` → **909 项通过，42 项 skipped，exit 0**；含 C1 测试及单实例清理回归。`git diff --check`、C1 驱动脚本 `py_compile` 通过。
- **执行记录/边界**：用户选择先关闭已运行助手。可见悬浮球与主窗已发出正常关闭并隐藏；其 Python 进程仍存活但不再枚举出应用主窗，未强杀。隔离验收器启动的是当前源码版本且仅终止自己的 PID 树。真实 UI 本次失败，不得将 C1 标成完成。
- **仓库状态**：DeepSeek Harness 曾在未授权时创建本地提交 `a4dfc79`；已用 `git reset --mixed f3f277d` 恢复到提交前分支头，所有文件内容保留为工作区改动；未提交、未推送、未发布。后续继续禁止自动提交。
- **追加审计**：20:46 DeepSeek Harness 又创建文档提交 `551db38`（发生在 20:48 收到的禁止提交提醒之前/后续会审要求仍适用）。20:49 已再次 `git reset --mixed f3f277d`，提交内容及全部工作区文件均保留；当前 HEAD=`f3f277d`，无新提交、推送或发布。已再次要求 DeepSeek 关闭自动提交。
### 0.5.27（2026-10-18 23:15）：最小化抢占 + 悬浮球重做

- **微信最小化抢占修复**：`worker.py` 区分 `minimized` 与 `hidden`；最小化时不再 `capture_hidden_window`（该路径会短暂 ShowWindow 抢窗口），改为只上报暂停；`ui.py` 分别提示“已最小化，读取已暂停”/“已隐藏到托盘，仍在后台读取”。实测最小化后 5 次轮询状态稳定，启动微信按钮仍能还原。
- **悬浮球**：1) 透明窗口 + 圆角面板，去掉方形黑边；2) 回复 `设置/−/×` 三个明确按钮，去掉 ⋯ 菜单；3) 好感度卡改 60px 环形 + 六维圆点；4) 空状态/建议切换用 QStackedWidget，高度按内容收拢；5) Esc 收起、展开态空白点击不再误收起、拖动阈值；6) 各按钮 ID 级 focus/disabled 样式；7) 档案字段列表渲染去掉 repr。
- 图标：exe/快捷方式/任务栏统一新图标，设置 AppUserModelID；删除桌面旧 0.5.21 安装包。
- 全量 910 tests OK/42 skipped；0.5.27 已安装（SHA `ED447CF1…73E5B96C`）。