# Windows ChatAssistant 完整版实施计划

> 2026-10-18 前置整改：用户补充的 T0a/T0b（读取/分析双后端、两端模型记忆、兼容 API 厂商预设与模型列表读取、ChatGPT 绑定持久化与手动断开）已先落地，见 [执行进度](WINDOWS_LITE_IMPLEMENTATION_PROGRESS.md)。T1–T12 任务定义与依赖不变。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有 0.2 原型完成为可验收的实时微信助手、可溯源历史记忆分析和 Windows 安装交付。

**Architecture:** 保留 PySide6 桌面应用和原生顶层停靠，建立共享账号服务与模型队列。历史通道使用带范围校验的加密记录库、可恢复全量分析及本地检索，为实时建议提供证据。

**Tech Stack:** Python 3.12、PySide6、Win32、Windows Graphics Capture、RapidOCR、SQLite、Windows DPAPI、Responses HTTP/SSE、PyInstaller、Inno Setup。

**Spec:** [完整设计](WINDOWS_LITE_COMPLETION_DESIGN_2026-10-17.md)。必须同时阅读设计与 [0.2 验证基线](WINDOWS_LITE_VERIFICATION_2026-10-17.md)。

## Global Constraints

- 本文全部任务尚未执行；打勾需要真实验证输出，不继承历史成功结论。
- 只修改 `windows/lite/` 和本任务相关文档，保留 Android 工程。
- 不修改官方微信进程，不自动发送消息，不直接读微信数据库，不强制破解客户端多开限制。
- 不保存真实截图/OCR 日志；测试以合成样本为主。记录数量、阶段、版本和脱敏错误。
- 当前显示名称不等于稳定账号/联系人身份。每个持久化操作必须携带 `ScopeKey`。
- 所有模型结果要经过完成事件、输出 schema、证据范围和请求版本校验。
- 原文、联系人、报告和索引载荷本地加密；不创建明文持久全文索引。
- 正文资料作为不可信输入，不能让模型输出驱动文件、网络、窗口操作或发消息。
- 任务只生成本地可审查更改，不自动 commit/push/发布；遵守本次发布授权范围。

## Review Focus

1. 同名联系人、同名账号及窗口重启：T1/T5/T9/T10 验证错误范围永不取数、旧绑定失效。
2. 删除资料或切换会话时请求仍在运行：T3/T7/T9 验证旧结果丢弃且不可复制。
3. 断网、晚到 OAuth 回调、轮换令牌：T2/T3 验证保留有效注册并限制重试。
4. 长历史、重复来源、扫描 PDF 和解析缺页：T6/T7/T8 验证覆盖率和引用，不把片段数冒充消息数。
5. 混合 DPI、弹窗、异常退出和安装回滚：T4/T11/T12 验证微信恢复、用户数据保留和旧程序拒写新库。

## 分工与依赖

T1 先由主智能体固定接口。此后 A 线负责 T2/T3，B 线负责 T4，C 线负责 T5/T6；允许并行但不同时编辑同一文件。主智能体串行集成 `ui.py`，再执行 T7/T8/T9。T10 依赖实时稳定和范围隔离，T11 可提前准备，T12 最后验收。每个任务完成后做针对性审查；整体验收前另做一次独立代码审查。

每个任务的标准循环：先添加下列验收用例并确认在现状下失败 → 最小实现 → 定向测试通过 → 检查 diff → 记录本地交接。不要把“函数存在”当有效测试。

以下命令在 `windows/lite` 运行，`python` 指已安装 `requirements-build.txt` 的 Python 3.12；本机可使用 `G:\codex\wechat-lite-venv\Scripts\python.exe`。功能任务只运行相关测试，最终才运行全套。

## T1：范围、来源、结果版本契约及单实例

**Files:** 新建 `liteapp/contracts.py`、`liteapp/single_instance.py`；修改 `liteapp/state.py`、`main.py`；新增 `tests/test_contracts.py`、`tests/test_single_instance.py`。

