# Codex 接管期独立复核报告（history，只读）

- 任务：Team 共享任务 `task-25` / 本地看板镜像 `TAKE-*` 复核者
- 时间：2026-10-18 10:11 起（Asia/Shanghai），接管窗口 14:51 前；**v1 10:16 → v20 冻结终稿 11:00**
- 仓库：`G:\codex\ChatAssistant`，分支 `feat/windows-lite`，**最终冻结 HEAD `6e264eb`（clean；`13c7ff3` 为其祖先的 0.5.9 bump 提交）**
- 复核方式：**只读**。不改业务代码、不 commit/push、不发微信消息、不读取真实聊天内容/凭据。唯一写入：本文件。最终干净全量已跑（§7），结果为 `Ran 750 tests / OK (skipped=37) / exit 0`。
- 基线状态：冻结工作区 `git status` 为空；接管产物全部已提交；安装版 0.5.9（build_info commit `13c7ff3`、source_digest `9340d08…`，Lead 记录 SHA `119FCC83…40BE1B6`，version/selftest/smoke/verify_install 通过）。

## 0.0 v20 最终冻结终稿（11:00）— 干净全量通过，接管复核结束

- **干净全量（Lead 指定，冻结后）**：HEAD `6e264ebf620533f016c565c6d62d76af558eeba9 docs: record clean 0.5.9 candidate and Codex takeover result`（`git status` 空，dirty=false；`13c7ff3` 为其祖先）；命令 `cd windows/lite; venv python -m unittest discover -s tests -t . -q` → **`Ran 750 tests in 117.904s` / `OK (skipped=37)` / exit code `0`**；运行前后 dirty 均为 0。详见 §7。
- **接管任务最终状态**：C1 completed（带限定：InvokePattern 等价槽 1 次真实成功；native 鼠标路径与 A-B 真机差分未完成）；C2 completed（隔离 test-AppId 重建包生命周期 26/26；真实候选包/干净 VM 待复跑）；C3 85%（部分自动 + 6 项人工；S-V03 待现场复签；F1/F3 产品建议）；C4 completed（前台守卫/精确匹配/无 Enter，真实窗口两目标成功，16 项测试）；C5 completed（17 项合成测试/证据；合成≠真实模型/双账号）。安装版 0.5.9 已安装并通过自检/冒烟/verify_install（Lead 记录）。
- **剩余风险（终稿保留）**：
  1. C1：native 鼠标点击路径未验收、A-B 差分未产出（驱动层限制；产品槽已由 InvokePattern 等价触发验证）。  
  2. C2：正式 AppId 的真实覆盖升级/干净 VM 未验；0.4.x 为 git 历史重建包（§7 已限定）。  
  3. C3：真实弹窗/真实托盘关闭/停靠视觉/最小化需人工；F1（aperture 最小时长提示不明确）、F3（托盘抓帧与恢复竞争）为低风险产品建议；F2 本机外部窗口/DPI churn 使跨 run 几何不可比。  
  4. 发送验证 `marker=reply[:6]` 仍偏弱（`ui.py:2563`，P2-3；真实发送需额度，C1/C4 范围外），建议后续加固。  
  5. C4 屏幕抓取要求微信前台且无遮挡（fail-closed、有 foreground guard）；真实聊天内容不落盘。  
  6. 严格模型 ID/读取校验的误拒/UX 需真实服务商与真实多模态服务验证（P3）。  
  7. 本报告 P1/P2 项均已在冻结前跟踪/修复/披露；无未修复的高风险安全问题。
- **复核结论**：证据链、版本/commit、隔离 profile、真实数据/凭据边界、全量测试均满足；本报告作为只读复核记录随仓库提交（若 Lead 提交）。task-25 完成。

## 0.0 v19 状态更新（10:58）— C3 最终复核

- **C3 证据可信但 S-V03 不能完整签收**：
  - run A（外部 UIA 黑盒 10:50:39–10:52:14）：28 pass/11 fail/7 manual；app 存活、无发送/聊天内容/截图；dock 失败解释为本机测试窗口过矮（aperture < 300 / run2 980×296）→ `WindowManager.attach` `geometry_mismatch` fail-closed（安全），但 UI 提示“微信窗口状态异常：中央为只读画面，请重新连接。”未说明“窗口太小”（**F1 产品 UX 低风险**）；cleanup `ok=false`（attempts 6，最终 visible；当前微信 visible，但外部又改几何 [74,53,708,504] → **F2 外部 churn 真实**，跨 run 坐标不可比）。
  - run B（进程内 10:56:44–10:57:11）：**aperture=980×660 时 dock_on attached/moved_into_aperture/zorder 全 true**；dock_off geometry 恢复 true；minimize 状态/editor 清空/恢复/reading 全 true；tray reading/degraded、no_second_wechat、geometry/reading true；`tray.restored_visible=false`（inproc harness 未点“启动微信”，postflight 已 visible）、popup 不可检测（微信菜单非独立顶层窗口，列人工）。**F3**：托盘抓帧“离屏显示+PrintWindow”与“启动微信”恢复有一次竞争，建议产品加互斥/恢复后校验重试。**F4**：UIA 看不到 attach reason，dock 归因需进程内探针。
  - 结论建议：C3 标“**部分自动化通过 + 6 项最小人工步骤（停靠视觉/真实弹窗/真实托盘关闭/最小化/发送确认/DPI）+ F1/F3 产品建议 + F2 环境限制**”，S-V03 待 Codex/用户现场复签；不写“S-V03 通过”。
- **C1**：Lead 裁定 completed（带限定：InvokePattern 等价槽成功；native 鼠标路径/A-B 未完成）；复核接受，交付不得写 native/A-B 已验证。
- **C2** 结案（28c03a7/d87253a/44abae2，快捷方式已恢复并验证）；C4 已提交 54436b6/48f30fb；C5 已提交 163c759。
- **待 Lead 提交 C1/C3 并冻结后**：跑干净全量（记录 commit/exit/通过/跳过），更新 §7，完成 task-25。

## 0.0 v18 状态更新（10:52）

- **C1 已由 Lead 裁定 completed（带限定）**，C1 证据 MD 已写：唯一成功 run 是 `c1_ui_acceptance_20261018-105003.json`（0.5.8/44abae2+dirty、隔离 profile、`find_started=true`、`status_category=success`、`switched=true`、duration 5.2s、无发送、WeChat visible+rect 不变）；**触发方式为 Qt 可访问性 `InvokePattern`（等价按钮槽），不是 native 鼠标点击**；C1 明确记录缺口：native 鼠标路径未验收、A/B 真机差分未产出、dock 模式不在此环境验收。C1 离线回归 37 项 OK。复核结论：证据与限定表述真实，可接受；不应把“native 鼠标点击已验证/A/B 已验”写进交付。
- **C3 run3 已启动**（10:51 后，app-stdout/stderr 已捕获、新隔离 profile），等待结果；其工具已加 baseline 漂移检测/app 日志；dock 暂不判产品失败、S-V03 不签收。
- C2 已结案（28c03a7/d87253a/44abae2，快捷方式已恢复）；C4/C5 已提交；C1/C3 待提交/收尾；冻结后干净全量待执行。

## 0.0 v17 状态更新（10:51）— C1 首次真实 UI 成功切换

- **C1 最小诊断出现 1 次真实成功**：`test-output/c1/c1_ui_acceptance_20261018-105003.json`：app_version=0.5.8、commit=44abae2、dirty=true、隔离 profile；完整 stages 到 `find_started=true → find_finished=true`，`status_category=success`、`switched=true`、`find_duration=5.2s`；`message_sent=false`、`screenshots_saved=false`、WeChat after visible 且 rect 与 preflight 一致 `[115,83,1106,718]`；旧 0.5.7 实例未交互。
- 同批另两次：10:48:44 早期退出（只到 child_started）；10:49:32 `find_started=false / click_not_delivered`——印证此前 4 次失败主要是 driver 点击投递问题；10:50:03 修正后成功。
- **C1 证据限制建议**：仅 1 次成功（非多次稳定）；JSON `events=[]`、`title_seen=false`，成功来自 UI 状态类别而非直接记录目标会话标题；A/B 真机仍未产出；source tree 为 44abae2+dirty（C1 ui.py 未提交）→ C1 提交后应用同 driver 复确认 1 次。C1 仍不应写 A3“充分验证”，建议写“修复后 App 内真实点击 1 次成功切换；driver 失败另计；A/B 未完成，待 Codex/用户复验”。
- **C3** 离线 patch 中（tool 67.6KB、`patch_c3.py`），baseline/dock/工具条件修正后待干净窗口重跑；dock 暂不判产品失败/S-V03 不签收。
- C4/C5 已提交；C2 已结案；冻结后干净全量待执行。

## 0.0 v16 状态更新（10:49）

- **C2 已结案**：Lead 提交 `28c03a7/d87253a/44abae2`，C2 工具/MD/证据摘要入库；桌面+开始菜单快捷方式已用 WScript.Shell 重建，我验证两处都指向 `E:\ChatAssistantLite\ChatAssistantLite.exe`；C2 MD §7.7 已补披露。C2 资格按 §7（隔离重建包/V2→V2/非干净 VM）限定。
- **C3 第二次真实 run（10:45–10:47）复核**：app 全程 alive（PID 33708），无发送路径/editor 只记长度/cleanup visible+rect restored，statuses 正常；**23/40 通过、17 失败**：
  - **4 项 geometry 失败系基线漂移**：preflight baseline `[115,83,1106,718]`（10:45:06）→ setup.connected `[144,104,1383,898]`（10:45:13）；`setup.wechat_untouched_in_mirror / dock_off.geometry_restored / minimize.restored_visible / tray.geometry_restored_after_tray` 都是旧 baseline 比对失败。建议工具在每场景前重取 rect，变化即 abort 标“环境不稳定”。
  - **dock 附着为真问题（待定性）**：勾选+保存成功但状态“微信窗口状态异常：中央为只读画面，请重新连接。”，`moved_into_assistant/aperture_shows_wechat/zorder_ok` false，WeChat 未进 aperture；可能环境干扰或需重连才 dock，S-V03 不能直接签收，需 C3 区分并写清。
  - 工具/环境类：popup 右键中心未打开真实菜单（后序 popup 检查连带失败；`no_misread_editor_empty` 在“未覆盖”前提下应条件化）；minimize 无专门状态文案但 reading_resumed 通过；tray 进程计数/no_second_launch 启发式需核对是否误报。
- **C1**：A/B 真机仍未产出；dry 验证通过（`c1_find_ab_20261018-104734.json`：imports→bind→dry complete）；真实 A/B 崩溃点未定位；C1 计划 C3 释放窗口后做 1 次 ≤3 分钟最小诊断（Lead 批准中）。C1 不写通过。
- C4/C5 已提交；C3/C1 未完成；冻结后干净全量仍待执行。

## 0.0 v15 状态更新（10:48）— C2 复核通过（有资格限制）

