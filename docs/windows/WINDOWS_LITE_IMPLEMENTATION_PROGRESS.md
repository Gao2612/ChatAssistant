# Windows Lite 整改执行进度

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
| T9 记忆检索与实时建议整合 | 🟡 前半完成 commit `5a06f92`（conversation_binding/memory_retrieval/realtime + 20 项测试）；ui.py/analysis.py 集成待 T4-UI 释放后做 | 10k 合成早期事实可命中；scope 隔离/版本失效/绑定撤销已测；真实 UI 接线与端到端待续 |
| T10 多微信来源调度 | ⏳ 未开始（Lead，依赖 T9） | `source_registry.py`/`capture_scheduler.py` |
| T11 构建追溯与安装生命周期 | 🟡 工具与 main 入口接线已提交（`11ba51f`、`617a89f`），待 0.4 重建/隔离安装验证 | build_info/self-test/verify_install + `--version-json`/`--self-test`/`--lite-pdf-worker` 分发；正式安装升级/卸载与干净 VM 未验证 |
| T12 集中实机验收 | 🟡 矩阵与只读证据脚本已建（`b15194f`），未执行场景 | 待 T7–T11 合并后重建候选包；人工扫码/输入法/弹窗在 T12 |
| S-安装包中文化 | ✅ commit `f9e7c6d` | 内置简体中文语言文件（向导/卸载/任务/启动描述），ISCC 编译通过；随下一版安装包生效 |
| S-回复风格扩充 | ✅ commit `f9e7c6d` | 9 个预设 + 可自定义、禁滚轮、自定义文本保留；16 项 UI 定向通过 |
| S-微信隐藏/防重复启动 | 🟡 windows 队友实现中（task-17） | 发现托盘/隐藏/最小化窗口、显式恢复 API、已有进程不重复 Popen；UI 接线待 Lead |
| S-HTML/PDF 专属风格 | 🟡 history 队友实现中（task-18） | `style_profile.py`：筛选“我”的发言→本地特征→中文风格说明→加密派生记忆；UI 接线待 Lead |
| P1 测试进程退出崩溃 | 🟡 contracts 排查中 | 单模块通过、组合模块在 “Ran OK” 后 0xC0000409；不修完不把全量回归当 T12 证据 |
| S-DOCK 镜像预览/停靠开关 | 🟡 用户新要求，Lead 实现中 | 默认镜像只读预览，不改原生微信大小/位置；停靠改为默认关闭的显式开关；应用 resize 只缩放预览 |
| S-自动查找微信对话 | ✅ 真实验收通过（仅切换） | 恢复托盘窗口→OCR 搜索“田子夜”→点击结果+Enter→顶部 OCR 命中；未发送消息 |
| P1 测试退出崩溃 | ✅ commit `4d8642d` | tests/__init__ runner 后清理顶层 QWidget；28/60→0/60；全量 622 项 `discover -s tests -t .` exit 0 |
| S-TRAY-ACT 托盘隐藏下自动找对话/发送 | 🟡 找对话实测通过；发送方案同构待授权实测 | 临时恢复窗口→OCR 找对话→恢复托盘；发送需标题校验+焦点探针+双条件验证；UIA 实测不可用 |
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
