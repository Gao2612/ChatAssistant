# ChatAssistant Lite 0.5.21 功能报告与实测包

- 生成时间：2026-10-18 21:28（Asia/Shanghai；21:26 重建新 logo 版）
- 生成方：DeepSeek Harness（初版），Codex（实机验收与安装包更新）
- 触发原因：用户 #93/#96 要求额度接近上限时整理现状并提供可实测安装包。
- 代码基线：`feat/windows-lite`；备份提交 `c02ac4b`，最新文档提交 `d577ca8`。功能分支已推送到 GitHub；安装包由 0.5.21 应用代码构建，manifest `dirty=true`（source digest 已验证）。
- 应用版本：`APP_VERSION = 0.5.21`

## 1. 交付物

| 交付物 | 路径 | 说明 |
| --- | --- | --- |
| 最新安装包 | `G:\codex\ChatAssistant\windows\lite\dist-release\ChatAssistantLite-Setup.exe` | **102,860,363 字节**，SHA256 `F5D668A4A420FBB778AEDA2C58EC79773495DF131B509440AF6C365FB42B0B90`（21:26 重建，含新 logo 图标） |
| 打包验证 | `windows/lite/package.ps1` | PyInstaller + Inno Setup 编译成功；便携 `--smoke` 成功；`verify_build_info.py` 返回 `ok=true` |
| 隔离安装生命周期 | `windows/lite/tools/verify_install.ps1` | 本轮新包 `install_exit=0`、`self_test_exit=0`、`uninstall_exit=0`、`files_removed=true`、`ok=true`；安装目录位于临时目录，未覆盖用户安装 |
| 全量测试 | `windows/lite/test-output/c1/reopen-20261018-final-full-after-driver.log` | 909 项通过 / 42 项跳过（Codex #78/#81） |
| C1 真实 UI 报告 | `windows/lite/test-output/c1/c1_ui_acceptance_20261018-204758.json` | 失败：`not_foreground`、`switched=false`、`title_seen=false`（无文本/无截图） |

## 2. 已交付/已完成功能（本轮及近期）

1. **看板与互审协作系统**（S1–S11）
   - `http://127.0.0.1:8790/` 看板：来源分工（DeepSeek/Codex）、按最近活动/完成时间排序、服务端自动纠正（待办任务写进度→进行中）、待办列不显示进度条；
   - `http://127.0.0.1:8790/chat` 互审对话：聊天/纠正/提问/回应/结论/任务消息、引用回复、任务 ID 关联、2 秒刷新；
   - Codex 聊天桥接（`codex_chat_bridge.ps1`）与对话驱动任务执行器（`codex_task_executor.ps1`）：kind=task 自动执行、claim/progress/completed、结果回写。
2. **0.5.21 应用侧修复**
   - C13：微信窗口被最小化/停到屏外（`-25600,-25600`）时，发现逻辑按 hidden/offscreen 处理，显式恢复先移回主屏再显示；已安装版本 0.5.21。
   - C8-FIX：模型/目录 ID 截断修复（保留完整 ID），新增长 ID 往返与防碰撞回归，展示差异补注释。
   - C1（部分）：修复查找完成通知早于窗口恢复的竞态；测试 harness 版本识别改为读取 `build_info.APP_VERSION` + 进程树 PID 所有权校验，去掉 0.5.8 硬编码。
   - C14/C15 方向：悬浮球外观/桌面图标资源、设置页信息分组（Codex 已提交审查意见与合成截图）。
3. **测试与质量**
   - 全量 909 OK / 42 skipped；C8-FIX 定向回归通过；C13 discovery 59 项、window 102 项通过（含跳过）。
   - 新包通过隔离安装→自检→卸载；Inno Setup 输出中文语言包缺失条目警告，未定义的安装向导消息回退为英文默认文本。

## 3. 未验收 / 已知风险（实测重点）