**Interfaces:**

```python
ScopeKey(wechat_account_id: str, person_id: str)  # frozen dataclass
SourceKey(exe_path: str, pid: int, process_started: float, hwnd: int)
AnalysisKey(source: SourceKey, conversation_epoch: int, snapshot_digest: str,
            binding_revision: int, memory_version: int, inference_revision: int)
EvidenceRef(scope: ScopeKey, source_id: str, record_id: str, start: int, end: int)
CancellationToken.cancel() -> None
CancellationToken.is_cancelled() -> bool
InstanceGuard.acquire_or_activate(data_dir: Path) -> bool
```

- [x] 添加用例：相同 HWND/PID 但不同进程启动时刻是不同 SourceKey；任一 AnalysisKey 字段变化使旧结果不被接受；空账号/联系人 ID 被拒绝。
- [x] 用两个独立进程验证同一数据目录仅一个实例可写，第二个只发送激活消息；不同隔离测试目录可独立运行。
- [x] 实现不可变契约、版本比较、取消令牌和用户会话内单实例；保留现有状态测试行为。
- [x] 运行 `python -m unittest tests.test_contracts tests.test_single_instance tests.test_logic -v`，通过后发布契约给各工作线，不随意重命名接口。

> 2026-10-18 完成（commit 见 PROGRESS）：`contracts.py`（ScopeKey/SourceKey/AnalysisKey/EvidenceRef/CancellationToken，frozen + fail-closed）、`single_instance.py`（`<data_dir>/instance.lock` 文件锁判唯一写者 + 回环 socket 随机令牌只投递激活，第二实例不读写用户数据）、`state.py` 作用域/摘要/分析键、`main.py` 启动抢锁与主线程激活轮询。定向 55 项、全套 137 项通过；双进程与 `main.py --smoke` 实测退出码 0。真实桌面前台激活留 T4/T12 实机验收。

## T2：统一 ChatGPT 账号注册与授权生命周期

**Files:** 新建 `liteapp/account_store.py`；修改 `liteapp/settings.py`、`liteapp/subscription.py`；设置 UI 由主智能体集成；新增 `tests/test_accounts.py`、`tests/test_auth_lifecycle.py`。

**Interfaces:**

```python
AccountStore.list_registrations() -> list[RegistrationInfo]
AccountStore.select(registration_id: str) -> None
AccountStore.access_token(registration_id: str, cancel: CancellationToken) -> str
AccountStore.sign_out(registration_id: str, cancel: CancellationToken) -> SignOutResult
AccountStore.save_refreshed(registration_id: str, expected_revision: int, credentials: dict) -> bool
AuthAttempt.start(registration_id: str | None) -> AuthAttemptInfo
AuthAttempt.cancel() -> None
```

`RegistrationInfo` 只含注册 ID、可显示标签、身份/权限状态及模型目录版本，不暴露令牌。`SignOutResult` 区分已撤销和仅本地退出。

- [ ] 用例：0.2 单账号配置迁移后 client_id/host_id 不变；相同邮箱的两个注册互不覆盖；并行请求只发生一次刷新，设置保存不能写回旧令牌。
- [ ] 用例：不相关路径、错误 state、过期事务回调不完成当前授权；取消立即停止监听；身份有效但无套餐权限保存为“仅登录”。
- [ ] 用例：临时网络故障保留凭据；终止刷新错误清令牌并保留注册映射；退出只影响选择的注册；撤销失败结果明确标识。
- [ ] 实现单写入者/原子保存与账号服务；刷新前从唯一权威记录取最新令牌；将 UI 工作线程持有的配置快照改为 registration_id。
- [ ] 运行 `python -m unittest tests.test_accounts tests.test_auth_lifecycle -v`；先用假授权服务验证回调和轮换，再用合成提示完成一次官方安装版授权验收（T12）。