**C2 复核结论：可标完成**（按 `docs/windows/CODEX_TAKEOVER_C2_UPGRADE.md` §7 的“隔离 test-AppId + git 历史重建包”范围）。核对证据：
- JSON `ok=true` 26/26 步；Inno install/upgrade/reinstall/uninstall 日志均含 `Installation process succeeded`/`Uninstallation process succeeded`（逐文件核对尾部）。
- AppId 隔离：patched .iss `AppId={{C2TAKEOVER-0000-4000-8000-C2C2C2C2C2C2}` / `AppName=ChatAssistant Lite C2 Test` / `CloseApplications=no`，与真实 `{AC7EB68E…}` 不同；跑后 HKCU Uninstall 无 C2TAKEOVER 残留键；真实键仍在且 `DisplayVersion=0.5.7`、`InstallLocation=E:\ChatAssistantLite\`。
- 真实 profile：`before==after`，**现在重算 SHA 仍一致**（settings `3911d1bc…`、memory `062058e3…`）；真实安装 `E:\ChatAssistantLite` 文件时间 5:09–5:10 未变；3 个 0.5.7 进程 PID 5652/34388/23020 启动时间未变。
- `data_migration={source_version:2,target_version:2,migrated:false,old/new_history_schema:2}`，与文档“V2→V2 非 V1 迁移”一致；安装包 SHA 与 `setups-sha256.txt` 一致；证据副本在 `test-output/c2-upgrade-evidence/`（gitignored）。
- **新增 P3（证据/交付披露）**：C2 MD §7.6 line 114 只提“开始菜单快捷方式被早期未隔离 test installer 替换/删除”，我扫描 Start Menu 与 `OneDrive\Desktop` **均无 `ChatAssistant Lite.lnk`**——桌面快捷方式当前也缺失；不影响数据/凭据/已安装 0.5.7/注册表/进程，官方 0.5.8 重装会重建（或手工重建）。建议补进 §7.6/交付说明。
- 备注：uninstall 步骤 JSON 里 `install_dir_removed=false`+`unins000.exe` 是卸载器自删除时序；复核时该目录已空/不存在，不影响结论。
- C2 证据簿记：Inno 日志在 gitignored `test-output/`；MD+两个 tools 仍 untracked，需 Lead 提交；若需长期追溯可把 `lifecycle-summary.txt`/`setups-sha256.txt` 摘要并入 docs。

## 0.0 v14 状态更新（10:46）

- **C2 已全绿**：lifecycle6 `ok=true, 26/26 steps`，`real_profile_unchanged=true`，`data_migration={source_version:2,target_version:2,migrated:false,old/new_history_schema:2}`（V2→V2，非 V1 迁移，证据需如实写）；安装/升级/卸载保留数据/重装/最终卸载全通过；`cleanup-stale-test-registration` 已加。C2 待证据 MD + 提交（工具仍 untracked）。
- **C3 10:42 实跑无效（不能当产品失败）**：baseline 微信 rect `[115,83,1106,718]`（10:42:07）→ setup.connected `[144,104,1383,898]`（10:42:16）外部改窗口；source-tree app（PID 34372）在 setup 后约 10:42:20 退出（dock_on.settings 已 checkbox/save missing，app_rect null，其后 app_alive=false、statuses 空），tray.restore 时微信 rect `[-30000,...]`；cleanup 最终 visible/rect_restored=true、无发送路径、editor 未误读。15 passed/25 failed 主要被“app 早退 + 外部几何变化”污染；C3 需捕获 app stderr、查早退原因、稳定环境重跑。**与 C1 A/B diag 两次异常退出可能同源**（UIA/Win32 驱动 source-tree Qt app 在隔离环境崩溃），建议合并排查。
- **交叉测试已修并验证**：Lead patch `test_action_dock_ownership` 的 `_foreground_ok`；我复跑 affected 子集 → **104 tests OK, exit 0**；单跑 actiondock 2 OK。
- **C1** 证据 MD 已写失败分类（driver 问题，A3 未证伪/证实）；`c1_ui_acceptance.py` 更新中；C1 不会写通过。
- C4/C5 已提交；P2-10 部分修复；P2-8 已修；C3 P2-8 修复后在有效状态标签上正常（setup.connected 有“正在本地识别 · 中央实时画面”），但 app 死后 statuses 空。

## 0.0 v13 状态更新（10:44）

- **全量会红 1 项（C4/C1 交叉作用域，必须修）**：`tests.test_action_dock_ownership.FinderNoSendTests.test_selected_result_never_presses_enter_into_existing_draft` 因 C4 `_foreground_ok` 校验未 patch → 我实测 `Ran 2 tests, FAILED (failures=1)`，`not_foreground`。修复：在该测试/`setUp` patch `wechat_find._foreground_ok=True`（与 C4 测试一致）；否则冻结全量必红。需 Lead 指定 C1 或 C4 修。
- **C1 4 次 UI 未切换 = driver 问题（非产品证据）**：C1 证据 MD §4.2 已分类：run1/2 settings 打不开（App 被覆盖/UIA 坐标不可靠）；run3 误把通用“失败/无法”当结果（误分类）；run4 find 未启动（点击落到 QQ，前台=QQ）。→ A3 产品路径未被证伪/证实；A/B 工具两次启动异常退出无 JSON，A/B 缺口未补。安全面 OK（no send/no screenshot；WeChat visible/rect unchanged）。
- **C2**：lifecycle6 加 `cleanup-stale-test-registration` 后 `install-old=True`，正在继续；旧 result JSON 仍 stale `ok=false`，等跑完覆盖。
- **C3**：首跑被 stale source-tree app（PID 26524，疑 C1 driver 残留）按设计 kill 并退出，需干净窗口后重跑。
- C4/C5 已提交；P2-10 部分修复；P2-8 已修。

## 0.0 v12 状态更新（10:43）

- **C3 首跑未进入场景**：`test-output/c3-runs/final-a3b2994/c3-evidence-20261018-104202.json`：preflight visible OK，但 `preflight.no_stale_app_instance=false`（stale source-tree app PID 26524）→ 脚本按设计 kill 该实例并安全退出，未操作微信、无场景结论；WeChat cleanup 显示 visible/rect restored。stale PID 26524 疑为 C1 UI 验收前一次失败运行的残留（C1 工具应清理自己拉起的 app 进程树）。**C3 需在无 stale 0.5.8 source-tree 实例后重跑**。
- **C2 安装链路已基本打通，收尾崩溃**：lifecycle5：reuse installers → install-old=True → installed-old version-json/smoke=True → upgrade-install-new-over-old=True → upgraded version-json/smoke/self-test=True → upgraded app reads preserved data=True；随后 `uninstall_setup` 崩溃 `PermissionError: ...\logs\inno-uninstall-after-upgrade.log`（父进程 stdout 日志与 Inno `/LOG=` 同文件冲突）。修复：父进程日志与 Inno log 分文件；重跑覆盖旧 `ok=false` result JSON。
- **C1**：4 次 UI 未切换；安全证据成立（no send/no screenshots；WeChat visible/rect unchanged）；待失败分类/根因。
- **C4/C5/P2-10/P2-8** 状态同 v11。

## 0.0 v11 状态更新（10:41）

- **C1 真实 UI 验收 4 次均未切换（C1 准入阻塞，非安全问题）**：`test-output/c1/c1_ui_acceptance_20261018-103454/103610/103716/103948.json` 全部 `connected=true`、`app_version=0.5.8`、`commit=48f30fb`、`dirty=true`、隔离 profile、`message_sent=false`、`screenshots_saved=false`、WeChat after visible 且 rect 与 preflight 一致；但 `switched=false/title_seen=false`，`after_find` category 为 blank/blank/`other_failure`/`unknown`。说明 readiness+retry 没有解决 App 内路径的 A3 失败（或失败原因未被 UI 正确分类）；C1 证据 MD §4 待填，完成前 **C1 不能标通过**。
- **C2 编译已修复但 install-old 仍失败**：`in_icons` 的 `#endif` 问题修好后，`compile-test-installer-old/new=True`；但 `install-old-0.4.3` 仍 exit 1（10.7s），`/LOG=` 指定的 `inno-install-old.log` 未生成、tail 空。可能为同 TEST_APP_ID 残留安装（Inno 认为已安装）或 installer 初始化失败；需 contracts 用 stdio probe/手动 `/LOG` 定位，必要时每次独立 AppId 或先清理测试卸载注册项。C2 仍 `ok=false`。
- **C3 证据 MD 已落盘**（`docs/windows/CODEX_TAKEOVER_C3_WINDOWS.md`）：preflight visible、0.5.8 source tree、运行快照 163c759（liteapp 与 a3b2994 一致）、隐私/恢复约束、6 项待运行结果；P2-8 已修（STATUS_FRAGMENTS + 编辑器外单行 TextControl）。真实运行待窗口释放。
- **C4** 已提交（`54436b6`，docs 48f30fb）；**C5** 完成。`P2-10` 已部分修复（见下）。

## 0.0 v10 状态更新（10:32）

- **C1 已按 P2-10 修改（部分修复，初审通过但仍有 1 个残留）**：`_escape_wechat_search` 现在：仅当 `window_status==visible` 才继续；前台不是微信时先 activate 一次、仍失败则跳过 ESC（避免发错应用）；ESC 后若窗口变 hidden 用 `SW_SHOWNOACTIVATE` 恢复；`_run_find_with_recovery` 用 `escaped` 标志保证 **UI 层最多一次 ESC**，最终失败只补一次。`test_find_pause_capture.py` 扩到 26.7KB。
  - **残留**：若 `wechat_find.find_conversation` 内部已先发 ESC（`title_mismatch`/`backend_error` 路径）并导致微信变 hidden，随后 UI 的 `_escape_wechat_search` 在入口 `window_status!=visible` 处**直接 return False**，不会执行恢复逻辑；`_finish_wechat_action` 又只在 `was_hidden=True` 时隐藏、不会恢复 → 失败后仍可能留下 WeChat hidden。建议：入口记录该动作开始时的可见性，若本次动作开始时 visible 而现在 hidden，则恢复 visible（或直接把“恢复可见”放在 finally，不依赖 ESC 分支）。
- **C2 仍未修复**：`c2-install-lifecycle3.log` 仍 `compile-test-installer-old/new=False`（ISCC `'endif' expected`，即我 10:31 消息中的 `[Icons]` 过滤把 `#endif` 删掉的 bug；`c2_upgrade_verify.py` 仍是 10:31 版本）。result JSON 仍 `ok=false`，仅 simulated uninstall/reinstall 步骤通过。
- **C3** `c3_real_window_verify.py` 更新到 59.5KB（新 primitives/editor 排除），P2-8 已在代码中修（`STATUS_FRAGMENTS` + `_main_editor_rect` + 只取编辑器外单行 TextControl），待真实运行证据。
- **C1 真实窗口 A/B/UI 证据**：10:29:50 遇 hidden 按规则停止，10:30:34 Lead 恢复后开始；结果仍未落盘（`test-output/c1/` 暂无文件），MD §4.2 待填。

