# Windows 实时助手便捷版：首版架构定稿与修订清单

> 历史架构记录。后续已完成 PyInstaller/Inno Setup 构建及部分真实微信/授权验证，下面关于尚无安装包、Nuitka 阻塞等描述不是当前状态。继续开发先读 [完整版设计](WINDOWS_LITE_COMPLETION_DESIGN_2026-10-17.md)、[实施计划](WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md) 和 [本机验证记录](WINDOWS_LITE_VERIFICATION_2026-10-17.md)。

日期：2026-10-16。审查对象：`feat/windows-lite` 分支的 `windows/lite/` 实现与本轮增补。

本文是本次首版实现的架构决策，范围以用户最新确认和 `WINDOWS_LITE_PLAN.md` 为准。较早完整产品方案中的嵌入、多账号持久库和历史 HTML 导入不作为本次交付门槛。纯逻辑/本地接口已做验证；真实 OCR、微信捕获、OpenAI 授权及安装包尚未通过验证。

## 1. 首版决策

采用 Python + PySide6 独立程序，保留现有模块；仓库主体已迁移到 `windows/`。官方微信由用户手动启动和登录；助手根据用户配置的完整 exe 路径发现窗口，唯一候选可自动连接，多个候选由用户选择。默认微信独立、助手放在旁边，不再修改微信父窗口、样式或焦点。现有“启动微信”按钮仅作为用户主动操作的可选快捷入口。

一次只采集一个活动窗口。每次得到的是完整的当前可见文字快照，不累积聊天历史，不按联系人名称合并记录；截图和实时聊天只在助手内存中。提供暂停校正、手动粘贴、可能意图/需求/建议动作/不确定性及三条可复制回复。发送始终由用户完成。

自动分析默认关闭；开启后须有去抖、速率上限和失败停止策略。自动读取界面不等于授权自动出网。选择后端、首次开启自动分析时，界面明确显示目的地和发送范围。

用户希望使用 ChatGPT Plus/Pro 或 Codex 订阅：订阅首选官方 Sign in with ChatGPT (SIWC) 本地开源应用流程，API 保留为用户主动配置的可选项。订阅要求用户在本软件单独授权 ChatGPT 套餐使用，并按所选账号读取模型；不会自动复制 Codex 桌面当前模型。不得把 API 费用说成订阅内用量，不得在订阅失败时自动切换付费 API。Codex app-server 可作为未来单独评估的适配器，不作为首版前提。

不加入数据库、联系人长期画像、HTML 导入、后台多账号并行、自动填入或发送。保留这些未来扩展的输入输出边界即可，不预建通用插件系统或任务平台。

## 2. 方案取舍

| 路径 | 决策 | 原因 |
| --- | --- | --- |
| 继续围绕 SetParent 嵌入开发 | 本版排除 | 实机单击与滚动已失败，不能阻塞实时助手；已有探针保留作独立研究 |
| 沿用现有单体 UI，直接增加订阅调用 | 不采用 | 状态失效、模型任务和平台能力会继续混在 UI，难以覆盖竞态 |
| 保留 Python/Qt，增加薄控制器与后端契约 | 采用 | 复用已有代码，修复快照时效与账号边界，交付最短且可测试 |

### 2.1 参考仓库分工

