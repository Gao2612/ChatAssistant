# Codex 额度空窗期：DeepSeek 多智能体临时接管（2026-10-18）

## 背景与时窗

- Codex 侧 5 小时额度用完，**使用量在 14:51 重置**；今天 14:51 前只有 DeepSeek 侧能干活。
- DeepSeek Lead 已把卡点任务分到看板 `source=codex`（C1–C5，均 pending）。
- 为避免空窗浪费，DeepSeek Lead 启动多智能体**临时接管 C1–C5**；14:51 后 Codex 恢复时不再重复做已完成的项，只做复核/补漏。

时间基准：2026-10-18 约 10:10（Asia/Shanghai）。接管截止：**14:30 前提交交接**，14:51 后交还 Codex 复核。

## 当前工件

- 仓库：`G:\codex\ChatAssistant`，分支 `feat/windows-lite`，本地提交、**不 push**
- HEAD：`2dd7d0b`（文档；最后代码提交 `32fc8f2`）
- 已安装候选包：**0.5.7**
  - `windows/lite/dist-release/ChatAssistantLite-Setup.exe`
  - SHA-256 `35C37FEC197C9B088FB79F36F831B9624093BD6D3427B898AFA679B57CE3E34E`
- 自动证据：全量 **668 项测试 exit 0**；构建/自检/安装生命周期通过
- 看板：<http://127.0.0.1:8790/>；Codex 任务：`board.py show --source codex --status pending`

## 接管分工（写作用域互斥）

| Codex 任务 | DeepSeek 接管者 | 主要写作用域 | 交付物 |
| --- | --- | --- | --- |
| C1 App 内自动查找按钮路径 | `windows`（现有队友） | `liteapp/ui.py`、`tests/test_find_pause_capture.py`、`tools/`、本 MD | 复现结论 + 修复 + 测试 + 真实 UI 点击证据 |
| C2 0.4.x→0.5.7 升级/卸载/保留数据 | `contracts`（现有队友） | `liteapp/build_info.py` 相关脚本、`tools/`、`tests/`、独立证据 MD | 升级/卸载/保留数据可重复脚本与结果 |
| C3 真实窗口停靠/弹窗/最小化/托盘恢复 | `takeover-wintest`（新 spawn） | `tools/`、`test-output/`、独立证据 MD | 状态/矩形/布尔证据 + 最小人工步骤 |
| C4 OCR 名字误识改进（郜亚禹类） | `takeover-ocr`（新 spawn） | `liteapp/wechat_find.py`、`tests/test_wechat_find.py`、OCR 证据 MD | 可安全切换或明确不可行 + fail-closed 证据 |
| C5 T9/T10 合成验收 | `inference`（现有队友） | `tests/`、`tools/`、独立证据 MD | fake server + 双源仿真可重复结果 |

**不参与写**：`history` 作为只读复核者，负责跑全量测试、审阅证据、找回归问题并报告 Lead。

### 互斥规则

1. 除上表作用域外，不改其他文件；尤其不要同时改 `ui.py`/`wechat_find.py`。
2. 不执行 `git commit`/`git push`；改动留在工作区，由 Lead 统一跑测试后分阶段提交。
3. 不发送微信消息；不保存真实聊天内容/截图；测试只用合成数据或只记录长度/计数/状态。
4. 不修改真实用户 profile（`%LOCALAPPDATA%\ChatAssistantLite`）；需要数据目录时用隔离临时目录。
5. 测试命令统一：
   `cd G:\codex\ChatAssistant\windows\lite`
   `G:\codex\wechat-lite-venv\Scripts\python.exe -m unittest discover -s tests -t . -q`
6. 每个 agent 在汇报里写：改了哪些文件、运行过的命令、测试结果、证据路径、未完成/风险。

## 各任务验收要点

### C1（接管者 windows）