## 0.0 v9 状态更新（10:31）

- **C2 最新失败已定位（可修）**：`compile-test-installer-{old,new}` 均 ISCC exit 2：`PreprocessingError ... 'endif' expected`。原因不是正则 replacement（已改用 lambda），而是 `c2_upgrade_verify.py` 的 `[Icons]` 过滤循环：进入 `[Icons]` 后把所有行都跳过，**把 `[Icons]` 区块末尾的 `#endif` 也删掉了**（原 .iss 是 `#if !Defined(VerificationBuild) / [Icons] / ... / #endif`），导致 `#if` 未闭合。修法：`in_icons` 循环遇到 `#` 预处理器行（尤其 `#endif`/`#if`）时恢复输出/停止跳过；或改用行级状态机保留 `#endif`。修复后重跑；当前 result JSON 仍 `ok=false`（compile 两步挂，后续 uninstall/reinstall 是 simulated fallback）。
- **C3 P2-8 已修（待运行验证）**：`status_texts` 只接受已知 `STATUS_FRAGMENTS` 协议/状态文本；`statuses()` 只取聊天编辑器区域外的单行 `TextControl`（新增 `_main_editor_rect`），聊天/建议文本即使含“微信/识别”也会被丢弃。
- **C1 真实窗口证据开始采集**：C1 MD 记录 10:29:50 preflight `window_status=hidden`（rect 屏幕外 -24000）→ 按 Lead 规则**立即停止、未操作窗口**；10:30:34 Lead 恢复 visible 后开始；源码树 commit 记录为 `54436b6`（含 C4 修复），隔离 LOCALAPPDATA；A/B/UI 结果待填。
- **C4 证据已随 `48f30fb` docs 提交**（含 Lead 前台守卫/OCR 发现）；C5 完成；`P2-10` 已转 C1；冻结后干净全量仍待执行。

## 0.0 v8 状态更新（10:31）

- **C1 新增 A3 根因修复已审（`_wait_wechat_render_ready` + `_run_find_with_recovery`）**：只做“单次 activate + 等 visible/foreground/非空帧（上限 2.5s）”，超时 **fail-open 进入 find_conversation（后者仍 fail-closed）**；失败只对 `search_not_found/result_not_found/capture_failed` **重试 1 次**，两次都只做 activate/capture/点击搜索/点击结果/头部校验，**无发送**；`work()` 已 try/finally，busy/capture 恢复。新增测试覆盖 readiness retry/超时/两次失败有界/不点发送。**新增 P2-10**：失败路径 `_escape_wechat_search` 用全局 SendInput 发 ESC，未校验前台=微信，且实测个别状态 ESC 会让微信主窗变 hidden；失败后可能留下“微信不可见”的用户状态/把 ESC 发给别的应用。建议 C1 在 ESC 后检查 `window_status`，必要时恢复 visible；ESC 前校验前台。
- **C4** 证据 MD 又更新（13349B，已提交后追加），`OCR_DEPENDENCY_AND_ALTERNATIVES_2026-10-18.md` 的修改经核对在 C4 允许写作用域内（CODEX_ASSIGNED_TASKS 明确允许）；C4 已提交 `54436b6`，我复跑 16 项 OK。
- **C2** 仍无修复版（`c2_upgrade_verify.py` 仍是含 `re.sub` replacement bug 的版本，result JSON 仍是 10:26 `ok=false`）；C2 可能成为唯一未完成项，需 Lead/contracts 尽快处理。
- **C3** 真实窗口时段刚开始（10:35–10:55），evidence/MD 未落盘；P2-8 未修。
- **C5** 已完成：Lead 记录 17 项合成单测 OK、evidence exit 0，已提交并同步 Codex 面板为 completed（合成≠真实）。

## 0.0 v7 状态更新（10:30）

- **C4 已提交 `54436b6`**（wechat_find.py + test_wechat_find.py + test_wechat_find_foreground.py + C4 证据 MD）：内容与我的初审一致；Lead 10:29 复跑 16 项 OK（我也复跑 16 OK）。
  - **新增 P3（证据卫生）**：提交的 `CODEX_TAKEOVER_C4_OCR.md` 含真实用户名路径（`C:\Users\16356\AppData\Local\Temp\...`）与 probe 输出文件名、真实联系人名（田子夜/郜亚禹）——名称是用户认可的验收目标，但**本机用户名/绝对路径**建议在最终提交/交付前替换为 `%TEMP%`；`%TEMP%` 里的 probe JSON 不在仓库，长期可复跑性依赖文档命令。
- **C5 已提交 `163c759`**（`test_c5_synthetic_inference.py`、`test_c5_synthetic_sources.py`、`tools/c5_synthetic_evidence.py`、C5 证据 MD）：我此前审过其 loopback/synthetic/temp 边界；证据 MD 结果段已从占位更新（commit 131 行版本），最终以 C5 完成后的真实输出为准。
- **C2 仍阻塞**：重跑仍在 `c2_upgrade_verify.py:164` `re.sub` replacement（含 `\c` 路径）崩溃；`c2-upgrade-install-result.json` 仍是 10:26 的 `ok=false`（install-old exit 1）。C2 agent 需修 regex + 加 `/LOG=` 后重跑；在此之前 C2 不能报通过。
- **C3** 仍在做工具自检（toggle/save/scroll validation 脚本在 test-output/c3-dev），真实窗口 evidence/MD 未落盘；statuses 全控件名（P2-8）未修。
- **C1** 真实 A/B/UI 验收待 10:55 窗口；`ui.py` 目前仍 dirty（C1 改动未提交），`test_find_pause_capture.py` 22.8KB。
- **C4 真实窗口已释放**（10:25 完成两个目标）；C3 窗口时段 10:35–10:55、C1 10:55 后，继续按排班观察。

## 0.0 v6 状态更新（10:29）

- **C4 已补前台守卫单测，复核通过**：`tests/test_wechat_find_foreground.py` 3 项（capture 非前台 None、find 首次点击前 `not_foreground` 不点击、结果点击前竞态失败）全部 OK；原 `test_wechat_find.py` 已 patch `_foreground_ok`，我复跑 `tests.test_wechat_find tests.test_wechat_find_foreground` → **Ran 16 tests, OK**。C4 证据 MD `CODEX_TAKEOVER_C4_OCR.md` 已审：真实窗口 10:25 两个目标（郜亚禹/田子夜）均 `ok=true`、头部 exact、无 Enter/无发送、窗口 visible/矩形未变；根因证据（WGC 看不到搜索结果下拉，屏幕抓取可见并精确匹配）充分；文档记录“个别状态 ESC 会把微信主窗变 hidden”的风险。**残留**：真实 probe 输出/脚本在 `%TEMP%` 而非仓库（可复跑性依赖文档命令）；C4 最终 MD 仍可补充“安装版未重打包”说明（已有）。
- **C2 重跑在编译阶段崩溃**：`c2_upgrade_verify.py:164-169` 用 `re.sub(pattern, replacement)`，replacement 里含 Windows 路径（`setups_dir` 的 `\c` 等）→ `re.error: bad escape \c`；这是新引入的 bug（不是安全/数据风险），需改用 lambda/replace。另已定位上一轮 `install-old-0.4.3 exit 1` 的可能原因：旧 installer `CloseApplications=yes` 在真实实例运行时静默 abort；工具已改为 `CloseApplications=no`、替换 VerificationBuild 块、去 [Icons]、唯一 AppId。修复正则并加 `/LOG=` 后重跑即可；**在 C2 证据 ok=true 前不能写“安装/升级/卸载通过”**。
- **C3** 仍待修 P2-8（statuses 全控件名）；真实运行 evidence/MD 尚未落盘。
- **C1** 真实 UI/A-B 验收仍待 10:55 窗口；`test_find_pause_capture.py` 已扩到 22KB。
- **C5** 结果段仍未更新。

## 0.0 v5 状态更新（10:28）

- **C4 P1-6 已修复（初审通过，残 TOCTOU/缺显式测试）**：`wechat_find.py` 新增 `_foreground_ok(hwnd)`（visible + `GetForegroundWindow()==hwnd`）；`capture_client_bgr` 在抓屏前校验，不满足返回 None → fail-closed；搜索框点击前/后（L291/294）与结果行点击前（L323）均校验，失败 `not_foreground`+ESC。`tests/test_wechat_find.py` 已更新（12 项 OK 的 Lead 日志）：精确结果行/消息预览不点击/搜索输入不点击/同名歧义 fail-closed/头部单字差异拒绝/read_title 精确接受/无 Enter。**残留**：check→SendInput 之间仍有极短 TOCTOU（可接受）；缺显式 `_foreground_ok`/capture-None 单测，建议 C4 补。
- **C2 实跑（10:21）结果：`ok=false`，10/11 步通过，`install-old-0.4.3` exit 1**：数据迁移链（old 写/old 读/new 读/new resave/new 迁移后读）、两份 installer retention、两份 test installer 编译均 OK；**真实 profile 前后快照完全一致（`real_profile_unchanged=true`）**；但旧 0.4.3 test setup 静默安装退出 1（installer 未捕获 `/LOG=`，tail 为空），安装生命周期矩阵不完整。C2 需加 `/LOG`、定位 install-old 失败原因并重跑；证据目前不能写“安装/升级/卸载通过”。
- **C3 更新**：`repo_git_info()` 记录 `pinned_commit=a3b2994` 与 `pinned_is_ancestor_of_head`，`setup.version` 现在接受“实际 commit 前缀或 pinned 是 HEAD 祖先”（docs-only 后代如 103f1e4 不再误报）；`source_root`/repo provenance 记录到证据；status_texts/全部控件名问题（P2-8）仍待修。
- **C1 更新**：`test_find_pause_capture.py` 已扩到 22KB（H3/H4/无 Enter/前台等更多回归）；`c1_ui_acceptance.py`/`c1_find_ab_diag.py` 更新；真实窗口 A/B/UI 验收待 10:55 后。
- **C5** 尚未更新结果段。

## 0.0 v4 状态更新（10:26）