## T3：通用模型层、取消和任务调度

**Files:** 新建 `liteapp/inference.py`、`liteapp/inference_queue.py`、`liteapp/inference_schemas.py`；改 `analysis.py`、`subscription.py`；新增 `tests/test_inference.py`、`tests/test_inference_queue.py`。

**Interfaces:**

```python
InferenceRequest(task_id: str, kind: str, profile_id: str,
                 input_data: dict, schema_version: str, version_key: str)
InferenceGateway.run(request: InferenceRequest, cancel: CancellationToken) -> InferenceResult
InferenceQueue.submit(request: InferenceRequest, priority: str) -> TaskHandle
InferenceQueue.cancel_scope(scope_id: str) -> None
```

`kind` 固定为 realtime_reply/history_extract/history_reduce；`InferenceResult` 为 validated_data、request_id、耗时与可用 usage，不含原始 HTTP 诊断正文。`TaskHandle` 提供 cancel 和完成回调，回调回到 Qt 主线程。

- [ ] 用例：已经产生 delta 后出现 failed/incomplete 不能视作成功；嵌套 `response.error` 的额度错误能暂停任务；重定向不能带出凭据和对话。
- [ ] 用例：切换/关闭取消可关闭流连接；超时按连接 10 秒、无数据 30 秒和总时限执行；旧结果不回填。
- [ ] 用假时钟验证全局在途数量最多 1、同来源待办只保留最新、多个来源轮转、历史等待 5 次后得到一次执行机会。
- [ ] 拆出与提示词无关的 SSE 传输和三套 schema；普通 API 后端遵循自己的协议，ChatGPT 后端只发送允许字段。
- [ ] 运行 `python -m unittest tests.test_inference tests.test_inference_queue -v`。使用本地假服务测试 401/403/429/503、断流、超大响应和取消，禁止用真实客户资料试网络。

## T4：原生停靠、焦点、最小化及异常恢复

**Files:** 新建 `liteapp/window_manager.py`、`liteapp/recovery_guard.py`；改 `window_dock.py`、`discovery.py`、`worker.py`、`main.py`；新增 `tests/test_window_manager.py`、`tests/fixtures/native_chat_fixture.py`。

**Interfaces:**

```python
WindowManager.attach(source: SourceKey, target_rect: tuple[int, int, int, int]) -> DockStatus
WindowManager.set_view(mode: str) -> None  # chat/settings/history/minimized
WindowManager.detach(restore: bool = True) -> None
WindowManager.capture_health(source: SourceKey) -> CaptureHealth
```

- [ ] 用例：句柄复用拒绝操作；助手只恢复自己管理的窗口；已有独立弹窗不被位置循环盖住；跨 DPI 坐标往返误差不超过 2 个物理像素。
- [ ] 增加外部合成聊天窗口，可产生文本变化、弹窗、resize、最小化与关闭；心跳和静止画面变化分别验证。
- [ ] 增加遮挡回归：几何完全不变时，在助手与微信之间插入第三个合成窗口，再激活助手；中央必须只露出微信或明确只读回退。单独检查所选 HWND 的捕获帧不能含第三窗口标记；其他应用真正处于前台时不被助手强行覆盖。
- [ ] 实现登录到聊天主窗的可信接替、事件驱动几何更新、状态区分、故障时只读回退；不调用 SetParent，不用循环激活抢焦点。
- [ ] 恢复守护只接受原助手创建的恢复记录；主程序异常退出后验证身份再恢复几何；原微信关闭后不能误操作复用 HWND。
- [ ] 运行 `python -m unittest tests.test_window_dock tests.test_window_manager tests.test_discovery -v`；可见合成测试输出各场景结果，真实微信输入法/扫码在 T12 集中做。

## T5：历史数据 V2 与强制范围校验

**Files:** 新建 `liteapp/history_models.py`、`liteapp/history_migrations.py`；改 `history.py`；新增 `tests/test_history_v2.py`、`tests/test_history_migrations.py`。

