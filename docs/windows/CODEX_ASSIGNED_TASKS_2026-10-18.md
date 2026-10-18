# Codex 分派任务（ChatAssistant Lite，2026-10-18）

> 给 Codex 的工作交接。Codex 侧请只看/使用 `source=codex` 的任务与事件，
> 不要改动 DeepSeek 侧任务、排序或状态。
> 本地看板：<http://127.0.0.1:8790/> · 机器视图：<http://127.0.0.1:8790/api/board?source=codex>
> 接入方式见 `G:\codex\team-board\CODEX_BOARD_GUIDE.md`；本仓库 `AGENTS.md` 已包含看板协作规则。

## 仓库与候选包

- 仓库：`G:\codex\ChatAssistant`，分支 `feat/windows-lite`，**本地提交、不 push**
- 当前 HEAD：`dfde965`（仅文档；最后一次代码提交 `32fc8f2`）
- 已安装候选包：**0.5.7**
  - 安装包：`windows/lite/dist-release/ChatAssistantLite-Setup.exe`
  - SHA-256：`35C37FEC197C9B088FB79F36F831B9624093BD6D3427B898AFA679B57CE3E34E`
- 自动证据：全量 `668` 项测试 exit 0；`--version-json` clean、隔离 `--self-test`、`--smoke`、
  `package.ps1 -VerifyInstall` 通过
- 未完成总清单：`docs/windows/WINDOWS_LITE_UNFINISHED_2026-10-18.md`

## 必守约束

1. **不 push**；所有改动留在 `feat/windows-lite` 工作区并分阶段 commit。
2. **不自动发送微信消息**；任何发送相关验收只做“未发送/安全失败/人工确认”路径。
3. **不保存真实聊天内容/截图**；测试只用合成数据或只记录状态/长度/计数。
4. 动 `windows/lite` 前先跑：
   `cd G:\codex\ChatAssistant\windows\lite`
   `G:\codex\wechat-lite-venv\Scripts\python.exe -m unittest discover -s tests -t . -q`
5. 每个 Codex 任务完成后在看板带 `--source codex` 上报进度/完成，并在 `docs/windows/`
   相应文档记录证据；四份文档（AGENTS/设计/计划/进度）不改 DeepSeek 侧已有内容。
6. 改动文件前检查看板 `write_scopes`，避免与 DeepSeek 侧同时改同一文件。

## C1（最高优先）应用内自动查找按钮路径修复

**现象**：同一真实窗口，代码级 `find_conversation(hwnd,"田子夜")` → `ok=true`（约 7–11s）；
从 App 设置页点“自动查找并切换对话”按钮 → 偶发
`search_not_found / result_not_found`（未切换、未发送）。

**已排查/已做**：
- 0.5.7 已在动作前 `stop_capture()`、结束后自动重连，排除两个 WGC 会话抢帧；
- OCR 搜索框/结果行定位有 2 次交互重试 + 4 帧结果等待；标题严格校验 fail-closed；
- 失败时 App 会显示“没有找到微信搜索框/搜索结果里没有找到”。

**要做**：
1. 用真实窗口复现 App 内点击路径，定位差异（前台时序、焦点、剪贴板、窗口 DPI、
   与 App 自身窗口/托盘恢复的相互影响均需排除）。
2. 修复根因；若无法复现，写出确定性复现脚本与最少条件，交回 DeepSeek Lead 判定。
3. 回归：代码级真实查找、UI 内真实点击、失败路径 ESC 关闭、不发送消息。

**允许改**：`liteapp/ui.py`、`liteapp/wechat_find.py`、`liteapp/wechat_actions.py`、
`tests/test_wechat_find.py`/`test_find_pause_capture.py`/新增测试、`docs/windows/` 证据。
**验收**：App 内点击“田子夜”成功切换或提供可重复的结论；全量测试通过。

## C2 T12-01：0.4.x→0.5.7 升级/卸载/数据保留 + 干净环境回归

