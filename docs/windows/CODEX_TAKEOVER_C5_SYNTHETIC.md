# C5 临时接管证据：T9/T10 本地合成验收（fake 模型服务 + 双源仿真）

- 日期：2026-10-18（DeepSeek 临时接管 Codex 额度空窗期，14:51 交还 Codex 复核）
- 接管者：`inference`（DeepSeek）；原任务：Codex C5「T9/T10 替代验收」
- 仓库/分支：`G:\codex\ChatAssistant`，`feat/windows-lite`
- 基线：Lead 已把 Codex 0.5.8 未提交改动审查提交为 **`a3b2994`**（全量 713 OK / 37 skipped）；本任务在其之上只**新增**文件，不修改 Codex 已提交文件、不改业务源码、不 commit/push。
- 证据性质：**合成，不等于真实模型 / 不等于真实双微信账号**：
  - 模型侧：本机回环 OpenAI 兼容 fake server 返回固定 JSON/SSE；真实 `InferenceGateway`/`InferenceQueue` 的传输、schema 校验、超时/取消、单飞队列、错误分类、`subscription.analyze` Responses 协议被真实执行；**不**证明真实模型输出质量、真实 ChatGPT 套餐额度、真实网络/代理条件。
  - 来源侧：两个内存中的合成 `WindowTarget`/抓帧源驱动真实 `SourceRegistry`/`CaptureScheduler`/`BackgroundSourceMonitor`；**不**打开、移动、截图或读取任何真实微信窗口；**不**证明真实双账号隔离、真实 OCR 质量或真实停靠行为。
  - 全程未使用真实凭据、token、聊天内容或用户 profile；历史数据为合成文本，临时目录隔离。

## 1. 新增文件（仅本任务写作用域）

| 文件 | 用途 |
| --- | --- |
| `windows/lite/tests/test_c5_synthetic_inference.py` | fake OpenAI server + 真实 `InferenceGateway/InferenceQueue`：T9 历史参考、transient 重试、401 授权、嵌套额度暂停/恢复、取消关闭流后重试、真实 HTTP 在途=1、`subscription.analyze` Responses 协议端到端（7 项） |
| `windows/lite/tests/test_c5_synthetic_sources.py` | 两个合成 `WindowTarget`/抓帧源：SourceKey 身份、active 优先、后台默认关闭、最小化暂停、帧未变不重复 OCR、关闭一个不影响另一个、后台 monitor 包驱动调度（10 项） |
| `windows/lite/tools/c5_synthetic_evidence.py` | 可重复证据脚本：跑同一组合成场景并输出 JSON 证据 |
| `docs/windows/CODEX_TAKEOVER_C5_SYNTHETIC.md` | 本证据文档 |

证据产物（gitignore 的运行时目录）：
- `windows/lite/test-output/c5/c5_synthetic_evidence.json`
- `windows/lite/test-output/c5/c5_unittest.log`
- `windows/lite/test-output/c5/c5_evidence_run1.log`、`c5_evidence_run2.log`

未修改业务源码、Codex/其他队友既有测试；未 commit/push。

## 2. 可重复命令

```powershell
cd G:\codex\ChatAssistant\windows\lite
G:\codex\wechat-lite-venv\Scripts\python.exe -m unittest tests.test_c5_synthetic_inference tests.test_c5_synthetic_sources -v
G:\codex\wechat-lite-venv\Scripts\python.exe tools\c5_synthetic_evidence.py
```

## 3. 真实输出（基线 `a3b2994`）

### 3.1 单元级合成验收

```
Ran 17 tests in 5.388s

OK
```
（7 项推理链路 + 10 项双源调度；日志 `test-output/c5/c5_unittest.log`）

### 3.2 证据脚本（连续运行两次均 exit 0，输出可重复）

```
C5 synthetic evidence: ALL EXPECTATIONS PASSED
evidence_json: G:\codex\ChatAssistant\windows\lite\test-output\c5\c5_synthetic_evidence.json
```

关键字段（`c5_synthetic_evidence.json`）：

```json
{
  "inference_gateway_queue": {
    "history": {"items": 2, "evidence_readback": 2, "queue_state": "completed",
                "replies": 3, "history_marker_echo": "历史参考2条",
                "caution": "合成模型；收到历史参考2条",
                "prompt_contains_reference": true,
                "authorization_header": "Bearer synthetic-c5-key",
                "in_flight_after": 0},
    "transient_retry": {"first_error": "InferenceTransientError",
                        "retry_state": "completed",
                        "server_modes_last_two": ["transient", "success"]},
    "quota_pause": {"error": "InferenceQuotaError", "queue_paused": true,
                    "second_blocked_while_paused": true,
                    "resumed_state": "completed",
                    "pause_reason": "ChatGPT 套餐当前无法继续处理，请在 ChatGPT Usage 页面查看。"},
    "cancel_retry": {"error": "CancelledError", "cancelled_in_seconds": 1.12,
                     "server_saw_closed_connection_best_effort": true,
                     "retry_state": "completed"},
    "single_flight": {"server_max_concurrent": 1, "tasks": 4},
    "chatgpt_responses_protocol": {"path": "/v1/responses", "store": false,
                                   "stream": true, "has_temperature": false,
                                   "replies": 3}
  },
  "two_source_scheduler": {
    "active_priority": {"first_source": "A", "first_priority": "active",
                        "second_source": "B", "second_priority": "background"},
    "background_off": {"new_a_ocr": 1, "new_b_ocr": 0},
    "minimized_pause": {"new_a_ocr": 1, "new_b_ocr": 0, "after_restore_b_ocr": 1},
    "unchanged_frame": {"extra_ocr_for_5_repeats": 0, "new_frame_ocr": 1},
    "close_isolation": {"other_source_ocr": 1},
    "two_sources": {"source_a": {"hwnd": 101, "pid": 2001},
                    "source_b": {"hwnd": 202, "pid": 2002},
                    "distinct_source_keys": true,
                    "initial_managed": 2, "managed_after_closing_a": 1}
  }
}
```

