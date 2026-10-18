# Windows Lite 代码通读与验收清单（2026-10-18 基线）

目的：在整改前把"文档声称 / 代码实际 / 测试覆盖 / 真机证据"分开核对，并回答用户 2026-10-17 在 Codex 里留下的两条未完成指令是否已写入记录。本文只记录事实，不把待实施能力写成已交付。

## 0. 核对方法与基线

- 源码：`windows/lite/`（分支 `feat/windows-lite`，工作区未提交；本机没有 git CLI，无法直接执行 `git status/diff`）。
- 基线测试（venv：`G:\codex\wechat-lite-venv\Scripts\python.exe`）：

```text
python -m unittest discover -s tests -q
Ran 53 tests in 1.937s
FAILED (failures=2)
```

2 个失败均为 `tests/test_backend_settings.py`（上一轮 Codex 在额度中断前写的"先失败测试"），其余 51 项通过。该文件预期、当前代码并不支持：`reading_backend` 默认 `ocr`、`reading_model/reading_base_url/reading_api_key` 往返、`backend=local` + 空 Key、`reading_api_key` 不落明文。

## 1. 两条 Codex 指令的落盘核对（用户问题 2）

| 指令 | 文档记录 | 代码/测试记录 | 结论 |
| --- | --- | --- | --- |
| OCR 读取文字后没有自动分析（回归修复） | 已写入 `docs/windows/WINDOWS_LITE_IMPLEMENTATION_PROGRESS.md`「OCR 后自动分析 / 回归修复已落代码」；`AGENT_HANDOFF.md` 未单独提 | `liteapp/state.py` 的 `mark_pending()`/修订语义；`liteapp/ui.py` `tick()` 中 `clear_results(forget=False)`、`restore_analysis()`；`tests/test_auto_pipeline.py` 4 项、`tests/test_logic.py` 5 项相关用例，基线全通过 | **已记录、已落码、测试通过**；0.2 安装包不含该修复，2026-10-18 已随 0.3 包重建 |
| 读取聊天文字与分析都能选 Codex | 已写入 `WINDOWS_LITE_IMPLEMENTATION_PROGRESS.md`「执行决定」：读取/分析两套配置、视觉读取、本地模型、Codex app-server（图像输入、模型与账号状态 RPC，不读桌面私有凭据） | 基线时仅有 `tests/test_backend_settings.py`（23:48，先失败的测试）和 `test-output/codex-schema/*`（23:47 生成的 app-server 协议 schema）。2026-10-18 已落码 `settings/backends/reading/codex_backend/worker/ui` 并新增测试 | **记录已写入；2026-10-18 已落码并通过 86 项测试**（见第 5 节） |

## 2. 验收清单（整改前）

状态口径：✅ 有代码且测试/真机证据；🟡 有代码或文档，但缺实机或边界证据；❌ 未实现。

### A. 微信窗口与停靠

| # | 要求（来源） | 代码证据 | 测试/真机证据 | 状态 |
| --- | --- | --- | --- | --- |
| A1 | 按完整 exe 路径发现官方微信窗口，识别进程实例（`WINDOWS_LITE_PLAN`/验证记录） | `liteapp/discovery.py:84 discover()`、`process_identity()`、`WindowTarget` | `tests/test_discovery.py`；10-17 真机发现 1 个已登录窗口 | ✅ |
| A2 | 多窗口时由用户选择、不自动乱连 | `ui.py:scan()`、`choose_window()`；`settings.auto_connect` 仅在唯一窗口时自动连接 | `tests/test_logic.py:test_full_path_matching_and_ambiguous_selection` | 🟡 多开真机未验收 |
| A3 | 原生顶层窗口停靠，不改父级/样式；失败显示只读画面 | `liteapp/window_dock.py`（只 `SetWindowPos`）、`ui.py:sync_dock()`、`setMask` 开口 | `tests/test_window_dock.py`；10-17 真机鼠标命中原生微信 | 🟡 单击/滚轮/输入法/扫码/弹窗未逐项验收（验证文档明示） |
| A4 | 切页/最小化/退出时恢复微信位置 | `window_dock.detach()`、`set_minimized()`、`ui.py:show_history/show_settings/closeEvent` | 10-17 真机：切设置最小化、返回重停靠、退出恢复位置 | ✅（真机） |
| A5 | 窗口身份变化/关闭时安全断开 | `worker.py` 身份校验、`ui.py:tick()` closed/error 分支 | `tests/test_discovery.py` 身份用例 | ✅ |