[crush-monitor](https://github.com/FerryCorleone/crush-monitor) 是历史文本分析方向的参考：它围绕粘贴/导入的对话，提供逐句情绪与意图标签、回复表现评估、多个模型服务配置和本地保存。它不实现官方微信窗口发现、WGC 实时采集或嵌入，因此不替代本版的实时链路。后续 ExportWeChat HTML 分析可以评估其逐句结构化结果、长记录分批和模型设置体验；分数应当作为有限证据下的参考，不能表述为真实好感概率。本版不复制其前端或模型实现，也不引入它的恋爱场景评分规则。

当前版与历史版继续共享“输入快照、模型后端、结构化结果”边界，但各自有单独的数据生命周期：实时版默认只在内存处理当前屏幕；历史版将来由用户明确导入 HTML 后，按项目保存在本地并提供可追溯的统计和结论。两类记录不自动合并。

## 3. 模块边界

```text
官方微信窗口
  → discovery：路径、进程、窗口验证
  → worker 子进程：WGC → 最新帧 → 本地 OCR → 带来源的快照
  → controller + state：状态机、时效、手动校正、分析调度
  → ModelBackend：ChatGPTPlan (SIWC) 或 CompatibleAPI
  → 结构校验 + 请求版本校验
  → Qt 纯文本显示 → 再校验 → 复制
```

| 模块 | 职责与依赖约束 |
| --- | --- |
| `state.py` | 纯 Python 数据与状态转换；不导入 Qt、Win32、网络；负责快照版本与接受条件 |
| `discovery.py` | Win32 候选枚举、完整路径及窗口所属进程校验；不启动、激活、嵌入微信 |
| `worker.py` | 独立进程管理 WGC/OCR；内存最新帧；输出健康、帧变化、快照、不可用和终止事件 |
| 新增薄 `controller.py` | 接收采集事件、维护 source 状态、去抖、分析取消和结果门禁；注入时钟与后端方便测试 |
| `analysis.py` / 后端文件 | 共享请求构造、输出 schema、错误分类；SIWC Responses 与兼容 API 传输各自隔离 |
| `settings.py` | 有版本的设置、API 密钥和 OAuth 凭据 DPAPI、后端选择；不保存聊天和联系人快照备注 |
| `ui.py` | 控件展示与用户事件；不直接改 `state.text/title/session`，不硬编码 API Key 门槛 |
| `main.py` / 打包配置 | 单一启动入口、multiprocessing freeze support、资源定位、关闭清理 |

不要求一次重写所有文件；先把状态转换和模型调用入口移出 UI，再逐项补齐契约。现有纯文本 QLabel、QPlainTextEdit、DPAPI、完整路径匹配、全量快照替换和结果 revision 检查应保留。

## 4. 最小数据契约

### 4.1 来源与快照

`WindowBinding`：`binding_id`（随机连接代次）、`hwnd`、`pid`、`process_started_at`（可取得时）、`exe_path`。每次新连接生成新 binding；HWND/PID 不是永久账号标识。发现器和 worker 均验证窗口仍属于该进程和路径，避免 HWND 被复用后只因 IsWindow 为真而继续采集。

`CaptureEvent`：`binding_id`、递增 `event_seq`、`kind`、单调时钟时间、健康状态；快照另带 `frame_seq`、`captured_at`、`recognized_at`、识别标题、按屏幕顺序排列的消息、局部画面签名和质量告警。

`ConversationSnapshot`：来源为 live/manual/edited、`snapshot_id`、内容 revision、当前会话观察代次、消息列表、显示标题、来源 binding、是否已由用户确认。标题仅用于显示和观察变化，不作为联系人 ID。

`AnalysisRequest`：唯一 `request_id`、不可变输入快照、输入 hash、`source_epoch`、内容 revision、`config_epoch`、backend id、模型选择及临时关系备注。网络调用不读取正在变化的编辑器或全局配置。

`AnalysisResult`：原请求标识、结构化结果、实际 backend/model（接口能够提供时）、完成时间、可选用量。请求中未提供而接口也未返回的信息标为未知。

### 4.2 最小来源状态

`disconnected → connecting → live_pending → live_ready`。

从 live 状态可进入 `unavailable`、`error`、`paused_edit` 或 `manual_edit`。恢复连接必须创建新 binding；手动/校正状态使用自己的冻结快照，不继续声称与微信实时同步。

任何来源切换、窗口失效、最小化、无法识别标题、后台采集失联、配置变更、正文编辑都使旧分析失效。关闭自动分析应取消待发任务并明确处理进行中的任务；若使旧请求失效，应同时禁用已有复制按钮，避免 revision 与控件状态不同步。

## 5. 快照时效与同名会话

仅用 session/revision 不能表达“画面已经变化，但 OCR 尚未结束”和“采集停止收到新帧”。首版现已增加共享帧序号与单调捕获时间：

1. WGC 回调记录整帧像素变化的序号及捕获时间；UI 观察到新序号先进入 `live_pending`，随后执行 OCR。进入 pending 即使旧模型尚未返回，也不接受其结果为当前可复制建议。
2. OCR 结果携带实际处理的帧序号；处理期间若出现更新帧，丢弃旧识别并继续识别最新帧。队列保持最新值，不能积压多秒旧画面。
3. worker 独立发送健康事件，区分进程存活、捕获流健康和 OCR 完成。`last_packet_at` 不能用任意状态包代替最新有效画面的时间。
4. 当前 windows-capture 的 `minimum_update_interval` 只限速，不保证静止画面持续收到回调。因此帧静止时不伪造心跳，也不按聊天文字未变判过期；我们目前无法区分窗口卡住与静止画面，连接状态会显示捕获限制，需后续合成窗口实测。
5. 设置独立的初始化、捕获健康和 OCR 耗时阈值；初始建议初始化 30 秒、健康失联 5 秒、OCR 处理 10 秒后停止自动分析并显示原因。最终值依据本机合成窗口测试调整，不使用一个全局超时混淆正常模型加载和失联。
6. 每次发送、显示结果、复制分别校验 request id、source/config epoch、内容 revision、当前模式和健康。复制校验失败就禁用结果并提示刷新。仅检查按钮 enabled 不足够。
7. 有限采样不能绝对知道两帧之间是否发生切换；首版不承诺“任何同名会话必然准确识别”。不做历史拼接、不保留联系人身份；观察到标题/选中项/相关画面变化即使文字相同也重新确认。仍无法区分的相同视觉画面，UI 显示来源、识别时间并要求用户核对后自行发送。

画面签名应围绕会话标题、选中项及消息区，排除光标闪烁等无关变化，避免输入时持续失效。首版先实现保守整帧失效，再以有证据的局部签名减少误触发，不以模糊文本去重换取稳定。

原全局持久化 `settings.relationship` 已移除。关系备注改为 UI 当前会话临时字段；窗口连接或观察到会话标题变化时清空。通用回复风格仍可持久化。手动模式下用户可填写备注。

## 6. 模型后端与订阅边界

统一接口只需五项：`probe()`、`list_models()`、`usage_status()`、`analyze(request, cancel_token)`、`close()`。返回能力标志区分“不支持”“未登录”“暂时不可用”，不要让 UI 以 `api_key` 是否存在判断所有后端可用。

### 6.1 CompatibleAPI

保留 Chat Completions 请求/响应适配、HTTPS 验证、大小限制、结构化输出校验与脱敏错误。补充禁止 HTTP 重定向，或明确只允许经过验证的同源 HTTPS 跳转；避免 Authorization 和正文因默认重定向转发到其它目标。支持可取消或有明确总时限的请求，取消后不会继续自动提交重试。

模型名称以用户配置为准；可选模型列表若接口提供才展示。API 用量和订阅额度分开标记，不合并为一个余额。没有配置 API 时不默认向预置第三方发送。

### 6.2 官方 ChatGPT 套餐（首选订阅后端）

采用 OpenAI Developers 的 SIWC ChatGPT plan usage 本地开源应用流程。首次以 `dynamic_agent_client` 注册，预先生成并保存本机稳定 `ext_agent_host_id`（如 `urn:uuid:<uuidv4>`）和授权返回的正式 `client_id`。系统浏览器执行 OAuth，使用随机 `state`/`nonce`、PKCE S256 和 `http://127.0.0.1:<port>/auth/callback`。交换授权码后，用 OpenAI JWKS 验证 ID token 的签名、issuer、audience、expiration、nonce 和账户身份；并检查 granted scopes 包含 `chatgpt.tokens.use.direct`。凭据按账户保存并由 Windows DPAPI 加密，refresh token 轮换时原子更新；不读取或复用 Codex 桌面认证文件。

用该账号 access token 请求 `GET https://api.openai.com/v1/models`，筛选 `visibility=list` 并显示官方返回的名称。分析使用公开 `POST https://api.openai.com/v1/responses`，每次 `store=false`、`stream=true`，输入完整包含所需上下文。使用 `instructions` 或 developer 消息，不发送 system role；不设置 SIWC 不支持字段（例如 `temperature`、`max_output_tokens`）。仅在 `response.completed` 后接受结果，并识别流中的订阅用量限制错误。禁止重定向、日志及响应正文泄漏。

用户需在授权页面同意此本地应用使用所选 ChatGPT 账户套餐。API key 和订阅登录是两个独立后端。登录拒绝、scope 缺失、token 过期和额度限制均明确告知，不转用 API。OpenAI 文档说明同一账号/workspace 下使用同一 client registration 的不同 host 会共享计划使用设置和限额，但未提供镜像 Codex 桌面 UI 当前模型/额度的接口；本应用只显示官方接口实际返回的内容。

### 6.3 “模型和额度同步”的准确含义

使用 SIWC access token 返回的当前账号模型目录。OpenAI 文档确认目录提供 `slug`/`display_name`，并建议切换账号后重新读取。用量状态只显示官方响应确实返回的数据；Models and inference 页面未定义通用订阅余额接口，因此首版说明“额度由 ChatGPT 账户管理；本应用暂不读取剩余量”。桌面 Codex 当前会话临时选择的模型不是此应用选择，UI 显示本应用单独选择的模型。

OpenAI 的账户与会话文档进一步说明：Plus 的五小时额度由所有获准使用 ChatGPT plan 的应用共享；Pro 不受该五小时限制。因此同一账号使用本地应用会计入官方共享额度，具体模型和功能限制仍以账号计划及授权返回为准。使用量页可链接 ChatGPT Settings → Usage；SIWC 没有定义从本应用查询剩余额度的通用接口，因此不绘制虚构的实时余额，也不镜像 Codex UI 当前选择。

这是官方文档规定的本地应用授权和推理能力，不代表本仓库已完成 OAuth 实现或实机登录验证。用量接口的具体返回和可展示范围仍须以当前官方响应为准。

## 7. 安全输出、错误与资源

所有模型内容按纯文本显示。继续校验三条非空候选、字段类型和长度，不将返回内容作为 HTML、脚本、命令或 UI 指令执行。聊天里的请求只是输入数据；程序权限必须由后端能力与应用代码限制，不能依赖提示词。

错误使用稳定错误码与本地化提示，记录组件、阶段、耗时、版本；不显示完整服务响应、URL 查询、Authorization、stderr 原文、提示词或聊天。Codex 输出同样要先过滤，再进入诊断。

采集只保留最新帧和必要短期 OCR 缓冲。停止先协作取消，再有界终止子进程；正常关闭需要清理 WGC、队列及模型任务，不只让 daemon 线程在退出时自行消失。强制终止只作用于本助手创建的进程。

自动分析对相同内容只发一次；网络/格式错误不自动重复刷请求，限流进入退避或等待用户重试。允许新快照替换尚未发送的待办，但单次只运行一个模型请求，旧结果永不覆盖新状态。

## 8. 当前代码缺口与落地顺序

首轮复核已修复状态表中标记为“已修复”的逻辑问题；未实现项仍是交付门槛。

| 优先级 | 缺陷和证据 | 最小修订 |
| --- | --- | --- |
| 状态 | 问题/剩余工作 | 当前处理 |
| --- | --- | --- |
| 已修复 | 捕获新画面时旧快照与模型建议仍可使用 | 共享帧序号、`live_pending` 和快照 frame_seq 门禁；27 项测试覆盖过期识别、界面状态与本地接口 |
| 已修复 | 自动分析关闭后仍可复制此前结果 | 失效时同时清除结果卡片、禁用复制 |
| 已修复 | 会话备注作为全局设置串到下一个联系人 | 改为当前窗口/标题临时备注，连接与标题切换清空 |
| 已修复 | 默认 HTTP redirect 可能转发 API Authorization 与正文 | API opener 禁止重定向；需增加本地 HTTP 假服务端到端验证 |
| 已落代码待实机 | OAuth PKCE/state/nonce、JWT/JWKS 验签、scope 检查、DPAPI 刷新、模型目录和 Responses SSE | 27 项逻辑/本地服务/Qt 测试覆盖签名/nonce/audience、模型筛选及流式完成；真实浏览器授权、token endpoint 和用量限制响应仍待验证 |
| 未完成 P0 | 静止帧与 WGC 捕获停滞无法区分，采集超时与关闭行为未充分测试 | 合成外部窗口观察回调行为，UI 明确展示限制并测试最小化/关闭/resize |
| 未完成 P1 | worker 只用 `IsWindow`，候选 id 只有 PID/HWND | 重新验证所属进程路径和进程启动时刻，避免句柄复用 |
| 未完成 P1 | 模型任务不可及时取消；窗口关闭后后台线程仍可能运行 | 可取消的网络后端、总超时与关闭收尾 |
| 未完成 P1 | 上游群聊识别按颜色启发且群名称可能沿用 | 合成 OCR 夹具，首版明确范围和方向不确定性 |
| 未完成 P1 | 尚无可交付便携包；PyInstaller onedir 与独立 Qt 示例均在 `QtWidgets` DLL 导入处失败 | `build.ps1` 已固定 Nuitka standalone + MSVC；当前机器无 MSVC/Windows SDK shell，构建会清楚地在前置检查失败。自动下载的 MinGW 缺少 `psdk_inc/intrin-impl.h`，仍需可用 MSVC 构建并完成打包 smoke |

现有 `Reader.read()` 返回整帧，未调用会吞重复的 `new_lines()`，这点正确，应保留。同一帧连续相同文本不能去重。

## 9. 验证与交付

按以下顺序逐层完成；前一层失败先修复，不让用户重复操作微信替开发者定位依赖错误。

1. **纯状态测试**：模拟同名联系人、相同连续消息、切换窗口、未知标题、修改备注/模型、关自动分析、过期结果、停滞心跳、慢 OCR 乱序、复制前失效。使用合成文本与假时钟。
2. **后端契约测试**：本地假 HTTP 服务覆盖正常响应、401/429、超时、取消、超大/坏 JSON、重定向；SIWC 用固定 JWKS/OAuth/SSE 夹具覆盖 state、scope、模型目录、限额和完成事件。真实授权用无隐私固定提示验证，不发送真实聊天来试通网络。
3. **离线 OCR 测试**：固定中文字体的合成微信式画面，人工预期气泡内容、方向、重复、缺名字和不可用状态；把 locator、RGB 通道和 OCR 分别验证。记录真实识别误差，不只验证函数没抛异常。
4. **Qt 测试**：离屏测试状态、按钮和纯文本渲染；可见窗口检查 100%/125%/150% 缩放与长文本。模拟模型返回时切换 source，确认复制禁用。
5. **捕获集成**：自行创建外部合成窗口测试 WGC、停滞、最小化、关闭、resize、动态变化、子进程退出。记录 WGC 静态帧行为，再确定健康阈值。
6. **打包检查**：先验证 Qt 官方推荐的 pyside6-deploy/Nuitka standalone，包含 OCR 模型、原生库、Qt 插件和来源许可。打包版 smoke 通过后测试真实子进程与离线 OCR；smoke 模式不枚举微信。无 Python 环境验证结果单独记录，未测不能写成通过。
7. **一次用户实机验收**：前六层通过后，仅集中验证当前微信版本的自动发现、中文气泡识别、切换、最小化恢复和手动复制。用户不必重复确认已证明失败的嵌入；没有真实微信通过前明确“未验证该版本兼容”。

第一交付物是经过上述测试的便携目录和明确启动入口；安装器可在便携版稳定后增加，不为“只有一个文件”牺牲 OCR 启动和资源定位可靠性。提供支持范围、已验证 Windows/微信/缩放/依赖版本、失败项和未测项，不用一个“测试通过”概括全部。

## 10. 官方资料与审查证据

订阅后端方案按 OpenAI Developers 官方 [Sign in with ChatGPT 概览](https://developers.openai.com/siwc/token-sharing-open-source)、[注册与登录](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)、[模型和推理](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)、[账户、会话和用量](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions) 及 [预览限制](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations) 整理。官方将 ChatGPT plan usage 定位为开源和本地托管应用的可选能力；商业/远程托管应用需另行联系 OpenAI。

已阅读根 `AGENTS.md` 的适用边界、Windows 详细方案、交接文档、嵌入实机报告、便捷版计划及 `windows/lite/` 当前代码。上游引用固定为 `jev-chat/jev-chat-windows` 提供的提交 `c6d3a10474ce1ff3b7f8f6402b3b5e0e58fad5f1`；当前 vendor 仅用于本地 OCR/定位，发布仍需来源说明和依赖许可清单。

本轮在允许本机环回假服务的环境运行 `python -B -m unittest discover -s tests -v`：27 项通过；`compileall` 和 Qt offscreen `main.py --smoke` 通过。PyInstaller 应用和最小 Qt 示例都在导入 `QtWidgets` 时失败；Nuitka standalone 构建目前要求本机 MSVC + Windows SDK，当前机器没有 `cl.exe`，自动下载的 MinGW 缺少 `psdk_inc/intrin-impl.h`。真实微信采集、离线 OCR、远端模型请求、SIWC 浏览器授权及独立打包尚未验证。产品代码和文档仍在本地分支，当前改动尚未提交。