- **C4 首版落盘并完成初审**：`wechat_find.py` 改为精确结果行匹配 + 同名歧义 fail-closed + 更紧头部二次精确校验（正面）；但 `capture_client_bgr` 改为 **PIL 屏幕抓取** 且缺少“前台必须是微信”校验 → 新增 **P1-6**（可能读取/点击非微信前台内容）；`tests/test_wechat_find.py` 尚未更新（测试缺口）。
- **C1 证据 MD 落盘**：`docs/windows/CODEX_TAKEOVER_C1_UI_FIND.md`：H3/H4 已写、明确“隔离 LOCALAPPDATA + 0.5.8 source tree、不点击/不 kill 已安装 0.5.7、不移动用户微信”，真实 A/B 与 UI 验收待 10:55 窗口；定向 52 项 OK。
- **C2 工具初审可行**：`c2_upgrade_verify.py` 用唯一 TEST_APP_ID（不碰真实注册）、隔离 profile + `--forbidden-profile`、probe 以 PYTHONPATH/cwd=归档代码运行；已核对 0.4.3（`5cafa1a`）确实已有 V2 history（APP_VERSION=0.4.3），迁移范围是 **V2→V2**（不是 0.2 V1→V2），证据需如实写；`NEW_COMMIT` 固定 `a3b2994`，冻结后应改为最终 commit 或允许 docs-only 后代。
- **C3 工具初审**：preflight 要求单主微信窗口且 visible 才继续；无 OCR/截图/发送；editor 只记长度；status 白名单短标签；cleanup finally 恢复 WeChat 可见+矩形并关 app。残留 P2-8（status_texts 遍历全控件，短聊天文本若含白名单词可能进 evidence）、P2-9（popup 步骤会真实右键点 WeChat 画面中心，只 ESC/关闭不点菜单；版本常量固定 a3b2994 与当前 HEAD 103f1e4 可能不符）。

## 0.0 v3 状态更新（10:22）

- **独立全量已跑（Lead 指定，WIP/dirty）**：HEAD `103f1e4` + 工作区（dirty）→ `Ran 713 tests, OK (skipped=37), exit 0`，119.668s；dirty 10→13（运行中有 C1/C2/C3 新文件落盘）。详见 §7。
- **H3/H4 已由 C1（windows）实现，初审判定通过**：
  - H3：`ui.py` 新增 `normalize_title_for_match()`/`titles_match()`（精确规范化相等，非子串）；发送前先截屏 OCR 头部标题，不匹配立即 `ValueError`，在任何点击/键入前取消；`tests/test_find_pause_capture.py` 新增 `TitleExactMatchTests`（`田子夜2`/`x田子夜`/`田子`/空串全部拒绝，精确匹配才发送）。
  - H4：find/send 的 `work()` 整体包 try/finally（`_prepare_wechat_for_action` 进 try，`_finish_wechat_action` 再包一层异常保护）；新增 `PrepareFailureRestoresTests` 覆盖 prepare 抛 `RuntimeError` 时 `wechat_busy=False` 且 capture 重连、send_message 未被调用。
  - 待最终（冻结后）全量 + C1 真实 UI 证据一起复核。
- **新产物已落盘待审**：C1 `tools/c1_find_ab_diag.py`/`c1_ui_acceptance.py`（初查：隔离 LOCALAPPDATA、auto_send_allowed=False、证据记录 app_version/profile、不点发送；`c1_find_ab_diag` 统计 `unexpected_enter` 并在结束 ESC 清理）；C2 `tools/c2_upgrade_verify.py`（初查：0.4.3→0.5.8、隔离 profile、`--forbidden-profile` 断言真实 profile 未变）；C3 `tools/c3_real_window_verify.py`（待逐行审真实窗口安全）。

## 0.0 v2 状态更新（10:18）

- **H1 版本已解决**：Lead 已将 0.5.8 review fixes 提交为 `a3b2994`；接管目标版本统一为 **0.5.8 source tree**（安装版仍 0.5.7，待接管修复齐后 clean rebuild/install）。所有 agent 被要求证据写“版本 + commit”。→ P1-1 降为“已解决，保留安装版/源码版本标注要求”。
- **H2 已澄清**：Lead 跑全量的命令确认为 `unittest discover -s tests -t . -q`；10:16 结果 **713 tests OK、37 skipped、exit 0**（日志 `windows/lite/test-output/lead_review_codex_0.5.8.log`）。我的独立复跑仍等 Lead 通知。
- **H3/H4 已转 windows/C1 本轮修复**：发送前标题改规范化精确相等；`work()` 包 try/finally + prepare 异常测试。→ 等 windows 改完后作为复核重点（见 P1-3/P1-4）。
- **H5/H6 已转告 takeover-wintest / takeover-ocr**：C4 保留“结果点击后不按 Enter”并补断言；C3 证据用 0.5.8 source tree、`test-output/` 不复制进 docs。
- **新增/更新产物（本报告 v2 已审）**：C5 `tests/test_c5_synthetic_inference.py`/`test_c5_synthetic_sources.py` + `tools/c5_synthetic_evidence.py` + 证据 MD（结果占位待填）；C2 `tools/c2_profile_probe.py`；C3 `test-output/c3-dev/*` 多个隔离 app 交互脚本；C4/C1 尚未落盘。

> 说明：C1/C2/C3/C4 最终产物（真实窗口脚本/升级脚本/OCR 改动/真实 UI 证据）在本稿时仍未落盘；本报告为**增量复核**，后续按 §8 清单继续更新。


## 0. 结论摘要

| 级别 | 数量 | 摘要 |
| --- | --- | --- |
| **P1 高风险** | 6（1 已解决/4 已修待终审/1 跟踪） | P1-1 版本口径已解决；P1-2 全量隔离依赖 `-t .`（已确认）；P1-3 发送标题精确匹配（C1，初审通过）；P1-4 busy 清理（C1，初审通过）；P1-5 真实 profile 旧 App 实例仍在运行（C1 隔离 profile+0.5.8 source tree 规避）；**P1-6 C4 屏幕抓取前台校验已补（`_foreground_ok` + 抓屏/点击前检查，fail-closed），初审通过；残 TOCTOU/缺显式测试** |
| **P2 中风险** | 8 | 查找去掉 Enter 后真实微信兼容性未验证；剪贴板被覆盖；发送验证 `marker=reply[:6]` 过弱；tray 恢复/隐藏副作用；C3 UIA 证据含窗口矩形/路径需脱敏；`test-output/` 旧开发脚本会读真实 profile（禁止复制进交付）；C2 探针信任 `LOCALAPPDATA` 且旧版夹具需由旧代码生成；**P2-10 C1 失败路径 ESC 可能隐藏微信/发给其他前台应用** |
| **P3 低风险/建议** | 5 | 文档 HEAD/候选版本统一（v2 已部分更新）；tests 临时 profile 目录未清理；全局 patched TextTestRunner（可接受但需知晓）；严格模型 ID/读取校验的 UX 回退需提示；证据文档版本/SHA 统一模板 |
| 正面结论 | — | 已落盘的 C1–C5 早期产物中**未发现**真实聊天内容/截图、真实凭据、真实微信自动发送；C3 使用隔离 LOCALAPPDATA 且 auto_connect=False（不连接微信）；C5 使用 127.0.0.1 随机端口 + synthetic token + 临时 HistoryStore；`test-output/` 不纳入 git；`wechat_find` 已移除 Enter（正确方向） |

> 说明：C1–C5 多数任务的最终产物（真实窗口脚本/升级脚本/OCR 改动/证据 MD）在本次初稿时尚未落盘；本报告为**初稿 v1**，后续按 §8 的清单增量更新。

## 1. P1 高风险问题

### P1-1 版本/候选包口径冲突：真实 UI 证据可能测的是 0.5.7，而源码是未提交的 0.5.8

**证据**
- `docs/windows/CODEX_TAKEOVER_2026-10-18.md:15-17`：写候选包 **0.5.7**，SHA `35C37FEC…`，HEAD `2dd7d0b`、最后代码提交 `32fc8f2`。
- 工作区实际：
  - `windows/lite/installer/ChatAssistantLite.iss:2` `AppVersion "0.5.8"`；
  - `windows/lite/liteapp/build_info.py:24` `APP_VERSION = "0.5.8"`；
  - `windows/lite/liteapp/ui.py:969` 窗口标题 `… 0.5.8`；
  - 当前 HEAD 实际为 `e365c2e`（不是 `2dd7d0b`）；
  - `windows/lite/dist-release/ChatAssistantLite-Setup.exe` 已重建为 0.5.8（进度文档记录 SHA `C3E34C98…`），但**尚未覆盖安装**，本机 `E:\ChatAssistantLite` 仍是 0.5.7。
- C3 早期证据 `windows/lite/test-output/c3-dev/dump_uia.py:17` 启动的是 `E:\ChatAssistantLite\ChatAssistantLite.exe`（0.5.7，dump 中标题也确认 0.5.7）。

**风险**
1. C1（App 内点击自动查找）与 C4（真实 OCR 查找）若用已安装 0.5.7 做“真实 UI/OCR 证据”，测不到工作区未提交修复：查找结果点击后**不再按 Enter**（`liteapp/wechat_find.py:189-191`）、远程读取严格校验（`reading.py:49-63`）、模型目录 `id` 修复（`analysis.py:141`）等。证据会与源码脱节。
2. C2 升级/卸载验收目标不清：0.4.x→0.5.7 还是 0.4.x→0.5.8？SHA/版本写错会导致验收矩阵失真。
3. 若 Lead 最终提交工作区后用旧 SHA/版本写证据，或构建后未更新文档，复核/用户签收会拿到错误结论。

**建议（Lead 裁决）**
- 立刻统一“本轮候选版本”：推荐 **0.5.8**（工作区已是 0.5.8，构建产物已存在）；在 `CODEX_TAKEOVER`/`UNFINISHED`/验收矩阵统一写 `0.5.8 + 新 SHA`，C2 目标改为 `0.4.x → 0.5.8`。
- C1/C4 任何真实 UI/OCR 证据必须写明“应用版本 + 安装包 SHA + 是安装版还是源码运行”；若只能用已安装 0.5.7，需在证据 MD 标注“未覆盖 uncommitted 修复：查找后不再按 Enter、远程读取严格校验”，并转 Codex 复验。
- 不要用旧 0.5.7 的 668 项/旧证据冒充本轮 0.5.8 结果。

**状态（10:18 更新，已解决）**
- Lead 已将原未提交的 0.5.8 review fixes 提交为 **`a3b2994`**，接管期目标版本统一为 **0.5.8 source tree**；所有 agent 被要求证据写“版本 + commit”。
- 仍需保留的要求：① 真实窗口/UI 证据必须写清“0.5.8 source tree @ `<commit>`”或“安装版 0.5.7 + SHA”，二者不可混写；② 安装版仍为 0.5.7，接管修复齐后需 clean rebuild 再安装，最终验收矩阵/HASH/README 同步更新；③ 不要把 0.5.7 的 668 项/旧 SHA 当作 0.5.8 证据。

### P1-2 全量测试命令必须带 `-t .`，否则测试 profile 隔离失效

**证据**
- `windows/lite/tests/__init__.py:26-27`：在 **tests 作为 package 导入时** 用 `tempfile.mkdtemp()` 把 `LOCALAPPDATA` 指到临时目录；文件头注释明确记录过“测试配置覆盖了真实保存的模型”的既往事故。
- `python -m unittest discover -s tests`（不带 `-t .`）默认把 `tests/` 当 top-level，模块以 `test_xxx` 导入，**不会导入 `tests` 包** → 隔离代码不执行。
- 许多测试会直接调用 `settings.save()` / `HistoryStore()` 默认路径；无隔离时有写真实 `%LOCALAPPDATA%\ChatAssistantLite` 的风险。