**Interfaces:**

```python
HistoryStore.import_records(scope: ScopeKey, source: ImportSource, records: Iterable[ImportedRecord]) -> ImportResult
HistoryStore.iter_records(scope: ScopeKey, source_version: int) -> Iterator[HistoryRecord]
HistoryStore.read_evidence(ref: EvidenceRef) -> EvidenceText
HistoryStore.delete_source(scope: ScopeKey, source_id: str) -> int  # new memory_version
HistoryStore.snapshot(scope: ScopeKey) -> SourceSnapshot
```

`ImportedRecord` 包含正文、原始顺序、message/fragment 类型、可空发言人/时间、locator、质量标记。`SourceSnapshot` 固定 source IDs、版本、record/字符范围清单。现有 list/search/stats 方法同步增加 ScopeKey，不保留可跨账号调用的快捷入口。

- [x] 用例：跨账号 person_id/source_id 的读、删、导入均被拒绝；同名联系人独立；删除来源同步提高版本并失效派生记忆。
- [x] 构造 0.2 加密数据库，迁移前后原文数量/顺序/摘要一致；旧片段保留 fragment 类型；中途失败回滚，不留下部分新表数据。
- [x] 实现事务迁移、受控备份、schema 版本门禁和强制 scope 查询；持久内容仍由 DPAPI 保护。
- [x] 运行 `python -m unittest tests.test_history tests.test_history_v2 tests.test_history_migrations -v`；检查数据库文件不含合成样本明文标识。

> 2026-10-18 完成：`history_models.py`（frozen 模型 + fail-closed）、`history_migrations.py`（受控备份、单事务迁移、user_version 门禁、失败回滚）、`history.py` V2 六表强制 `ScopeKey`；`source_set_version`/`memory_version` 语义、`iter_records` 版本不符 fail-closed、`delete_source` 失效派生记忆；`history_ui.py`/`history_report.py` 最小适配（T6 用 ImportJobs 替换）。定向历史 38 项通过；排除并行 T4 窗口线的全模块 199 项通过；DPAPI 明文扫描 PASS。接口清单见 `_reports/T5_report.md` §3。

## T6：HTML/PDF 解析、源位置与导入任务

**Files:** 新建 `liteapp/importers/base.py`、`html_generic.py`、`exportwechat_html.py`、`pdf.py`、`liteapp/import_jobs.py`；改 `history_ui.py`、依赖清单；新增 `tests/test_importers.py`、合成格式夹具。

**Interfaces:**

```python
Parser.probe(path: Path) -> FormatMatch
Parser.parse(path: Path, cancel: CancellationToken) -> Iterator[ImportedRecord]
ImportJobs.start(scope: ScopeKey, path: Path, enable_page_ocr: bool) -> str
ImportJobs.progress(job_id: str) -> ImportProgress
```

