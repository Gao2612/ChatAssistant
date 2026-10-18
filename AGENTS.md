# Agent 项目入口

本仓库当前主体为 Android 聊天助手；新增 Windows 产品方向的权威需求入口如下：
- [Windows 完整版设计（2026-10-17）](docs/windows/WINDOWS_LITE_COMPLETION_DESIGN_2026-10-17.md)
- [Windows 完整版分任务实施计划](docs/windows/WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md)
- [Windows 微信详细方案](docs/windows/WINDOWS_WECHAT_PRODUCT_PLAN.md)
- [Windows Agent 交接](docs/windows/AGENT_HANDOFF.md)
- [Windows Lite 0.2 本机验证](docs/windows/WINDOWS_LITE_VERIFICATION_2026-10-17.md)
- [读取/分析双后端验收清单（2026-10-18）](docs/windows/WINDOWS_LITE_ACCEPTANCE_CHECKLIST_2026-10-18.md)
- [读取/分析双后端整改报告（2026-10-18，本机 `_reports/`）](_reports/backend_read_analysis_split_report.md)
- [微信嵌入实机结果](docs/windows/WECHAT_EMBED_TEST_RESULTS_2026-10-14.md)
- [临时探针源码](windows/probes/wechat-embed/README.md)

任何涉及 Windows、微信嵌入、多开、实时回复、ExportWeChat HTML、客户画像、关系趋势、聊天统计、exe 安装包、读取/分析后端选择的任务，应先阅读完整版设计、分任务实施计划、0.2 本机验证与 2026-10-18 验收清单。设计中的待实施能力不得写成已交付。

2026-10-18 状态：T0a/T0b（点1/点4）与 T1–T11 代码/构建完成并分阶段提交；T12 自动项已通过（0.4.1 安装版：3×644 回归、单实例、版本无泄露、自检 fail-closed、隔离安装生命周期），真实微信场景与干净 VM 待人工；T9/T10 UI 集成完成；D-UI 已完成（当前候选 0.5.7，哑光纸+青瓷设计系统）。T12 自动可做项已全部通过；真实模型回合需要用户重填 API Key/恢复额度，人工场景需要用户在场，详见 T12 人工交接文档。真实进度以 [WINDOWS_LITE_IMPLEMENTATION_PROGRESS.md](docs/windows/WINDOWS_LITE_IMPLEMENTATION_PROGRESS.md) 为准。

## 工作边界
- `windows/lite/` 已有 Windows 0.2 本地实现和测试安装包；旧临时 `SetParent` 探针发现单击/滚动故障。新版顶层窗口停靠已验证鼠标命中、画面和 OCR，但单击/滚动、输入法、扫码及弹窗尚未逐项验收，不得写成完整兼容。
- Windows 工程建议独立放在 windows/，保留 Android app/。
- 根 CLAUDE.md 中 Android 平台规则适用于原 Android 工程，不应误作 Windows 固定路径或依赖要求。
- 用户于 2026-10-14 明确授权编写并上传本次方案；这次文档提交符合用户指令，覆盖 CLAUDE.md 中通用的禁止提交/推送规则。
- 同日用户另行授权上传临时测试系统源码和结果；本次提交不包含真实聊天截图和运行日志。
- 后续提交与发布遵循各自任务的用户授权，不从本次文档授权推导产品部署授权。
- 不自动发送聊天、不修改官方微信、不直接读取微信数据库；实时采集与历史导入是独立通道。
- 保持账号/会话隔离，不把联系人名称作为稳定身份。
- 不提交真实聊天、附件、密钥和个人资料；测试使用合成或脱敏数据。
- 报告事实、推断和数据缺口分开，保留原始消息证据与实际覆盖范围。
- 开始代码修改前确认所需子目录规则；交付说明真实验证和未测项。
- 当前未完成/待用户验收项唯一清单：[docs/windows/WINDOWS_LITE_UNFINISHED_2026-10-18.md](docs/windows/WINDOWS_LITE_UNFINISHED_2026-10-18.md)
## 任务看板协作（Codex / 其他 agent）

- 本地看板：http://127.0.0.1:8790/ （本机 127.0.0.1，2 秒自动刷新；顶部「来源分工」区分 DeepSeek 与 Codex，默认两边都可见）。
- 读写入口：`G:\codex\team-board\board.py`（Python venv：`G:\codex\wechat-lite-venv\Scripts\python.exe`）。
- Codex 的每次写入必须带 `--source codex`（或设置环境变量 `TEAM_BOARD_SOURCE=codex`），否则任务会落到 DeepSeek 一侧。
- 标准流程：`show --source codex --status pending` 看待认领 → `claim --task <id> --owner codex --source codex` 认领 → 每完成一步 `progress --task <id> --text "..." --status in_progress --percent <n>` → 完成时 `progress --task <id> --text "完成：..." --status completed --percent 100`。
- 详细命令、HTTP 接口、提示词模板与冒烟验收见：`G:\codex\team-board\CODEX_BOARD_GUIDE.md`。
- 约定：不要手工编辑 `board.json`；只读 `source=deepseek` 的任务，不要改动或重排；不能访问 127.0.0.1 时先报告网络/沙箱限制。

## Codex 分派任务（2026-10-18，待认领）

DeepSeek Lead 已将卡点任务分派到看板 **source=codex**，Codex 请先读
[docs/windows/CODEX_ASSIGNED_TASKS_2026-10-18.md](docs/windows/CODEX_ASSIGNED_TASKS_2026-10-18.md)
并在看板认领：

| 任务 | 内容 |
| --- | --- |
| C1 | 修复 App 内自动查找微信对话按钮路径（UI 触发 search_not_found/result_not_found，代码级已 ok=true） |
| C2 | T12-01 0.4.x→0.5.7 真实升级/卸载/数据保留 + 干净环境回归 |
| C3 | T12-03 真实窗口停靠/弹窗/最小化/托盘恢复验收自动化 |
| C4 | 自动找对话 OCR 名字误识改进（郜亚禹类，保持 fail-closed） |
| C5 | T9/T10 替代验收：本地合成模型服务 + 双源仿真 |

认领命令：
`& G:\codex\wechat-lite-venv\Scripts\python.exe G:\codex\team-board\board.py claim --task C1 --owner codex --source codex`
（C2–C5 同理；只写 `source=codex`，不要改动 `source=deepseek` 的任务。）