## 4. 逐项对照 C5 验收要点

| C5 要求 | 合成验收证据 | 结论 |
| --- | --- | --- |
| 本地 OpenAI 兼容 fake server 驱动真实 InferenceGateway/InferenceQueue | 回环 fake `/v1/chat/completions` + `/v1/responses`；真实 gateway 发请求、schema 校验结果、真实 queue 调度；服务端记录请求模式与在途数 | 通过 |
| T9 历史参考 | 合成加密 HistoryStore → 真实 `MemoryRetriever`（2 条证据、2 条可 `read_evidence` 回读）→ `[历史参考]` 进入 prompt → fake 模型回显“历史参考2条” → queue 返回 3 条建议；Authorization 仅为合成 key | 通过（仅模型层与检索层） |
| Token/额度错误 | HTTP 401 → `InferenceAuthError`，仅取消同 profile 待办、其他 profile 继续；嵌套 `subscription_sharing_usage_limit_exceeded` → `InferenceQuotaError` → queue pause（pause_reason 正确）→ resume 后任务完成 | 通过 |
| 取消/重试路径 | 取消 SSE stalled 流 → `CancelledError`（1.12s，含 stalled 周期）；服务端 best-effort 观察到连接关闭；同 scope 重试成功（新请求）；503 → `InferenceTransientError` 后重试成功 | 通过 |
| 双源仿真驱动 SourceRegistry/CaptureScheduler | 两个合成 `WindowTarget`（hwnd 101/202、pid 2001/2002）+ 内存抓帧源；initial managed=2，关闭一个后 other 继续 | 通过 |
| active 优先 | 首个 job source=A priority=active；随后 source=B priority=background | 通过 |
| 后台关闭 | 后台来源新帧不 OCR（new_b_ocr=0）；显式启用后才 OCR | 通过 |
| 最小化暂停 | 最小化来源不 OCR（new_b_ocr=0），活动来源不受影响；恢复后继续（after_restore_b_ocr=1） | 通过 |
| 帧未变不重复 OCR | 5 次重复帧（frame_seq 不变）额外 OCR=0；新帧 OCR=1（活动/后台来源均验证） | 通过 |
| 真实 HTTP 在途=1 | 4 个任务并发提交，服务端记录 max_concurrent=1 | 通过 |

## 5. 仍需真实环境/用户的项目（合成不能替代）

1. 真实模型/套餐额度与用量（需用户已授权 ChatGPT 或受支持 API 模型；当前设置中兼容 API 模型名存在被服务商拒绝的风险，需用户在设置里确认受支持模型名）。
2. 两个真实微信账号窗口的多开、停靠、OCR 质量与真实隔离（需用户提供两个已登录官方微信窗口）。
3. 真实 UI 内点击/自动查找/发送链路（本任务只验证模型层与调度层，不触发 UI 动作，不发送消息）。
4. 真实网络代理、断网/断流、真实 429/额度响应（fake server 只按契约模拟）。
5. 真实托盘隐藏/恢复、最小化恢复等窗口行为属 C3 接管范围。
6. 真实后台抓帧子进程（`multiprocessing` worker + WGC）链路属 T4/T10 实机项；本任务用合成 packet/帧序列驱动同一 registry/scheduler 逻辑。

## 6. 风险与限制记录

- fake server 的 SSE 分块/取消行为是本机确定性模拟；真实 ChatGPT 服务的事件顺序/字段可能不同（T3 已有独立假服务测试，本任务补充 T9/T10 组合链路）。
- 合成 `WindowTarget` 不经过 Win32；`SourceKey` 规范化路径/进程启动时刻约束由 T1 契约测试覆盖，本任务只验证使用方式与多源状态机。
- 取消时“服务端观察到连接关闭”在 Windows TCP 下是 best-effort（连续运行两次均为 true，但未作为硬断言，避免 flaky）；硬证据是客户端 `CancelledError` + 不产生结果 + 重试使用新请求。
- 本任务未发现必须修改的业务 bug；如后续 Codex 复核发现真实链路不一致，以真实双账号/真实模型证据为准。
- 运行环境：Windows + `G:\codex\wechat-lite-venv\Scripts\python.exe`；无 Qt/显示器依赖（纯模型层与调度层测试）。

## 7. C5 小结

- **合成范围内 T9/T10 替代验证据可重复通过**：fake 模型服务 + 真实推理层/队列、双合成来源 + 真实调度器，覆盖额度/授权/取消/重试、active 优先、后台关闭、最小化暂停、帧未变不重复 OCR。
- **不能据此宣称**：真实模型可用或额度正常、真实双账号不串数据、真实 OCR/停靠/托盘行为通过。
- 交还 Codex：优先复核 §5 的真实环境项；本 MD、测试与 JSON 可作为替代证据基线。