- [ ] 用例：HTML 脚本/远程资源不执行不请求；UTF-8/UTF-16/GB18030 能保留文本；相同连续正文保留；同文件重导不增加记录。
- [ ] 用例：PDF 页码/顺序准确，混合扫描页只对无文字页 OCR，取消不提交半条记录，损坏/密码页给出页级问题；原图不进入长期日志。
- [ ] PDF 页面渲染选本地 `pypdfium2`，复用现有 OCR；实施时固定经测试的版本及许可。解析/渲染在受控子进程中进行：文件上限 100 MB，默认渲染 150 DPI、每页最多 1200 万像素，单页解析/OCR 上限 30 秒，超时页记录失败并可单独重试。HTML 解析上限 30 秒；这些上限在配置中集中定义并测试边界。
- [ ] 使用标准不含 V8/XFA 的发行构建，显式关闭页面/位图句柄；随二进制携带 PDFium 及依赖许可。依据 [pypdfium2 官方说明](https://pypdfium2-team.github.io/pypdfium2/readme.html) 核验所选 wheel 的打包内容。
- [ ] `exportwechat_html` 只接受能识别的实际格式特征；没有真实导出样本时不启用“精确支持”标记，继续通用回退。取得样本后锁定格式签名和脱敏回归夹具。
- [ ] 运行 `python -m unittest tests.test_importers tests.test_history_v2 -v`；用 10,000 条合成记录验证首/中/尾引用可跳回原文，并记录导入时长。

## T7：可恢复的全量历史模型分析

**Files:** 新建 `liteapp/history_jobs.py`、`history_prompts.py`、`history_analysis_store.py`；使用 T3/T5 接口；新增 `tests/test_history_jobs.py`、`tests/test_history_coverage.py`。

**Interfaces:**

```python
HistoryJobs.start(scope: ScopeKey, profile_id: str) -> str
HistoryJobs.pause(job_id: str) -> None
HistoryJobs.resume(job_id: str) -> None
HistoryJobs.cancel(job_id: str) -> None
HistoryJobs.progress(job_id: str) -> JobProgress
HistoryJobs.report(scope: ScopeKey, source_version: int) -> HistoryReport
```

`JobProgress` 必须包含成功记录数、字符范围覆盖率、待办/失败批次、解析缺页数；输出 fact/inference/open_question 三类内容，证据是 T1 EvidenceRef。

- [ ] 用例：长记录跨批切片全部覆盖且重叠不重计；首、中、尾植入的事实都能进入对应批次；报告无证据引用被拒收。
- [ ] 用例：第 3 批失败后重启只恢复未完成部分；删除来源使旧汇总失效；同文件重导不产生新模型任务；模型版本变化不复用旧分析缓存。
- [ ] 实现 12288 字节正文批次、最多 2 条/2048 字节重叠、32768 字节请求上限和分层合并；上下文超限缩小批次并保留原引用偏移。
- [ ] 批次提交采用事务，只有完成和校验通过才记录成功；流中断不“补齐”覆盖率。任务启动明确固定所选来源范围及模型，增量自动分析使用档案级设置。
- [ ] 运行 `python -m unittest tests.test_history_jobs tests.test_history_coverage -v`；用假模型确定性核对覆盖，再用少量合成历史做实际服务验收。

## T8：档案、趋势、统计与任务界面

**Files:** 新建 `liteapp/profile_ui.py`、`liteapp/history_statistics.py`；改 `history_ui.py`、`history_report.py`；新增 `tests/test_profile_ui.py`、`tests/test_history_statistics.py`。

**Interfaces:**

```python
HistoryStatistics.compute(scope: ScopeKey, snapshot: SourceSnapshot) -> StatisticsReport
HistoryPage.open_evidence(ref: EvidenceRef) -> None
ProfilePanel.set_report(report: HistoryReport) -> None
```

- [ ] 用例：片段只有片段统计；无可靠发送人或时间时回复时延/主动比例显示不可计算；重叠来源明确提示；模型推断与事实显示不同标签。
- [ ] 用例：报告引用点击跳到正确页/记录；已删除的证据不能打开旧缓存；任务暂停/继续/失败批次状态与实际进度一致。
- [ ] 实现原文/档案/趋势/统计标签，档案显示需求、偏好、承诺、待办、冲突及时间线；长任务使用独立任务列表，不阻塞聊天。
- [ ] 历史主界面改为“当前联系人 + 导入聊天记录”；账号选择移到设置，首次导入确认档案关联并创建 UUID。次要入口“选择其他联系人”保留独立离线导入；不再要求主流程先填写账号或手动新建联系人。
- [ ] 运行 `python -m unittest tests.test_profile_ui tests.test_history_statistics tests.test_history_ui -v`，在 100%/125%/150% DPI 检查长中文和键盘导航。

## T9：记忆检索与实时建议整合

**Files:** 新建 `liteapp/memory_retrieval.py`、`liteapp/conversation_binding.py`、`liteapp/realtime.py`；改 `ui.py`、`analysis.py`、`state.py`；新增 `tests/test_memory_retrieval.py`、`tests/test_realtime_memory.py`。

**Interfaces:**

```python
ConversationBindings.bind(source: SourceKey, conversation_epoch: int, scope: ScopeKey) -> Binding
ConversationBindings.resolve(source: SourceKey, conversation_epoch: int) -> Binding | None
MemoryRetriever.retrieve(scope: ScopeKey, query: str, memory_version: int,
                         max_items: int = 8, max_bytes: int = 8192) -> MemoryContext
RealtimeController.on_snapshot(snapshot: ChatSnapshot) -> None
```

`Binding` 带 revision；`MemoryContext` 包含经过范围验证的证据和版本；`ChatSnapshot` 包含 SourceKey、会话 epoch、帧序号、标题、消息、有效性状态。标题变化只是失效信号，不当持久 ID。

- [ ] 用例：两位同名联系人从不互取记忆；没有明确绑定则历史为空；窗口重启/可观测会话变化撤销旧绑定；来源删除立即废弃分析。
- [ ] 以 10,000 条合成历史测试检索能命中早期事实和未完成承诺，而不只取尾部；结果不超过 8 条/8192 字节且每条都可定位。
- [ ] 实现范围内存索引、中文词/字片段匹配、BM25 排序、邻近原文扩展；切换 scope 释放旧索引，持久化派生结果加密。
- [ ] 接入当前对话+历史证据提示词，扩展右侧“当前档案/引用依据”；建议复制前再次比对 AnalysisKey；过时价格/库存不能作为当前承诺直接复述。
- [ ] `MainWindow.show_history` 传递当前已确认会话的来源、epoch、标题和 Binding；HistoryPage 自动预选绑定档案。用例覆盖从联系人 A 切到 B 后导入只落 B、识别未就绪时不继承 A、同名联系人不自动合并。
- [ ] 保持默认自动分析、2 秒稳定等待、来源 10 秒最短间隔；右栏 320 DIP 向右扩展，屏幕不足改独立工具窗。
- [ ] 运行 `python -m unittest tests.test_memory_retrieval tests.test_realtime_memory tests.test_ui -v`；外部合成窗口 → 真 OCR → 假模型 → Qt 建议走完一次自动链路。

## T10：多微信来源调度

**Files:** 新建 `liteapp/source_registry.py`、`liteapp/capture_scheduler.py`；改 `realtime.py`、`window_manager.py`、`ui.py`；新增 `tests/test_multi_source.py`。

**Interfaces:**

```python
SourceRegistry.discover(exe_path: str) -> list[SourceInfo]
SourceRegistry.assign_account(source: SourceKey, wechat_account_id: str) -> None
CaptureScheduler.enable(source: SourceKey, enabled: bool) -> None
CaptureScheduler.activate(source: SourceKey) -> None
```

- [ ] 两个合成来源交错更新、慢 OCR/慢模型乱序返回，结果始终归正确来源；一个来源关闭不停止另一个。
- [ ] 实现默认最多 3 个监控来源、活动来源优先的 OCR 调度和每来源独立状态。首轮可保留每来源采集子进程；模型请求仍由 T3 全局调度。
- [ ] 后台监控默认关闭；启用后只处理具备有效新帧的正常窗口，最小化显示暂停；切活动来源时不抢其他账号输入焦点。
- [ ] 运行 `python -m unittest tests.test_multi_source -v`；用 2 个真实微信窗口做 30 分钟兼容测试后才声明支持该版本多开监控，记录 CPU/内存/句柄趋势。

## T11：构建追溯与安装生命周期

**Files:** 新建 `tools/write_build_info.py`、`tools/verify_build_info.py`、`tools/verify_install.ps1`、`liteapp/self_test.py`；改 `build.ps1`、`package.ps1`、`main.py`、`installer/ChatAssistantLite.iss`；新增 `tests/test_build_info.py`。

**Interfaces:** `--version-json` 输出版本与源码摘要，`--self-test --data-dir <isolated>` 使用指定隔离目录执行资源/数据库/合成 OCR 检查，不枚举微信、不访问正式授权。

- [ ] 用例：构建过程中改源码使最终校验失败；打包内清单和磁盘清单不符阻止发布；未提交代码清晰标 dirty；输出不含本机密钥/个人目录列表。
- [ ] 保留 PyInstaller `--clean`，构建前后校验源码摘要；随包附清单/许可，portable smoke 后执行自检。
- [ ] 固定 AppId 和用户数据目录；升级前请求应用正常退出并恢复微信；卸载默认保留用户数据，清数据入口单独显式执行。
- [ ] 干净 Windows VM 中自动安装候选包、启动自检、安装上一版数据夹具、升级、卸载、重装；每步检查退出码、文件/注册表和加密数据恢复。
- [ ] 运行 `python -m unittest tests.test_build_info tests.test_history_migrations -v`，再执行 `./package.ps1` 和 VM 中 `./tools/verify_install.ps1`；不把仓库隔离解包替代正式安装测试。

## T12：集中实机验收和交接发布

**Files:** 新建 `docs/windows/WINDOWS_LITE_ACCEPTANCE_MATRIX.md`、`windows/lite/tools/verify_realtime.ps1`；更新 README、AGENTS、AGENT_HANDOFF、版本记录。

- [ ] 运行全套逻辑/假服务/Qt/合成窗口测试，填写测试环境、依赖版本和输出。无执行权限的用例写“未运行”，不写“通过”。
- [ ] 以同一候选安装包验证以下真实微信场景，结果记录包 SHA：

| 场景 | 通过标准 |
| --- | --- |
| 已登录后启动助手 | 自动找到正确窗口并停靠，原位置可恢复 |
| 测试账号扫码 | 登录窗到主窗衔接；不退出用户现有账号 |
| 单击/滚轮 | 单击更换会话正常，滚动顺畅，旧建议立即停用 |
| 中文输入法 | 候选窗位置正确、组合输入不丢失、不触发发送 |
| 图片/文件/右键弹窗 | 原生可操作，不被助手遮挡或当作聊天分析 |
| 缩放/跨屏 | 100%/125%/150% 下聊天完整，右栏不压缩中央 |
| 最小化/还原/退出 | 行为符合设计，恢复后重新确认新帧；异常退出可恢复窗口 |
| 自动建议 | 测试会话新文字自动产生建议，版本/来源正确，无需手动分析 |
| 历史联动 | 引用导入资料中的早期证据，点击可定位，未绑定联系人不引用 |
| 两账号 | 交错聊天不串记录、建议或授权；不存在自动发送 |
| 授权与安装 | 安装版登录/重启/续期/退出正常，正式安装升级卸载验证通过 |

- [ ] 用户只需集中完成系统无法代办的本人扫码/授权和输入法主观确认；其余由开发者自动验证并记录，避免重复前期探针。
- [ ] 修复全部阻断项后重新构建，只有影响相关测试的代码改变才重跑对应项；最终校验包 SHA 与验收包一致。
- [ ] 准备源码差异、安装包、便携包、校验清单和发布说明供审查；获得本次明确提交/发布授权后才 commit/push/创建 Release。若创建 PR，必须附加到当前任务。

## 最终交付判定

T1–T12 对应门槛通过才标记完整交付。ExportWeChat 特定格式、真实扫码或某个微信版本没有样本/环境时，应在支持矩阵明确列为未覆盖；不能仅靠单元测试把它们判为通过。真正最小化时继续采集、遍历所有未打开会话、镜像 Codex 当前模型不属于本路线承诺，见设计第 11 节。