### B. 采集与本地 OCR

| # | 要求 | 代码证据 | 证据 | 状态 |
| --- | --- | --- | --- | --- |
| B1 | 只读画面采集（WGC），不 hook/不改包/不读数据库 | `worker.py`（`windows_capture` + `SetProcessDpiAwarenessContext`） | 10-17 真机取帧、11 条可见消息 OCR | ✅ |
| B2 | 聊天区定位与标题识别 | `vendor/area.py:chat_area()`、`vendor/ocr.py:read_title()` | 真机快照含 11 条消息 | ✅ |
| B3 | 气泡归属（我/对方/灰字过滤）与同一气泡多行合并 | `vendor/ocr.py:who_said()/Reader.read()` | 仅间接；无独立单测 | 🟡 依赖真机效果 |
| B4 | 截帧新鲜度：旧帧 OCR 结果不得覆盖新画面 | `state.FrameFreshness`、`ui.tick()` 中 `frame_seq` 校验 | `test_logic.py:test_ocr_from_superseded_frame_is_discarded`、`test_auto_pipeline.py` | ✅ |
| B5 | OCR 快照不落盘、日志不打印正文 | `worker.py`（内存帧）、`save_config` 加密；测试日志不含正文 | 验证记录 + 代码检查 | ✅ |

### C. 自动分析链路（用户指令 1）

| # | 要求 | 代码证据 | 证据 | 状态 |
| --- | --- | --- | --- | --- |
| C1 | 新文字被确认后 2s 自动分析；纯画面抖动不误判 | `state.mark_pending()` 语义、`ui.tick()` 2s/10s 节流 | `tests/test_auto_pipeline.py` 4 项、`test_logic.py` 5 项（基线通过） | ✅ 代码与单测 |
| C2 | 分析中画面重检：旧结果先挂起，文字确认一致后恢复 | `ui.analysis_done()`、`restore_analysis()`、`_cached_analysis` | `test_auto_pipeline.py:test_visual_recheck_restores...`、`test_result_finished_during_recheck...` | ✅ |
| C3 | 文字真变化后旧建议不可见、不可复制 | `state.accepts()`、`copy_reply()` 校验 token | `test_changed_text_never_restores...`、`test_ui.py:test_new_snapshot_invalidates_visible_replies` | ✅ |
| C4 | 模型未就绪时提示而不是静默失败 | `backend_ready()`、`ui.tick()` 提示 | `test_auto_pipeline.py:test_missing_model_is_explained...`、`test_ui.py` | ✅ |
| C5 | 回归修复进入安装包 | — | 0.2 安装包构建于 23:11，修复在 23:46 之后；2026-10-18 已用修复后的源码重建 0.3 便携版并重新编译安装器（见第 5 节） | ✅ 源码与 0.3 包；🟡 未覆盖安装到 E:\ 验证升级 |

### D. 模型与授权