**建议**
- 最终全量统一用：`G:\codex\wechat-lite-venv\Scripts\python.exe -m unittest discover -s tests -t . -q`（与接管文档 §全局命令一致）。
- 跑前确认工作区冻结（各 agent 已停止改动）、记录 exit code、通过数、跳过数；若使用其他命令（如 pytest/IDE）需确认 `tests/__init__` 已导入。
- 可选加固（非本任务范围）：`tests/__init__.py` 注册 atexit 删除临时 profile 目录；或在 `settings.data_dir()` 测试模式下 fail-fast 断言路径在临时目录下。

### P1-3 发送前标题校验是子串匹配，存在发到错误会话的风险

**证据**
- `windows/lite/liteapp/ui.py:2394-2396`：
  ```python
  current = verifier()                       # 当前会话标题的 OCR
  if not current or title not in current:    # ← 子串匹配
      raise ValueError("当前会话标题与目标不一致，已取消发送。")
  ```
- 对比 `wechat_find.py:227-231` 的精确规范化相等（注释明确“切错人比失败更糟”）。

**风险**
- 若当前 OCR 标题是目标标题的**超串**（同名/同前缀联系人，如 `田子夜` vs `田子夜2`、群名包含联系人名），`title in current` 会通过校验，随后 `send_message` 可能把建议发到错误会话。发送是不可撤销动作，属于最高安全级别。
- 该问题在 0.5.8 未提交改动中即存在，与本轮 C1 改同一文件，若 C1 只关注“查找”而未收敛发送前校验，会被漏掉。

**建议**
- 发送前复用与查找一致的规范化精确相等（或双向：`normalize(current) == normalize(title)`；必要时再要求 find 结果 revision/token 未变），不满足即取消发送并提示。
- 补测试：`title="田子夜"`、当前标题 OCR `"田子夜2"` / `"客户田子夜"` 时必须拒绝发送；并断言 `wechat_actions.send_message` 未被调用（可用 mock）。

**状态（10:22，已由 C1 实现，待最终复核）**
- `ui.py` 新增 `normalize_title_for_match()`/`titles_match()`（去空白/常见标点后 **精确相等**，`casefold`）；`send_reply_to_wechat` 先 `_capture_client_frame` + `read_title(header)`，`titles_match` 失败立即 `ValueError("当前会话标题与目标不一致，已取消发送。")`，**在任何点击/键入之前**取消。
- `tests/test_find_pause_capture.py` 新增 `TitleExactMatchTests`：`titles_match` 正向/负向（`田子夜2`、`x田子夜`、`田子`、空串拒绝；`田子夜：`/空白通过）；`test_send_rejects_substring_title_and_never_touches_wechat` 断言 `send_message`/Reader 均未被调用；`test_send_accepts_exact_title` 覆盖正常路径。
- 初审通过；剩最终冻结全量 + 真实 UI 证据（版本/commit）复核。

### P1-4 find/send 工作线程异常时 `wechat_busy`/capture 可能永久卡住

**证据**
- `windows/lite/liteapp/ui.py:2341`（find）与 `:2388`（send）：
  ```python
  def work():
      was_hidden = self._prepare_wechat_for_action(hwnd)   # ← 在 try 之外
      try:
          ...
      finally:
          self._finish_wechat_action(hwnd, was_hidden)
  ```
- `wechat_busy` 在 UI 线程置 True（`:2332`/`:2380`），只在 `wechat_action_done` 槽（`:2449`）清 False；若 `work()` 在发送 `wechat_action_done` 之前异常终止（`_prepare_wechat_for_action` 抛非 OSError/ValueError、`threading.Thread.start()` 失败、`stop_capture()`/信号 emit 意外抛错），则：`wechat_busy` 永久 True → 自动查找/发送按钮只 return；capture 已被 stop 且不会重连；`sync_dock` 因 busy 会一直提前 return（`:1448-1450`）。
- `_prepare_wechat_for_action` 目前捕获 `(OSError, ValueError)` 后返回 False（`ui.py:2242-2258`），但不是所有异常都被覆盖（例如 `discovery.find_wechat_windows` 内部 TypeError、ctypes 参数异常等）。

**建议**
- 把 `work()` 整体（含 `_prepare_wechat_for_action`）包进 `try/finally`；`finally` 里保证 `wechat_busy=False` 只能由 UI 线程/槽执行（可在 finally 里通过 `bus` 发一个 action_done 兜底，或加 watchdog 超时）。
- 加测试：mock `_prepare_wechat_for_action` 抛 `RuntimeError` 时仍恢复 `wechat_busy=False` 且 `connect_item` 被调用；当前 `test_find_pause_capture.py` 只覆盖正常与 `find_conversation` 抛 RuntimeError，不覆盖 prepare 异常。

**状态（10:22，已由 C1 实现，待最终复核）**
- find 与 send 的 `work()` 均已改为：`was_hidden=False`；`try: was_hidden = _prepare_wechat_for_action(...); ...`；`finally: try: _finish_wechat_action(...) except Exception: pass`。prepare 异常不再绕过 finally。
- `tests/test_find_pause_capture.py` 新增 `PrepareFailureRestoresTests`：mock `_prepare_wechat_for_action` 抛 `RuntimeError` 后断言：find 路径 `find_conversation` 未调用、`wechat_busy=False`、`connect_item` 重连、notice 含“自动查找对话失败”；send 路径 `_capture_client_frame`/`send_message` 未调用、busy 恢复、capture 重连。
- 初审通过；剩最终冻结全量复核。

### P1-5 真实 profile 的已安装 App 实例仍在运行（C1/C3 真实 UI 验证前需处理）

**证据（10:18 实测）**
- 3 个 `ChatAssistantLite.exe` 进程自 09:56:53 起持续运行：PID 5652（主程序，真实 profile）、PID 34388（`--recovery-guard --data-dir C:\Users\16356\AppData\Local\ChatAssistantLite`）、PID 23020（`--multiprocessing-fork`，父进程 5652）。主程序命令为 `E:\ChatAssistantLite\ChatAssistantLite.exe`（**已安装 0.5.7**，非 0.5.8 source tree）。
- 真实 profile 目录已有 `instance.json`/`instance.lock`/`recovery-guard.json`（09:56 更新）。

**风险**
- C1 需要通过“App 内点击自动查找”验证 UI 路径；若直接启动已安装 0.5.7（或任何同 data_dir 的新进程），单实例机制会**只激活这个旧实例**（0.5.7 旧代码、真实 profile、可能仍连接真实微信），证据既不是 0.5.8 source tree，也可能操作真实 profile/真实会话。
- 若 C1 使用隔离 LOCALAPPDATA + 0.5.8 source tree，则不受该实例影响；但必须确认启动的进程确实是隔离 profile，且旧实例不会抢回前台/干扰 UIA 查找。
- 该旧实例同时占着真实 profile 的恢复守护（recovery-guard），若 C1/C3 脚本误用真实 data_dir 会被单实例/恢复守护干扰。

**建议**
- C1/C3 真实 UI 前：由用户/Lead 正常关闭这个 0.5.7 实例及其 recovery-guard 子进程；或 C1 明确用隔离 profile + 0.5.8 source tree 启动独立进程并在证据中记录 PID/版本/profile 路径。
- 不要在真实 profile 上做任何自动点击/停靠/查找实验；C1 的 A3 人工路径若必须用真实安装版，应单独列为“0.5.7 安装版人工验收”，与 0.5.8 source tree 自动证据分开。

### P1-6 C4 屏幕抓取缺少“前台=微信”校验，可能读取/点击非微信前台内容

**证据（`windows/lite/liteapp/wechat_find.py`，C4 10:24 版）**
- `capture_client_bgr()`（L74-121）从 WGC 主窗口抓取改为 **PIL `ImageGrab.grab(bbox=窗口矩形, all_screens=True)`**（L100/L109），理由：微信 4.x 的搜索结果下拉层是独立分层表面，WGC 抓主 HWND 看不到。
- `find_conversation` 只在开头 `if not backend.activate(hwnd): return fail("activate_failed")`（L252），**没有**校验 `GetForegroundWindow()==hwnd`、也没有校验窗口仍 visible；注释只说“occluded/covered windows are not usable”，运行期不拒绝。
- 屏幕抓取到的是 WeChat 窗口矩形处的**屏幕像素**：若激活被前台锁/其他进程阻止、或 WeChat 被其他窗口（包括 ChatAssistantLite 自己的镜像窗口）遮挡，抓到的会是别的应用的画面。随后 `Win32Backend.click` 用 `SendInput` 按屏幕坐标点击；若前台其实是 App 自己，点击可能落到 App 控件（包括“发送到微信”所在区域）；这是不可接受的错误读取/错误点击面。
- 旧 WGC 抓取不会包含其他应用内容；此风险是 C4 本次改动新引入的。

**建议**
- 在 `find_conversation` 每次抓屏/点击前（至少 `capture_client_bgr` 入口和结果点击前）校验：`discovery.window_status(hwnd) == "visible"` 且 `GetForegroundWindow() == hwnd`；不满足立即 `fail("not_foreground")` + ESC，绝不回退屏幕抓取（回退 WGC 看不到下拉，无法安全定位结果）。
- 记录实际抓屏路径（screen/WGC fallback）到 evidence（布尔/枚举即可），便于审计；OCR 行文本不要写入证据。
- 若要保留“被遮挡时抓取”能力，也只能在确认 WeChat 仍在前台且未被遮挡（`WindowFromPoint`/z-order 检测）时使用，否则 fail-closed。
- 建议 C4 加对应测试：activate 成功但前台不是 WeChat / window_status 非 visible 时不得调用 ImageGrab、不得点击，必须 ESC fail-closed；ImageGrab 用 mock，不得真实截屏。

**状态（10:28，已修复，初审通过）**
- 已新增 `_foreground_ok(hwnd)`（L63-72：visible + `GetForegroundWindow()==hwnd`）；`capture_client_bgr` 入口校验（L90），非前台返回 None → find 首帧 `capture_failed` / 后续 `result_not_found` fail-closed，不会抓其他窗口。
- 点击前校验：搜索框点击前/后（L291/294，失败 `not_foreground`，后者不 ESC 因为未点结果）、结果行点击前（L323，失败 ESC + `not_foreground`）。屏幕抓取只在 `_foreground_ok` 通过时发生。
- 残留：① check→`SendInput` 之间存在极短 TOCTOU（可接受，但可再加一次点击前 window_from_point 校验）；② 缺显式单测覆盖“非前台 → capture None / not_foreground / 不点击”；建议 C4 补（ImageGrab mock，不真实截屏）。

## 2. P2 中风险问题