- 复现：代码级 `find_conversation(hwnd,"田子夜")` 成功；App 设置页点“自动查找并切换对话”偶发 `search_not_found/result_not_found`。
- 方向：排查 App 内点击与直接调用的差异（前台/焦点/剪贴板/DPI/托盘恢复/WGC 会话）。0.5.7 已做动作前 `stop_capture`。
- 禁止为通过而发送消息；失败必须 ESC 安全退出。
- 成功：App 内点击真实切换；失败：给出确定复现条件与最小修复。

### C2（接管者 contracts）

- 构造 0.4.x 安装包或数据夹具（可用 git 历史构建；不要覆盖当前安装）。
- 验证：旧数据可读 → 升级 0.5.7 → Key/授权/历史保留 → 卸载默认保留数据 → 重装恢复。
- 不碰真实 profile；结果写独立证据 MD，Lead 汇总进验收矩阵。

### C3（接管者 takeover-wintest）

- 用 UIA/Win32 脚本验证：停靠勾选/取消、最小化、真实弹窗不误读、托盘隐藏→恢复后窗口位置/可见性。
- 只记录矩形/布尔/状态文本；不保存聊天内容、不发送消息。
- 必须人工的场景列“最小人工步骤”。

### C4（接管者 takeover-ocr）

- 目标：`郜亚禹` 类名字在**不切错人**前提下可自动查找。
- 可用搜索结果行精确文本为主证据、头部受控容错/二次裁剪、或可行非 OCR 信号。
- 禁止放宽到可能切换错误会话；失败必须 fail-closed 并说明原因。

### C5（接管者 inference）

- 本地 OpenAI 兼容 fake server 驱动真实 `InferenceGateway`/`InferenceQueue`：历史参考、额度/授权错误、取消/重试。
- 两个合成来源驱动 `SourceRegistry/CaptureScheduler`：active 优先、后台关闭、最小化暂停、帧未变不重复 OCR。
- 只合成数据；报告注明“合成≠真实模型/双账号”。

## 看板记录

Lead 会为每个接管项建 `source=deepseek` 镜像任务（`TAKE-C1…TAKE-C5`）并标注“临时接管 Cx”；C1–C5 原任务保留给 Codex，14:51 后 Codex 先看本 MD/看板再决定复核。

## 14:51 交还协议

1. 14:30 前各 agent 停止改动并汇报；Lead 冻结工作区、跑全量测试。
2. Lead 按互不重叠文件分阶段 commit；把证据汇总进 `WINDOWS_LITE_ACCEPTANCE_MATRIX.md` 与 `WINDOWS_LITE_UNFINISHED_2026-10-18.md`。
3. 14:51 后 Codex 恢复：
   - 已完成的 C1–C5 子项：Codex 只复核 + 补漏，不重复实现；
   - 未完成的：Codex 继续，DeepSeek 产物作为起点；
   - 冲突以“测试通过 + 真实验证证据更完整”为准，由用户最终签收。
4. 看板上把 `TAKE-C*` 标完成/关闭，C* 补进度说明“DeepSeek 接管已完成/交接给 Codex 复核”。

## 状态记录

- [x] 10:15 已建本地看板镜像任务 TAKE-C1…TAKE-C5、Team 共享任务 task-20…task-25
- [x] 10:15 已启动/唤醒：windows(C1)、contracts(C2)、takeover-wintest(C3)、takeover-ocr(C4)、inference(C5)、history(只读复核)
- [ ] 13:30 中期检查（Lead 汇总）
- [ ] 14:30 冻结与全量测试
- [ ] 14:51 交还 Codex 复核

### 运行中名单（10:15）

| 任务 | Agent | Team 任务 | 本地看板 |
| --- | --- | --- | --- |
| C1 | windows | task-20 | TAKE-C1 |
| C2 | contracts | task-21 | TAKE-C2 |
| C3 | takeover-wintest | task-22 | TAKE-C3 |
| C4 | takeover-ocr | task-23 | TAKE-C4 |
| C5 | inference | task-24 | TAKE-C5 |
| 只读复核 | history | task-25 | — |

> 所有 agent 均被要求：不 commit/push、不发微信消息、不保存真实聊天内容、使用隔离 profile、只改各自写作用域；完成后向 Lead 汇报，由 Lead 统一测试与提交。