| # | 要求 | 代码证据 | 证据 | 状态 |
| --- | --- | --- | --- | --- |
| D1 | 官方 Sign in with ChatGPT（PKCE、state/nonce、JWKS 校验、DPAPI 保存） | `subscription.py:sign_in/access_token/_verify_identity` | `test_logic.py` SubscriptionTests 5 项；10-17 真实授权 7 模型 | ✅ |
| D2 | 模型目录来自账号，不复制 Codex 桌面选择 | `subscription.list_models/model_catalog` | 单测 + 真机 7 模型 | ✅ |
| D3 | Responses SSE 流式解析、store=False、失败码提示 | `subscription.analyze()` | 单测（假 SSE）+ 真机合成对话 3 条建议 | ✅ |
| D4 | 兼容 API（Chat Completions） | `analysis.py:analyze/build_payload/parse_result/request_json` | `test_logic.py` AnalysisTests；无真实 API 调用证据 | 🟡 |
| D5 | **读取/分析分别选择后端**（本地 OCR / 多模态 / Codex；ChatGPT / API / Codex / 本地模型） | `settings.py` 读取键 + DPAPI；`backends.py` 选项/就绪/校验/路由；`ui.py` 设置分卡；`worker.py` 远程读取画面 | `tests/test_backend_settings.py`、`test_backends.py`、`test_remote_reading_ui.py`；86 项通过 | ✅ 代码与单测（真实视觉/Codex 回合未验证） |
| D6 | 本地模型服务（OpenAI 兼容 localhost） | `analysis.DEFAULT_LOCAL_BASE`、`backends` local 后端、设置 UI | `test_backends.py` 假服务端；本机无本地模型服务 | ✅ 代码；🟡 真实服务未实测 |
| D7 | Codex 官方 app-server（账号/模型/图像输入，不读桌面凭据） | `codex_backend.py`：initialize/account/model/thread/turn、只读沙箱、审批 never、拒绝工具请求、超时中断 | `tests/test_codex_backend.py`（假服务端 8 项）+ 真实免费 RPC 探针（账号 plus、7 模型均 text,image） | ✅ 协议与账号目录；🟡 真实模型回合未执行（额度） |

### E. 历史记录与本地报告

| # | 要求 | 代码证据 | 证据 | 状态 |
| --- | --- | --- | --- | --- |
| E1 | HTML/PDF 纯文本解析、不执行脚本/远程资源 | `history.py:_PlainHTML/_sections_from_*` | `tests/test_history.py` | ✅ |
| E2 | 按账号+联系人隔离、DPAPI 加密、去重 | `history.py:HistoryStore` | 单测 | ✅ |
| E3 | 分页浏览、搜索、本地统计报告，不把片段数冒充消息数 | `history_ui.py`、`history_report.py` | `tests/test_history_report.py/test_history_ui.py` | ✅ |
| E4 | 真实 ExportWeChat HTML 校准发送人/时间 | 保守解析 | 验证文档明示未提供样本 | ❌ 未验证 |
| E5 | 全量历史模型分析 / 档案 / 趋势 / 实时检索 | 设计 T5–T9 | 未实现 | ❌（计划内） |

### F. 打包与交付

| # | 要求 | 代码证据 | 证据 | 状态 |
| --- | --- | --- | --- | --- |
| F1 | PyInstaller `--clean` 便携版 + 冒烟 | `build.ps1`、`ChatAssistantLite.spec` | 10-17 构建 0.2；2026-10-18 重建 0.3 并通过 `--smoke` | ✅ |
| F2 | Inno Setup 正式安装器 | `package.ps1`、`installer/ChatAssistantLite.iss`（版本 0.3.0） | 0.2 安装器 SHA-256 见验证文档；0.3 安装器 2026-10-18 重新编译，结果见第 5 节 | ✅ 构建；🟡 升级/卸载未实测 |
| F3 | 安装目录与源码/构建对应 | — | `E:\ChatAssistantLite` 仍是 10-17 的 0.2（exe 6735436 B） | 🟡 未覆盖安装 0.3 |
| F4 | 构建可追溯（版本/提交/清单） | 设计 T11 | 未实现；当前只靠安装器版本号与报告 | ❌（计划内） |
| F5 | 本轮整改后重建安装包 | `build.ps1` + ISCC | 2026-10-18 便携版 + 安装器均重建成功（见第 5 节） | ✅ 构建；🟡 真实用户安装流程未跑 |

### G. 测试与文档一致性