### P2-1 `wechat_find` 移除 Enter 后，某些微信版本可能完全不工作（需真实版本验证）
- `wechat_find.py:186-219`：点击结果后不再按 Enter，改为最多 4 帧头部校验，失败 ESC。设计正确（避免 Enter 发送草稿），但若目标微信版本必须按 Enter 才真正切换/关闭搜索层，则 UI 自动查找会从“偶发失败”变为“稳定失败”。
- 这是可接受的 fail-closed 行为，但 C1/C4 必须在**真实目标版本**上确认：查找成功、失败时 ESC 且不发送；若该版本必须 Enter，需作为已知限制写入证据（或改为点击“结果行”后只发送安全的关闭搜索热键而非 Enter）。
- 建议 C4 补回归断言（目前新测试 `tests/test_action_dock_ownership.py:39-46` 已有“点击结果后未按 0x0D”的用例，保留并覆盖失败路径）。

### P2-2 剪贴板被覆盖（用户内容丢失）
- `wechat_find.py:164-167` 用 `set_clipboard_text(title)` 粘贴联系人名；`wechat_actions.Win32Backend.set_clipboard_text`（`wechat_actions.py:358-392`）明确不读取旧剪贴板、直接清空写入。若用户剪贴板里是密码/重要文字，会被永久覆盖。
- 建议（非阻塞）：动作前保存/动作后恢复剪贴板（或至少文档提示）；若担心读取剪贴板内容本身有隐私风险，可只记录“将覆盖剪贴板”的 UI 提示。

### P2-3 发送验证 `marker = reply[:6]` 过弱
- `ui.py:2417` 取回复前 6 字符作为“已发送”标记，`verify_sent` 只要求 body 中出现 marker 且输入框不出现 marker（`:2419-2432`）。若回复很短（≤6 字）或前 6 字是常见词（“好的，”“收到，”），可能与历史气泡/其他文本误匹配，导致“已发送+已通过双条件验证”的假阳性。
- 建议：marker 使用完整规范化回复（或 ≥8–12 字且含独特字符）；验证要求完整匹配且输入框/发送区无残留；短回复时降级为“已发送但需人工确认”，不作为 verified 成功。

### P2-4 tray 恢复/隐藏的副作用需 C3 覆盖
- `ui.py:2242-2258` `_prepare_wechat_for_action`：窗口不可见时调用 `discovery.restore_window(target, activate=True)` 或 `ShowWindow(hwnd,4)`；`_finish_wechat_action`（`:2260-2271`）在动作后 `ShowWindow(hwnd,0)` 隐藏窗口。若用户原本是“最小化”而非“托盘隐藏”，状态判断为非 visible 时也会走 restore→动作→hide，可能改变用户窗口状态。
- C3 的真实窗口脚本需验证：动作前状态（窗口/最小化/托盘）→ 动作后恢复原状态；失败路径也要恢复；不得把用户窗口永久隐藏/移动。

### P2-5 C3 早期 UIA 证据含本机窗口矩形/安装路径，作为 docs 证据需脱敏
- `test-output/c3-dev/dump_uia.py:17` 硬编码 `E:\ChatAssistantLite\...`；`uia_dump.json` 含窗口 rect、pid、hwnd、控件名/尺寸。已检查控件名只有应用 UI 文案（无真实联系人/聊天正文），但矩形/pid/hwnd/用户名路径属于本机信息。
- 建议：docs/验收矩阵只引用“合成/脱敏后的结论 + 脚本路径”，不要把 `uia_dump.json` 原文或含 `C:\Users\<user>` 的日志复制进 git；`test-output/` 已 gitignore（`.gitignore:13`），保持。

### P2-6 `test-output/` 中既有脚本会读取真实 profile/DPAPI 凭据，禁止复制进交付证据
- 例如 `test-output/diag_subscription.py:6,13`（`settings.load()`、打印 has_refresh_token 布尔）、`test-output/inspect_profile.py:27`（protected_subscription）、`test-output/verify_token_refresh.py:11,17`（settings.load + subscription.access_token）。
- 这些是历史开发脚本，未发现打印令牌值，但位于证据目录且可读取真实 profile；若被复制/打包/上传会带来凭据风险。建议 Lead 在最终证据清单中排除 `test-output/` 与 `_reports/`，或先人工脱敏审核。

### P2-7 C2 探针工具信任 `LOCALAPPDATA`，且旧版夹具必须由旧代码生成

**证据（`windows/lite/tools/c2_profile_probe.py`，10:16 版）**
- `_profile_dir()`（L58-64）：`local = Path(os.environ.get("LOCALAPPDATA", ""))`，非空即 `local / "ChatAssistantLite"` 并 `mkdir`；只有 `LOCALAPPDATA` 为空时才 fail-closed。若 C2 driver 忘记设置隔离 `LOCALAPPDATA`（或继承到真实用户环境），探针会**直接写真实 profile**（synthetic 内容也一样覆盖真实设置/历史，属数据破坏风险）。
- `write_profile()`（L93-95）调用 `settings.save(config, profile)` 并 `history.HistoryStore(profile/"memory.sqlite3")`；`read_profile()`（L181-203）同理。探针使用 `from liteapp.history_models import ImportSource, ImportedRecord`（V2 API）——若 driver 把 `PYTHONPATH` 指到归档的 **0.4.x** 代码，`history_models` 不存在，write/read 会失败；若用当前 0.5.8 代码写 0.4.x 夹具，则不是真正的旧 schema 升级测试。
- 探针把 `profile_dir` 绝对路径、synthetic settings/subscription 摘要写入结果 JSON（无真实凭据，但路径属本机信息）。

**建议**
- driver（`c2_upgrade_verify.py` 等待落盘）必须：先创建隔离 WorkDir，显式设置子进程 `LOCALAPPDATA=<WorkDir>/profile`，校验 profile 路径前缀在白名单内，再运行探针；任何“真实 %LOCALAPPDATA%”路径直接拒绝。
- 0.4.x 旧数据夹具必须由 **归档 0.4.x 代码**（或按 0.4.x schema 直接构造）生成，再用 0.5.8 读取/迁移；证据里记录 `from_version`/`migrated` 与 schema 版本（`last_migration`），避免用当前代码冒充旧数据。
- 结果 JSON 只进 `test-output/`（gitignored），不进 docs/发布证据。

### P2-8 C3 evidence 的 status_texts 仍遍历全部控件名，存在少量聊天文本入证据的可能

**证据（`tools/c3_real_window_verify.py`，10:21 版）**
- `statuses()`（L332-333）取 `status_texts([self.name(item) for item in self.walk()])`，即**遍历全部 UIA 控件名**；`status_texts`（L124-135）只保留 ≤80 字、且包含 `STATUS_HINTS`（“微信/停靠/只读/画面/最小化/托盘/弹窗/识别/窗口/连接/等待/保存/启动/设置已/当前会话/未连接/正在”）的文本。
- 正常情况只会命中状态标签；但若某个聊天/OCR 文本控件名恰为 ≤80 字且含上述词（例如“微信已连接…”类用户消息），会被写入 evidence。这与“never chat content”的声明存在残余差距。

**建议**：`statuses()` 只遍历工具栏/状态栏/对话框等已知状态控件（按 ControlType + 名称前缀/位置过滤），或明确排除中央“识别到的聊天文字/聊天画面”子树；测试里增加“包含 hint 的聊天文本不会被 `status_texts` 采纳”的用例。

### P2-9 C3 popup 步骤真实右键点击微信画面中心，且版本常量固定旧 commit

**证据**
- `step_popup()`（L838 起）在 `_wechat_center_point()`（L833，默认 60% x / 45% y）对真实 WeChat 窗口 `post_mouse(..., right=True)` 打开真实右键菜单，随后只用 ESC / WM_CANCELMODE / WM_CLOSE 关闭，**不点击任何菜单项**；证据只记录 popup hwnd/class/rect_size/布尔。
- `SOURCE_TREE_COMMIT = "a3b2994"`（L47）固定；`read_version()`（L633 附近）要求 `version["git"]["commit"]` 前缀等于它。当前 HEAD 已到 `103f1e4`（docs-only 提交），若 C3 现在运行，setup.version 会因 commit 前缀不符而失败。

**建议**
- 在证据/文档中明确 popup 是“真实菜单打开后仅 ESC 关闭、不选择菜单项”的认可场景，并确保菜单出现期间不做任何键鼠输入到菜单项；若用户窗口右侧/中心不是安全空白区域，提供 `--popup-point`/跳过开关。
- 运行参数化最终 commit（如 `--expected-commit` 传最终 HEAD，允许 docs-only 后代）或在冻结后更新常量；否则会误报 setup.version 失败。

**状态（10:28，版本检查已改善）**：C3 已加 `repo_git_info()`（记录 `head`/`pinned_commit`/`pinned_is_ancestor_of_head`），`setup.version` 接受“实际 commit 前缀 **或** pinned commit 是 HEAD 祖先”，因此 `103f1e4`（docs-only 后代）不会误报；仍建议证据写清 pinned/head 两者。P2-8（status_texts 全控件）待修。

### P2-10 C1 失败路径 ESC 可能隐藏微信窗口或把 ESC 发给其他前台应用

**证据（`windows/lite/liteapp/ui.py`，C1 10:29 版）**
- `_escape_wechat_search(hwnd)` 直接用 `Win32Backend().key_combo((0x1B,))`（SendInput 全局键盘）发送 ESC，**没有**校验当前前台仍是 WeChat，也没有在 ESC 后检查 `window_status`。
- `find_wechat_conversation` 失败路径：`_run_find_with_recovery` 在重试前发一次 ESC，最终失败后**再发一次** ESC；C4 证据（`CODEX_TAKEOVER_C4_OCR.md §2.2/§6`）实测“个别状态下（会话已打开、焦点在消息区）ESC 会让微信主窗变为 hidden（IsWindowVisible=false，矩形不变），已人工恢复 visible”。
- 若用户原本是 visible 的微信窗口，一次失败自动查找后可能变成 hidden；`_finish_wechat_action` 只在 `was_hidden=True` 时隐藏窗口，不会把“ESC 导致的 hidden”恢复。另一个风险：若失败瞬间用户切到别的应用，ESC 会被发给该应用（可能关闭对话框）。

**建议**：ESC 前校验 `foreground_window()==hwnd`（不是就跳过 ESC 或先 activate），ESC 后校验 `window_status(hwnd)`，若从 visible 变为 hidden 且本次不是用户原本隐藏状态，则 `ShowWindow(SW_SHOWNOACTIVATE)`/restore 回 visible；避免失败路径重复 ESC（一次即可）。C1 真实 UI 验收需记录失败后的窗口状态（visible/rect 保持不变）。

**状态（10:32，已部分修复）**：`ui.py` 已改 `_escape_wechat_search`：入口 visible 检查、前台不是微信先 activate 一次（仍失败则跳过 ESC）、ESC 后 hidden 时 `SW_SHOWNOACTIVATE` 恢复；`_run_find_with_recovery` 用 `escaped` 标志保证 UI 层最多一次 ESC，最终失败只补一次。**残 留**：若 `find_conversation` 内部已 ESC 并把窗口变 hidden，UI `_escape_wechat_search` 入口直接 `return False`，不会执行恢复；见 v10 建议。需 C1 真实失败路径验收确认 final state visible。