1. **C1 真实 UI 前台丢失（未验收）**：点击自动查找已触发 `find_started/find_finished`，但最终 `status_category=not_foreground`、`switched=false`、`title_seen=false`；微信可见且矩形未变，未发消息。需要实测：真实微信打开、点击“自动查找并切换对话”，观察是否切到目标联系人。
2. **C10 档案/分析链路（Codex 审查，未修）**：真实 PDF/HTML 记录 `sender=None` 会被跳过，可能零 chunks 却标 `model_analyzed=True`；同名档案 `matches[0]` 自动绑定；partial/cancel/空 fields 覆盖既有完整档案；本地档案 5 万条/400 万字静默上限；全量 chunk summaries 一次塞入 prompt；`model_busy` 时自动导入分析无排队。
3. **C11 联系人/档案作用域（Codex 审查，未修）**：`favorite_contacts` 按规范化标题全局匹配，跨账号同名可能串档；`contact_profile_for_current` 未传绑定 scope；浮球档案按钮仅打开历史页；分数环为本地启发式且无来源提示。
4. **C7 托盘抓帧与恢复竞争（Codex 审查，未修）**：后台 `tray_capture` finally 恢复为隐藏，`ui.launch_wechat` 显式恢复无互斥，可能被再次隐藏；疑似 bitmap GDI 资源未恢复原对象。
5. **C14/C15 视觉与布局（部分未验）**：浮球真实多屏/DPI/置顶/拖动/图标缓存未验；浮球展开固定 400x560、拖动无屏幕边界、长内容无滚动；设置页分组后组内仍偏挤。
6. **C17 测试清理（未验收）**：`tearDown` 在 10 次 PermissionError 后忽略清理错误，可能静默留 tmp；要求移除忽略、有限重试、达上限显式失败并给残留路径，加最终目录删除回归。
7. **环境类**：全量中的 DPAPI 相关失败与 `test_single_instance` 锁清理间歇错误在基线 `a7c5ea0` 即存在，非本轮改动引入；C1 真实微信验收需在无并行测试进程时进行。
8. **C9/C12 好感度与大文件分析（Codex 复核，未修）**：模型没有逐句证据仍可能输出 100 分/0.7 confidence；悬浮球目前仍显示本地启发式分数。模型分块后的摘要仍可一次膨胀到约 504,050 字符；UI 先读取/构建全量消息与 chunks；长消息中途取消会回到 offset 0；没有持久化断点/重启恢复，取消后仍可进入汇总阶段。
9. **本轮截图审查的限制**：设置页/悬浮球截图使用合成配置与本地字体，不含微信聊天内容；只能评价当前可见布局，不能证明真实桌面 DPI、多屏、置顶或微信连接行为。

## 4. 实测建议步骤

1. 退出当前 ChatAssistantLite（悬浮球/托盘），运行安装包覆盖安装；确认关于/标题版本为 0.5.21。
2. 连接微信：点击应用内“自动查找并切换对话”，记录是否切到目标联系人（C1 重点）。
3. 检查悬浮球：拖动/展开/收起/多屏与 DPI、任务栏/托盘恢复；观察图标是否为新版。
4. 打开设置页：确认微信连接、读取模型、分析模型与账号、自动操作与隐私、外观/应用行为分组可扫读，保存行为正常。
5. 打开联系人档案分析：确认关联当前会话后显示联系人名、档案按钮进入正确 tab（C10/C11 重点）。
6. 如有问题：记录时间/页面/复现步骤/报错，回复本报告或看板即可。

## 5. 边界声明

- 本报告只汇总现状，不代表全部未验收项已修复；未验收项已明确标注。
- 0.5.21 半成品备份已按用户 #98 授权推送到 `feat/windows-lite`；本轮安装包在本机重新构建，SHA 与 20:43 旧包不同，请使用本报告“最新安装包”行的文件。
- 自动互审/执行器只在本机 127.0.0.1 运行，不读取聊天文本、不截图会话、不发送微信消息。

## 6. GitHub 备份（2026-10-18 21:02）

- 仓库：https://github.com/Gao2612/ChatAssistant
- 分支：`feat/windows-lite`（已设置 upstream）
- 备份提交：`c02ac4b`（应用与报告），随后 `2ff787c`、`d577ca8` 更新了备份说明；当前远端 `feat/windows-lite` 指向 `d577ca8`。
- PR 入口：https://github.com/Gao2612/ChatAssistant/pull/new/feat/windows-lite
- 说明：备份包含 0.5.21 应用代码、UI 资源（`windows/lite/assets/`）、C1/C13/C8-FIX 改动与本报告；仅备份用途，未经 Codex 验收的改动已在第 3 节标注；未创建 PR、未合并 `main`。