| # | 检查 | 结论 |
| --- | --- | --- |
| G1 | 测试套件是否全绿 | 整改前 51/53，2 个失败为新特性先失败测试 |
| G2 | 文档是否把计划写成已交付 | 设计/进度/交接文档明确区分，未发现把 T1–T12 写成已交付 |
| G3 | 遗留中间产物 | `test-output/codex-schema/*`、`nuitka-crash-report.xml`、多份 `build-*/dist-*` 在仓库内（被忽略）；无 `_reports/` 目录 |

## 3. 本轮整改范围（用户确认）

1. 读取（屏幕→文字）与分析（文字→建议）拆成两套独立配置，可分别选择后端与模型：
   - 读取：本地 OCR（默认，离线）/ 多模态模型（OpenAI 兼容视觉接口）/ Codex 应用（官方 app-server 图像输入）。
   - 分析：ChatGPT Plus/Pro 官方授权 / 兼容 API / 本地模型服务（OpenAI 兼容 localhost）/ Codex 应用（官方 app-server）。
2. 远程读取只发送当前画面；失败回退本地 OCR；读取与分析都受同一修订/过期保护。
3. 补齐失败测试 `tests/test_backend_settings.py`，新增后端路由、Codex 协议（假服务端）、远程读取解析与 UI 门控测试。
4. 不 commit/push，不自动发送消息，不保存真实聊天截图或正文日志；真实模型调用受额度限制时只做不消耗额度的 RPC 验证并在报告中标注缺口。

## 4. 风险与限制（本轮）

- 本机未安装/未运行任何本地模型服务（无 Ollama / LM Studio / 监听端口），本地后端只能以假服务端测试，真实可用性未验证。
- Codex 读取/分析的真实模型回合会消耗用户额度，本轮默认不执行；只验证 initialize / account / model 目录与假服务端协议。
- Codex 图像输入（`url` data URL）协议字段按 schema 实现，真实回合未验证。
- 远程视觉读取会把当前微信画面发送给所选第三方接口，需在 UI 明示；受保护窗口（FLAG_SECURE）截不到时仍走本地 OCR 失败提示。

## 5. 整改后更新（2026-10-18）

- 读取/分析双后端已落码：`liteapp/settings.py`（`reading_backend` / `reading_base_url` / `reading_model` / `codex_path`、DPAPI `protected_reading_key`）、`liteapp/backends.py`、`liteapp/reading.py`、`liteapp/codex_backend.py`、`liteapp/worker.py`、`liteapp/ui.py`。设置页拆为「文字读取 / 对话分析 / Codex 账户」；分析后端新增 `local` 与 `codex`，读取后端新增多模态 `api` 与 `codex`。
- 测试：`python -m unittest discover -s tests -q` → **86 项通过**（基线 51/53；新增 `test_backends.py`、`test_codex_backend.py`、`test_remote_reading_ui.py`、`fake_codex_app_server.py`；上一轮先写的 `test_backend_settings.py` 转绿）。
- 真实 Codex：本项目 `codex_backend.status()` 对官方 app-server 执行 initialize / account/read / model/list 通过：账号 `chatgpt` / `plus`，7 个模型均支持 `text,image`，进程退出干净；未发送聊天内容、未消耗模型额度。
- 打包：PyInstaller `--clean` 重建 0.3 便携版（`windows/lite/dist-release/ChatAssistantLite`），打包后 EXE `--smoke` 退出码 0；Inno Setup 编译 `dist-release/ChatAssistantLite-Setup.exe` 成功：
  - 大小 99,414,850 字节
  - SHA-256 `C84E0298BC82E654EFA880D3FF4B3914815DEDEC14045EE4E49DE71DEE75D191`
- 仍未验证：多模态读取真实接口、Codex 读取/分析真实模型回合、本地模型服务（本机无服务）、真实微信窗口下的远程读取质量与延迟、0.3 覆盖升级/卸载。
- 详细交付报告：`_reports/backend_read_analysis_split_report.md`。