## 3. P3 低风险/建议

1. **文档 HEAD/版本已部分统一（v2）**：Lead 已提交 `a3b2994` 并在接管文档/证据要求中统一 0.5.8 source tree；`CODEX_TAKEOVER`/`CODEX_ASSIGNED_TASKS` 里的 `2dd7d0b`/`dfde965`/`32fc8f2` 等旧引用建议最终交还前一并清理。
2. **工作区状态（v2）**：`a3b2994` 提交后工作区仅剩 C1–C5 接管产物（C5 tests/docs、C2 probe tool、C3 `test-output/`、本报告）；Lead 按任务拆分提交/提交前全量测试即可。
3. **tests/__init__.py 全局 patched `TextTestRunner.run`**：只加 finally 清理 Qt 顶层控件，属于测试基础设施；但会影响所有 unittest 运行（包括第三方脚本），建议保留注释并在最终测试报告注明。临时 profile 目录未删除（仅泄漏 %TEMP%，无隐私内容）。
4. **严格模型 ID 修复的 UX 回退**：`analysis.py:141` 只接受 `id`；若某些服务商只返回 `name`/`model` 字段（旧代码兼容），现在会得到空列表。需要 UI 错误提示“服务未返回可用模型 ID”，并保留手动输入；实际服务商字段兼容性需真实 Key 验证（与进度文档一致）。
5. **真实读取校验严格化**：`reading.py:49-63` 拒绝空标题/未知 side（大小写 `ME` 也拒绝），会让部分模型输出整体失败并回退本地 OCR——这是安全优先的正确取舍；验收时需在真实多模态服务上确认“误拒率”可接受。
6. **C3/C4 真实窗口脚本的清理责任**：脚本必须 finally 恢复窗口可见性/位置/停靠开关、关闭自己创建的助手窗口、结束拉起的 app 进程；否则会留下孤立进程/隐藏的微信窗口。待脚本落盘后逐行复核。
7. **C2 升级脚本安全要求**：必须使用隔离 `WorkDir`/`/DIR`/隔离 `LOCALAPPDATA`，禁止对 `E:\ChatAssistantLite`、真实 `%LOCALAPPDATA%\ChatAssistantLite` 执行安装/卸载/删除；卸载验证只针对隔离目录。待脚本落盘后逐行复核。

## 4. 基线 `a3b2994`（0.5.8）改动审阅

> `a3b2994` 即原未提交的 0.5.8 review fixes（22 文件、+764/−55）。下表为只读复核意见；接管期 C1 还会在此基础上改 `ui.py`。

| 文件 | 改动摘要 | 复核意见 |
| --- | --- | --- |
| `liteapp/wechat_find.py` | 删除结果点击后的无条件 Enter；失败 ESC；保留头部精确匹配 | **正确方向**（避免 Enter 发送草稿）；见 P2-1 真实版本兼容性验证 |
| `liteapp/ui.py` | 预览缩放/滚动、模型目录下拉、dock 默认关闭、`wechat_busy` 期间 `sync_dock` 跳过、远程读取过期回退、标题 0.5.8 | 见 P1-3/P1-4/P1-5；预览缩放为离屏合成验证，需真实 DPI 人工确认 |
| `liteapp/reading.py` | 远程读取要求标题/side 明确，否则 ValueError 触发回退 | 安全优先正确；见 P3-5 |
| `liteapp/analysis.py` | 模型 ID 只认 `id`，不把 display name 当 ID | 正确（修复保存错误 ID）；见 P3-4 |
| `liteapp/memory_retrieval.py` | 检索全程持 RLock，避免索引被另一线程替换导致 A 证据配 B 文本 | 正确（RLock 可重入，`_build_index` 同一锁不会死锁） |
| `liteapp/build_info.py`、`codex_backend.py` | Git/Codex 子进程 `CREATE_NO_WINDOW` | 正确（避免弹控制台）；测试覆盖 |
| `installer/ChatAssistantLite.iss`、窗口标题 | 0.5.8 版本号 | 已随 `a3b2994` 提交；安装版 0.5.7 需最终 rebuild/install |
| `liteapp/history_ui.py` + `tests/test_history_ui.py` | 同名档案选择唯一化；清空当前联系人时清报告/趋势/统计；历史写作用域无越界 | 行为安全（同名不自动合并）；测试补齐 |
| `tests/fixtures/native_chat_fixture.py` + `tests/test_native_fixture_opt_in.py` | 可见桌面夹具改为显式 opt-in（env `CHATASSISTANT_NATIVE_DESKTOP_TESTS=1`），默认 SkipTest 且不 Popen | **正确**（避免默认测试开真实桌面窗口）；测试覆盖默认不启动/显式 mock 启动 |
| `tests/__init__.py`（已跟踪） | LOCALAPPDATA 临时隔离 + Qt 退出清理 | 关键安全网；见 P1-2：必须 `-t .` 才生效 |
| 新测试 `test_action_dock_ownership.py` | busy 期间不重接管；查找结果后不按 Enter | 覆盖了两个关键回归点；建议再补 P1-4 的 prepare 异常清理 |
| 新测试 `test_model_catalog_regressions.py` 等 | 模型目录/读取校验/远程读取卡死/预览缩放 | 初始检查未见真实数据/凭据/桌面依赖；Lead 10:16 全量 713 OK/37 skip 覆盖 |

## 5. C1–C5 接管产物复核清单（状态：进行中）

### C1（windows；写：ui.py、tests/test_find_pause_capture.py、tools/、证据 MD）
- [x] `git status` 确认只改允许文件：当前 `M liteapp/ui.py`、`M tests/test_find_pause_capture.py` + `tools/c1_find_ab_diag.py`/`tools/c1_ui_acceptance.py`（均为允许范围；`wechat_find.py` 未被 C1 改，仍归 C4）；
- [x] H3：发送前标题改规范化**精确相等**（`normalize_title_for_match/titles_match`），子串/前缀/空串拒绝，`send_message` 未被调用测试；见 P1-3 状态；
- [x] H4：find/send `work()` 整体 try/finally + prepare 异常测试；见 P1-4 状态；
- [x] A3 根因修复初审：`_wait_wechat_render_ready`（单次 activate + visible/foreground/非空帧，2.5s 超时 fail-open 到仍 fail-closed 的 find_conversation）+ `_run_find_with_recovery`（只对 `search_not_found/result_not_found/capture_failed` 重试 1 次，两次均无发送；`test_find_pause_capture.py` 已加 readiness/retry/有界/不发送测试）；**但见 P2-10：失败 ESC 可能隐藏微信/发给其他应用**；
- [ ] 真实 UI 点击证据：必须“隔离 LOCALAPPDATA + 0.5.8 source tree（`a3b2994`/`103f1e4`）+ PID/app_version/commit/profile 是否隔离”；绝不点击已安装 0.5.7/真实 profile；失败时 ESC、不发送、不切错人；**失败后微信仍 visible、rect 不变**（P2-10 验证点）；
- [x] 新工具初查：`c1_ui_acceptance.py` 用 `sys.executable main.py` + 隔离 LOCALAPPDATA（L37-38/L296-297），`auto_send_allowed=False`（L293），证据记录 `app_version`（L261）与 profile 路径，文档声明不点发送；`c1_find_ab_diag.py` 隔离 profile（L36-37）、`auto_send_allowed=False`（L177）、统计 `unexpected_enter`（L253）、结束 ESC 清理（L436-437）。**待其真实运行证据落盘后核对 app_version/commit 与是否隔离**；
- [ ] 证据不含真实联系人/聊天截图（只记录提示文本/布尔/矩形）。

### C2（contracts；写：build_info 相关脚本、tools/、tests/、证据 MD）
- [x] `tools/c2_profile_probe.py`（10:20 版）：`_profile_dir()` 现在要求 `--isolated-localappdata` 且 `LOCALAPPDATA` 必须等于该隔离目录；`--forbidden-profile` 拒绝真实 profile；synthetic 内容、凭据只记 digest/布尔；`_code_info()` 记录实际 package_root/版本/schema，供“旧代码生成”审计；
- [x] `tools/c2_upgrade_verify.py`（10:21 版）初审：0.4.3（`5cafa1a`）→0.5.8（`a3b2994`）；`git archive` 提取到独立 WorkDir；**TEST_APP_ID 唯一**（不碰真实安装注册）；pinned `--forbidden-profile` 真实 profile 快照前后对比；build/install 用隔离 `LOCALAPPDATA`；`/NOICONS` + `/DIR` 隔离目录；probe 以 `PYTHONPATH=root`+`cwd=root` 跑归档代码；`installer_retention_check` 检查 UninstallDelete 不含 localappdata；uninstall 只针对隔离目录；
- [x] 已核对：`5cafa1a`（0.4.3）确实包含 V2 history（`history_models`/`import_records`/`snapshot`/`save_derived_memory`、APP_VERSION=0.4.3），probe 可在旧代码下运行；**但 0.4.3 已是 V2，本次是 V2→V2 兼容/数据保留，不是 0.2 V1→V2 迁移**，证据需如实写；
- [x] C2 已实跑（10:21）：数据链 5 步（old write/read、new read/resave/迁移后读）、installer retention 2 步、test installer 编译 2 步均 OK；`real_profile_before==after`、`real_profile_unchanged=true`（真实 profile 未被触碰）；
- [ ] **C2 当前阻塞**：`install-old-0.4.3` 静默安装 exit 1（未捕获 `/LOG=`，tail 为空）→ 本次 `ok=false`，安装/升级/卸载矩阵不完整；需给 install_setup 加 `/LOG=`、定位失败原因后重跑；在修复前不能把 C2 写成“通过”；
- [ ] 冻结后建议把 `NEW_COMMIT` 从固定 `a3b2994` 改为最终 commit（或允许 docs-only 后代），并用 `--json-out` 落在 `test-output/`；
- [ ] 运行完成后核对：`isolation.real_profile_after == before`、`data_migration.from_version/migrated`、安装/卸载/重装退出码、`risky_uninstall_delete=[]`；证据不含 `C:\Users\<user>` 日志原文（只引用 tail/摘要）；
- [ ] 待落盘：证据 MD、tests（若有）。