**现状**：隔离 `verify_install.ps1` 已通过；真实 0.4.x→0.5.7 升级、干净 VM 未做。
**要做**：
1. 准备 0.4.x 安装包/数据夹具（可从 git 历史构建或人工构造最小 fixture；不要破坏当前安装）。
2. 在独立目录/用户数据下验证：旧数据可读 → 升级 0.5.7 → 授权/Key/历史保留 → 卸载默认保留数据 → 重装恢复。
3. 输出可重复脚本/记录到 `test-output/` 与验收矩阵；**不要**把真实 profile 当作测试目标。

**允许改**：`windows/lite/tools/`、`windows/lite/tests/`（隔离夹具）、`docs/windows/WINDOWS_LITE_ACCEPTANCE_MATRIX.md`。
**验收**：S-V01 的升级/卸载/保留数据有真实步骤与结果；不破坏 `E:\ChatAssistantLite`。

## C3 T12-03/托盘：真实窗口 UI 行为验收自动化

**现状**：镜像预览缩放已自动验证（应用改宽，微信矩形不变）；真实停靠/弹窗/最小化/托盘恢复
的人工/自动化混合验收仍缺；托盘隐藏读取已通过（自动）。
**要做**：用 UIA/Win32 编写真实窗口验收脚本（不发送消息、不保存聊天内容）：
- 勾选/取消“停靠微信窗口”行为；
- 微信最小化、真实弹窗（图片预览/菜单）时助手不误读/误发；
- 托盘隐藏→恢复后窗口位置/可见性正常。
记录状态文本、矩形、布尔值；结果写矩阵；如某场景必须人工，明确列出最小人工步骤。

**允许改**：`windows/lite/tools/`、`test-output/`、`tests/`、矩阵文档。
**验收**：S-V03 可签收或有最小人工清单；无真实内容落盘。

## C4 自动找对话 OCR 名字误识改进

**现状**：`田子夜` 可查找；`郜亚禹` 头部 OCR 常读成“部亚禹/鄢子夜”，产品 fail-closed（安全但不便）。
**要做**：在不冒“切错人”风险的前提下提高可读性，例如：
- 用搜索结果行的精确文本作为主证据 + 头部 OCR 的受控容错/二次裁剪；
- 或增加非 OCR 的会话确认信号（窗口标题/UIA/进程内存不可行时明确记录）。
**禁止**：为了通过而放宽到可能切换错误会话。
**允许改**：`liteapp/wechat_find.py`、相关测试、`docs/windows/OCR_DEPENDENCY_AND_ALTERNATIVES_2026-10-18.md`、矩阵。
**验收**：`郜亚禹` 可安全切换，或写明不可行原因并保留 fail-closed 证据。

## C5 T9/T10 替代验收：本地合成模型服务 + 双源仿真

**现状**：真实模型回合被本机 API Key 占位/额度阻塞；双真实微信窗口不可得。
**要做**：构建确定性验收：
1. 本地 OpenAI 兼容 fake server（固定 JSON 响应），走真实 `InferenceGateway`/`InferenceQueue`，验证 T9 历史参考、Token/额度错误、取消/重试路径；
2. 双源仿真（两个合成 `WindowTarget`/抓帧源）驱动 T10 `SourceRegistry/CaptureScheduler`，验证 active 优先、后台关闭、最小化暂停、帧未变不重复 OCR；
3. 运行结果写报告/矩阵，标注“合成，不等于真实模型/双账号”。

**允许改**：`windows/lite/tests/`、`tools/`、`docs/windows/`。**不要**触碰真实凭据/真实聊天内容。
**验收**：T9/T10 在无凭证条件下的替代证据可重复；明确剩余仍需用户的项目。

## 看板上报命令（Codex 侧）

```powershell
$py = 'G:\codex\wechat-lite-venv\Scripts\python.exe'
$b  = 'G:\codex\team-board\board.py'
& $py $b show --source codex --status pending
& $py $b claim --task C1 --owner codex --source codex
& $py $b progress --task C1 --text "已复现/定位…" --status in_progress --percent 30 --owner codex
& $py $b progress --task C1 --text "完成：…验证结果…" --status completed --percent 100 --source codex
```

完成后 DeepSeek Lead 复核、跑全量测试、更新四文档与验收矩阵；用户做最终人工签收。