### C3（takeover-wintest；写：tools/、test-output/、证据 MD）
- [x] 早期 `test-output/c3-dev/dump_uia.py`：隔离 LOCALAPPDATA、auto_connect=False、dock=False，未连接微信；dump 只含控件名/尺寸（已检查无聊天内容）；
- [x] `test-output/c3-dev/dev_*.py`（dev_click/dev_postclick/dev_settings/dev_settings2/dev_toggle_save/dev_scroll/dev_resize_click）：均启动隔离 profile 的 0.5.7 已安装 App，`auto_connect=False`，只点应用自身设置/导航/缩放/保存，未点微信聊天/输入框；`finally` 杀死自己拉起的 app 进程；EditControl 只记 `len(value)`；
- [x] `tools/c3_real_window_verify.py`（10:21 版）初审：只操作**源代码树 0.5.8**（`sys.executable main.py` + 隔离 LOCALAPPDATA；记录 version_json/app_version/commit）；preflight 要求 `window_status=="visible"` 且**唯一主微信窗口**（`find_wechat_window` 仅 `main_count==1` 才继续）；无 OCR、无截图、无发送；editor 只记长度；status 只留白名单短标签；`auto_send_allowed=False`、`dock_wechat=False`；每个 step 后恢复 WeChat 可见/矩形；`finally` cleanup 关 app（WM_CLOSE→taskkill）并 `force_visible()` 恢复 WeChat；popup 只用右键打开真实菜单后 ESC/WM_CANCELMODE/WM_CLOSE 关闭，不点菜单项；
- [ ] P2-8：`statuses()` 应限定状态控件/排除中央编辑区，避免含 hint 的短聊天文本入 evidence；
- [ ] P2-9：证据明确 popup 是认可场景；`SOURCE_TREE_COMMIT` 改为运行时最终 commit（当前 `103f1e4` 会与固定 `a3b2994` 前缀不符，误报 setup.version）；
- [ ] 证据版本/矩形/布尔/状态标签；UIA 矩形/pid/hwnd 等本机信息不进 docs 原文；未覆盖场景列“最小人工步骤”。

### C4（takeover-ocr；写：wechat_find.py、tests/test_wechat_find.py、证据 MD）
- [x] 结果行改为 `_result_rows()`：规范化**精确等名** + 搜索输入带下方 + 左栏宽度限制；同名多行 → `result_ambiguous` fail-closed（L171-185、L317）；
- [x] 头部新增 `_header_secondary_match()`：紧裁剪 + 1x/2x + app `read_title`，仅精确匹配，**无模糊接受路径**（L187-217）；
- [x] 未重新引入 Enter（结果点击后仍不按 Enter，失败 ESC）；`_result_rows` 排除输入框/历史消息预览/网络结果，降低切错人风险；
- [x] **P1-6 已修**：`_foreground_ok`（visible+前台）在抓屏入口与搜索框/结果点击前校验；非前台 capture None / `not_foreground`+ESC fail-closed，不再抓/点其他窗口内容；见 P1-6 状态；
- [x] `tests/test_wechat_find.py` 已更新（Lead 日志 `Ran 12 tests OK`）：精确结果行/消息预览不点击/搜索输入不点击/同名歧义 `result_ambiguous`/头部单字差异拒绝/`read_title` 精确接受/无 Enter；`_read_title_crop` 在测试中 mock，不加载真实 OCR；
- [ ] 补显式测试：`_foreground_ok=False` / `capture` 返回 None → 不调用 ImageGrab/`click`，fail-closed（ImageGrab mock）；check→click TOCTOU 可再加 `WindowFromPoint` 校验（低优先）；
- [ ] 真实窗口时段（10:20–10:35）只做查找/切换，绝不发送；证据版本/布尔/长度，不保存截图/聊天内容；结果 MD 待落盘。

### C5（inference；写：tests/、tools/、证据 MD）
- [x] `tests/test_c5_synthetic_inference.py`：fake server 仅 `127.0.0.1` 随机端口（L120）、synthetic token（L244）、临时 `HistoryStore`（L268）；`tools/c5_synthetic_evidence.py` 同样 loopback（L119/L133）+ synthetic 凭据（L232-238、L374-377）+ 临时 store（L246）+ 只写 `test-output/c5/*.json`（L584）；**未发现真实凭据/profile/聊天内容**；
- [x] `docs/windows/CODEX_TAKEOVER_C5_SYNTHETIC.md` 明确“合成≠真实模型/双账号”，并列出 5 项不可替代的真实环境项；**结果段（§3）仍是占位，需完成时填真实输出**；
- [ ] 完成后检查：不调用真实 API（URL 全为 fake server）、不读取真实 profile/history、不写真实数据目录；测试断言有效（先失败后通过）；报告注明“合成≠真实模型/双账号”；
- [ ] 双源仿真不得使用真实微信窗口/HWND；只合成 WindowTarget/抓帧。

## 6. 接管期边界检查（v4 快照，10:26）

- **基线/越界文件**：当前 HEAD `103f1e4`（docs-only 提交在 `a3b2994` 之上）；工作区：`M liteapp/ui.py`、`M liteapp/wechat_find.py`、`M tests/test_find_pause_capture.py` + C1/C2/C3/C5 新 tools/tests/docs + 本报告。C1 只改 `ui.py`/`test_find_pause_capture.py`/tools；C4 只改 `wechat_find.py`（`tests/test_wechat_find.py` 尚未更新）；C2/C3/C5 均在各自 tools/tests/test-output 范围。**未发现越界写**。
- **真实数据/凭据/聊天内容**：C1/C3 用隔离 LOCALAPPDATA + 源码树；C2 probe 强制隔离 + forbidden profile；C5 synthetic/loopback；未发现真实聊天内容/凭据写入交付物。残余风险：P2-6（test-output 旧脚本）、P2-8（C3 status_texts 全控件名）、P1-6（C4 屏幕抓取遮挡）；真实 profile 0.5.7 实例仍在运行（P1-5，C1 已按隔离方式规避）。
- **切错会话/自动发送风险**：C4 结果行精确匹配 + 歧义 fail-closed、无 Enter（正面）；C1 发送标题精确匹配 + prepare finally（正面）。剩余：P1-6（C4 前台校验）、P2-1（真实微信版本 Enter 兼容性）、P2-3（marker[:6] 弱验证）。
- **测试缺失**：C4 `tests/test_wechat_find.py` 未更新（P1-6/mock 测试缺）；C1 已补 H3/H4 + 无 Enter/52 项定向；C3 helper tests 已落盘；C5 结果段待填；C2 tests 未见（若有需核）。
- **测试基础设施**：`tests/__init__.py` 隔离 + Qt 清理；全量命令带 `-t .` 已确认。

## 7. 全量测试状态

- **Lead 已跑（10:16）**：`cd windows/lite; venv python -m unittest discover -s tests -t . -q` → **Ran 713 tests, OK (skipped=37), exit 0**（日志 `windows/lite/test-output/lead_review_codex_0.5.8.log`；尾部确认）。
- **我的独立复跑（10:19–10:22，WIP/dirty，已执行）**：
  - 命令：`cd G:\codex\ChatAssistant\windows\lite` + `G:\codex\wechat-lite-venv\Scripts\python.exe -m unittest discover -s tests -t . -q`
  - 运行前：HEAD `103f1e4ca29dafe322fe6bb69b19d20539d8eda3`；`git status --porcelain` **10 项 dirty**（`M liteapp/ui.py`、`M tests/test_find_pause_capture.py` + C2/C5/C3 新文件）。
  - 结果：**`Ran 713 tests in 119.668s` / `OK (skipped=37)` / exit code `0`**。
  - 运行后：dirty 10→13（C1/C2/C3 新文件落盘：`c1_find_ab_diag.py`、`c1_ui_acceptance.py`、`test_c3_evidence_helpers.py`、`c3_real_window_verify.py`、`c2_upgrade_verify.py`）；**本次结果未包含运行开始后才写入的 C1/C2/C3/C4 新测试**（C4 `wechat_find.py` 10:24 才改）。
  - 说明：WIP 中间信号，不能作为最终验收证据；与 Lead 10:16 的 713 OK 基线一致。
- **最终干净全量（Lead 冻结通知后，已执行）**：
  - 运行前：HEAD `6e264ebf620533f016c565c6d62d76af558eeba9 docs: record clean 0.5.9 candidate and Codex takeover result`；`git status --porcelain` 为空（dirty=false）。Lead 消息中的 `13c7ff3` 已验证为本 HEAD 的祖先（0.5.9 bump 提交）。
  - 命令：`cd G:\codex\ChatAssistant\windows\lite` + `G:\codex\wechat-lite-venv\Scripts\python.exe -m unittest discover -s tests -t . -q`
  - 结果：**`Ran 750 tests in 117.904s` / `OK (skipped=37)` / exit code `0`**（119.2s wall）。
  - 运行后：HEAD 不变、`git status` 仍为空（clean）。
- 对比：Lead 10:16 基线 713 tests（0.5.8 WIP），冻结后 750 tests（C1/C3/C5 新增测试并入），skipped 仍 37；无失败/错误。此结果作为最终全量证据。

## 8. 终稿状态（已完成，11:07）

- 干净全量通过：HEAD `6e264eb`（clean），`Ran 750 tests / OK (skipped=37) / exit 0`。
- C1 completed（带限定）、C2 completed（隔离范围）、C4 completed、C5 completed；C3 85%（部分自动 + 人工项，S-V03 待现场复签）。
- 安装版 0.5.9 已安装（build_info commit `13c7ff3`，Lead 记录 SHA `119FCC83…`）；Codex 面板 C1/C2/C4/C5 completed、C3 85%。
- 剩余人工/后续项见 `WINDOWS_LITE_UNFINISHED_2026-10-18.md` 与本报告 v20 的“剩余风险”；本报告作为交接复核记录。
- task-25 已具备完成条件（全量通过 + 报告终稿）。

## 9. 方法与证据

- `git log`/`git status`/`git diff`（Git at `C:\Program Files\Git\cmd\git.exe`）；最终 HEAD `6e264eb`（clean；`13c7ff3` 为祖先）；冻结前工作区 15 个已跟踪修改 + C1–C5 接管产物；
- 逐文件静态审查：`ui.py`（H3/H4/ESC/A3 retry 前后 diff）、`wechat_find.py`（C4 全 diff）、`tests/test_find_pause_capture.py`/`test_wechat_find*.py`、`tests/__init__.py`、`tests/fixtures/native_chat_fixture.py`、C1/C2/C3/C5 tools 与证据 MD、`test-output/` 快照；
- 边界扫描：`Select-String` 搜真实 profile/凭据/Enter/发送/ImageGrab/屏幕抓取/status_texts/JSON 输出路径；
- 已核对：0.4.3 commit `5cafa1a` 的 V2 history API、C2 TEST_APP_ID 唯一/真实注册表未变/真实 profile SHA、C3 单窗口/visible preflight/cleanup/F1-F4、C4 foreground guard/16 项测试、C5 loopback/synthetic/temp；
- 独立全量：WIP 713 OK（10:19）→ **冻结干净 750 OK / 37 skipped / exit 0（HEAD `6e264eb`）**；
- 未发送微信消息、未修改业务代码/真实 profile、未读取真实聊天内容；最终安装版 0.5.9 由 Lead 构建/安装并记录。

> 本文件为只读复核记录，不构成对 C1–C5 的最终验收；最终以 Lead 冻结工作区后的全量测试 + 真实证据为准。
