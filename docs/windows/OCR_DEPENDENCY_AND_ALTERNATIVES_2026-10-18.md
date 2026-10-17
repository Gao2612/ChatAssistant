# OCR 依赖清单与非 OCR 方案分析（2026-10-18）

> 目的：把当前项目中**所有依赖 OCR（或多模态视觉识别）才能完成的任务**集中列出，供 Codex 专门分析是否存在不依赖 OCR 的替代实现。
> 约束：不 hook 微信、不读微信数据库、不自动发送未经用户确认的消息、不保存真实聊天正文/截图；实验阶段只读、只用合成数据或用户明确授权的窗口。
> 相关任务：T2/T4/T6/T8/T9/T12、S-AUTO（自动选对话/自动发送）、S-WECHAT（隐藏窗口发现）、S-STYLE（HTML/PDF 风格）、S-TRAY（托盘读取）。

---

## 1. 结论摘要（当前事实）

1. **聊天正文、发言人、气泡方向、会话标题**目前都来自图像 OCR：`worker.py` 用 WGC/PrintWindow 截帧 → `vendor/area.py` 定位聊天区 → `vendor/ocr.py` 的 RapidOCR/Windows OCR 识别标题与消息。
2. **自动找对话/自动发送**的“搜索框、结果行、标题校验、输入框、发送按钮、发送后验证”全部用 OCR 或固定比例坐标 + OCR 校验；坐标比例来自 4.x 估计值，已出现单字误识（郜→部）、OCR 空帧、输入焦点不稳定、发送误判等问题。
3. **托盘隐藏窗口**无法直接 WGC/PrintWindow 抓取（黑屏/异常），当前方案是“离屏非激活显示 → PrintWindow → 恢复隐藏”，之后仍走 OCR 解析。
4. **扫描 PDF/图片页导入**只在页面无文字层时显式启用 OCR；质量标记 `quality.source=ocr`，失败页可单独重试。
5. **窗口发现本身不依赖 OCR**：`discovery.py` 使用 Win32 窗口标题/类名/进程身份（`GetWindowTextW`/`GetClassNameW`/进程路径），托盘/隐藏/最小化状态也是 Win32/DWM 属性，不是 OCR。
6. **远程读取后端**（`reading_backend=api` 多模态模型）是**非 OCR 的替代路径之一**：把当前聊天画面发给视觉模型，结果经 schema 校验后覆盖本地 OCR；失败回退 OCR。代价是云调用、额度与隐私（只发当前画面）。

---

## 2. OCR 依赖点总表

| ID | 任务/功能 | 代码位置（函数） | OCR 角色 | 输入 | 已知失败模式/证据 |
|---|---|---|---|---|---|
| O1 | 实时聊天标题识别 | `liteapp/worker.py: run() → read_title(header)`；`vendor/ocr.py: read_title` | 必需 | 当前帧 header 区域 | 单字误识（“郜亚禹”→“部亚禹”）；空帧/标题空 → 不发 snapshot；隐藏窗口无 DWM 表面 |
| O2 | 实时消息列表/发言人/方向 | `worker.py → Reader.read(chat, bg)`；`vendor/ocr.py: Reader.read/who_said/similar`；`vendor/area.py: chat_area` | 必需（文本）；方向部分靠像素颜色 | 聊天区 RGB | 同一段像素 OCR 会抖（“傻逼了”↔“傻逼”）；小字/表情/图片字需过滤；名字行漏识别会挂到上一个人 |
| O3 | 聊天区检测 | `vendor/area.py: chat_area`（像素/背景/边距启发式，非 OCR） | 无 OCR，但依赖视觉 | 整帧 | 布局/主题/DPI 变化会误判；标题区与消息区边界错位 |
| O4 | 自动找对话：搜索框/结果行/标题校验 | `liteapp/wechat_actions.py: make_ocr_verifier/_read_title_via_ocr/select_conversation`；`test-output/find_contact*.py` | 必需 | 窗口帧 + 固定/比例 rect | 标题容错（2/3 字符）仍需人工兜底；搜索结果可能不是首条；OCR 空帧导致 `title_unverified` |
| O5 | 自动发送：输入框/发送按钮/发送后验证 | `wechat_actions.py: send_message`（可选 `verify_sent`）；验收脚本 `test-output/send_final.py` 等 | 必需（定位与验证） | 窗口帧 | 输入焦点不稳定 → 文字未进输入框；仅 OCR“下方区域含文字”会误判输入框为已发送；必须“输入框清空+气泡出现”双条件 |
| O6 | 托盘/最小化隐藏读取 | `liteapp/tray_capture.py` + `worker.py` 隐藏分支 | 必需（解析） | PrintWindow 图像 | 直接 PrintWindow 隐藏窗口黑屏；离屏非激活显示后可用；仍靠 OCR 解析标题/消息 |
| O7 | 远程读取变更检测/回退 | `worker.py`（remote_reading 时 snapshot 是触发条件）、`liteapp/ui.py: last_ocr_snapshot` | 间接必需 | OCR snapshot 结果 | OCR 抖动导致重复触发/漏触发；远程文本不能被 OCR 覆盖（已有保护） |
| O8 | 扫描 PDF/图片页导入 | `liteapp/importers/pdf.py: _ocr_text/process_page`；`base.py` | 可选（页面无文字层时显式启用） | 150 DPI 渲染页 | 超时/空白/密码页 → 页级问题；`quality.source=ocr` |
| O9 | 历史分析/档案/统计/风格 | T5/T7/T8/T9：`history.py/history_jobs.py/style_profile.py` | 间接（导入文本若来自 O8 则是 OCR 质量） | V2 store 记录 | 片段/消息计数需区分；OCR 页文本质量影响事实/推断 |
| O10 | 测试与验收入口 | `tests/test_discovery.py`（fake `vendor.ocr`）、`tests/test_importers.py`（fake OCR 文本）、`test-output/*.py`（真实窗口验收脚本） | 测试替身 | 合成图像/文本 | 真实验收脚本包含 OCR 定位/校验逻辑；不进仓库 |
| O11 | 窗口发现/停靠/恢复 | `liteapp/discovery.py`、`window_dock.py`、`window_manager.py` | 无 OCR | Win32/DWM | 标题只用于分类（“微信”等），不读聊天内容；隐藏/托盘状态已覆盖 |
| O12 | 模型读取后端（多模态） | `liteapp/reading.py`、`backends.py: reading_backend=api/codex` | 非 OCR（视觉模型） | 当前聊天画面 | 需要额度/网络；只有当前画面；失败回退 OCR |

---

## 3. 替代方案目录（供 Codex 评估）

### A. Windows UI Automation（UIA，首选候选）
- 目标：用 `IUIAutomation`/`uiautomation`/`pywinauto` 读取微信 4.x 的控件树，替代：
  - 会话标题（标题栏/会话名元素 Name）
  - 搜索框（ControlType=Edit，Name/AutomationId）
  - 搜索结果列表（List/ListItem，Name=联系人名）
  - 输入框（Edit/ValuePattern）
  - 发送按钮（Button/Name=“发送”）
  - 消息列表（List/ListItem/TextPattern：正文、发送人、时间）
- 可行性：微信 4.x 为 Qt 应用，Qt 通过 UIA 桥暴露可访问树（需客户端请求无障碍时激活）；需实测是否完整、是否包含聊天消息文本。
- 优点：不 OCR、可读隐藏窗口、可精确定位控件、支持键盘/值模式。
- 风险：控件树可能被应用限制/不稳定；跨版本变化；UIA 读消息可能被判定为辅助功能（通常允许，不注入不 hook）。
- 实验：`Inspect.exe`/Accessibility Insights 或脚本遍历树，只输出 ControlType/Name 长度/AutomationId，不保存消息正文；对比 OCR 结果。

### B. MSAA / IAccessible（旧无障碍接口）
- 用 `oleacc` 枚举 `IAccessible` 树；Qt 在 Windows 上通常走 UIA，MSAA 可能不完整；作为 UIA 失败后的备选。

### C. Win32 子窗口/消息
- `EnumChildWindows` 列出微信主窗口的子 HWND/类名/控件 ID；Qt 控件一般不是独立 HWND，但可能暴露部分原生控件（输入框/按钮）。
- 若存在 Edit 控件，可用 `WM_GETTEXT`/`EM_GETLINE`；否则不可用。
- 不注入、不 Hook；只读窗口消息。

### D. 键盘/快捷键流程（减少坐标与 OCR）
- 搜索会话：全局/窗口快捷键（如 Ctrl+F 或点击搜索框一次）→ 输入名字 → 下方向键选择 → Enter；用 UIA 或输入后读取标题校验。
- 发送：输入框聚焦 → 粘贴 → 发送快捷键/按钮（UIA 定位）。
- 可完全避免“搜索框/结果行/按钮”的 OCR 定位；仍需一个非 OCR 的标题/发送确认（UIA 或视觉模型）。

### E. 剪贴板读取（需用户授权的一次性实验）
- 微信消息区通常支持“选中消息→复制”（Ctrl+C/右键菜单），可把文本放到剪贴板而非 OCR。
- 风险：复制动作改变用户选择/剪贴板；需及时清空；可能包含整段聊天（隐私），不能落盘；不适合全自动高频。
- 实验：在合成/测试窗口验证复制范围与格式；“全选/复制”菜单是否可通过键盘触发。

### F. 多模态视觉模型（已有实现，非 OCR）
- `reading_backend=api`：把当前聊天裁剪图交多模态模型，返回结构化消息；可扩展用于标题校验/发送验证。
- 优点：不依赖本地 OCR；对字体/模糊更鲁棒。
- 代价：额度、网络延迟、只发当前画面（隐私边界需明确）；结果仍需 schema/标题校验防幻觉。

### G. Windows 自带 OCR（Windows.Media.Ocr）
- `vendor/ocr_windows.py` 已支持（主要给韩日等 RapidOCR 不支持的界面）；中文准确率可能优于 RapidOCR，但仍是 OCR，不是“非 OCR”方案。

### H. 视觉模板/颜色/几何（非 OCR 定位）
- 用 OpenCV 模板匹配或颜色规则定位：搜索框、结果行、输入框、发送按钮、气泡方向（绿色/右侧=我）。
- 已有基础：`vendor/ocr.py: who_said` 用颜色/墨高判断方向；`area.py` 用像素找聊天区。
- 优点：不 OCR、快；缺点：版本/DPI/主题依赖，需每版本校准；可先用于 UI 定位，文字仍靠 UIA/模型。

### I. 官方数据通道（合规性待评估）
- 微信官方“聊天记录备份/导出、迁移”不是实时接口；解析本地数据库/备份文件属于读取微信数据，**当前项目明确禁止**，不建议作为方案。
- 可向用户提供“导出聊天记录后导入”的离线路径（已有 T6 导入器），实时场景仍不适用。

---

## 4. 建议 Codex 执行的实验（只读、逐步、可回退）

**E1 UIA 控件树探测（最高优先级）**
- 目标窗口：用户授权的真实微信主窗口（只读，不点击/不发送）。
- 输出：控件树摘要（ControlType、Name 长度、AutomationId、是否支持 ValuePattern/TextPattern），**不输出真实聊天正文**。
- 成功标准：能唯一识别会话标题、搜索框、结果列表、输入框、发送按钮、消息列表。
- 工具：`uiautomation`/`pywinauto`/`comtypes`，或 Inspect.exe 手工确认。

**E2 UIA 读取 vs OCR 对比**
- 同一帧：UIA 读标题/输入框/消息列表（只比较长度/数量/哈希，不落盘正文）vs 现有 OCR 结果。
- 指标：标题准确率（含郜/田等易混字）、消息条数、方向、延迟、对隐藏/最小化窗口可用性。

**E3 隐藏窗口 UIA 可用性**
- 关闭到托盘（用户授权）后，直接查询 UIA 树，看标题/消息是否可读；成功则无需“离屏显示+PrintWindow+OCR”。

**E4 键盘-only 找对话**
- 不 OCR 定位搜索框：用快捷键/一次点击进入搜索→输入“田子夜”→Down/Enter→UIA 读标题确认；测量成功率与版本差异。

**E5 模板/颜色定位基准**
- 对搜索框/发送按钮/输入框/气泡方向做模板匹配与颜色规则，在 100/125/150% DPI、窗口大小变化下测准；可用合成窗口/用户授权窗口（不保存截图）。

**E6 剪贴板提取实验（可选，需用户在场）**
- 验证消息区 Ctrl+A/C 或多选复制是否可行、复制内容范围、是否可清空；仅用于评估，不进入自动链路。

**E7 视觉模型校验基准**
- 用视觉模型读取标题/验证发送（只发当前裁剪图），与 OCR/UIA 对比准确率/延迟/成本；评估隐私边界是否可接受。

**E8 系统 OCR 对比（仍是 OCR，作为质量兜底）**
- 对比 RapidOCR vs Windows.Media.Ocr 在标题、控制文字、模糊帧上的准确率。

**E9 `chat_area` 启发式 vs UIA 边界**
- 若 UIA 能给出消息列表/输入框边界，替换像素启发式 `chat_area`，降低布局漂移风险。

---

## 5. 验收标准（若替代方案成立）

1. 标题识别：对“郜亚禹/田子夜/天罚团”等易混名字，非 OCR 方案在真实窗口连续 20 次准确率 ≥ 99%，失败时 fail-closed（不发送/不分析）。
2. 自动找对话：20 次不同联系人切换，0 次点错会话；不依赖 OCR 坐标。
3. 自动发送验证：必须同时满足“输入框清空 + 会话出现对应消息（UIA/视觉模型/UIA TextPattern）”；不得以 OCR 文本片段误判。
4. 隐藏/托盘窗口：不恢复用户窗口即可读取标题与消息（UIA 优先）；若必须离屏显示，需保持无焦点/无任务栏闪烁。
5. 不 hook、不读微信数据库、不注入进程；不保存真实聊天正文/截图。
6. 性能：标题校验 < 300ms；找对话 < 2s；不影响 UI 主线程。

---

## 6. 文件与函数索引（供 Codex 定位）

- 采集/OCR：`windows/lite/liteapp/worker.py`（`run`、`read_title`、`Reader.read`）、`windows/lite/vendor/ocr.py`（`_engine`、`read_title`、`Reader`、`who_said`、`similar`）、`windows/lite/vendor/area.py`（`chat_area`）、`windows/lite/vendor/chatapps.py`（app profile）。
- 自动操作：`windows/lite/liteapp/wechat_actions.py`（`Win32Backend`、`select_conversation`、`send_message`、`make_ocr_verifier`、`_read_title_via_ocr`、`plan_for_window`、`estimate_layout_rects`）。
- 托盘读取：`windows/lite/liteapp/tray_capture.py`、`worker.py` 隐藏分支。
- 窗口发现/恢复：`windows/lite/liteapp/discovery.py`（`discover`、`find_wechat_windows`、`wechat_presence`、`restore_window`）。
- 远程读取：`windows/lite/liteapp/reading.py`、`backends.py`（`reading_backend`）。
- PDF/导入：`windows/lite/liteapp/importers/pdf.py`、`base.py`、`import_jobs.py`。
- 历史/风格：`windows/lite/liteapp/style_profile.py`、`history_jobs.py`、`history_statistics.py`。
- 测试/验收脚本：`windows/lite/tests/test_discovery.py`、`tests/test_importers.py`；`windows/lite/test-output/find_contact*.py`、`send_final.py`、`probe_*.py`（gitignored）。
- 真实验收证据：`_reports/S_AUTO_SEND_REAL_report.md`、`_reports/S_WECHAT_HIDDEN_report.md`、`_reports/TEARDOWN_CRASH_report.md`。

---

## 7. 待 Codex 回答的问题

1. 微信 4.x 的 UIA 树是否完整暴露聊天消息正文/标题/输入框/发送按钮？哪些控件类型/模式可用？
2. 是否存在可用的、非注入式的消息读取方式（UIA TextPattern、IAccessible、剪贴板、系统 API）？各自准确率/稳定性/合规性如何？
3. 隐藏/最小化窗口下 UIA 是否仍可读？若可以，能否完全替代“离屏显示+PrintWindow+OCR”的托盘方案？
4. 自动找对话能否完全键盘化（避免 OCR 定位搜索框/结果行）？标题确认用什么非 OCR 信号？
5. 自动发送验证能否用 UIA 输入框 ValuePattern + 消息列表变化来双条件判定？
6. 如果 UIA 不可行，视觉模型/模板匹配/系统 OCR 三者如何组合能把 OCR 依赖降到最低？成本与隐私边界如何？
7. 建议的最小改造路径与实验顺序，以及每步的成功/失败判据。

---

## 8. 边界与安全声明

- 本清单只描述现状与候选方案，不代表已实现；UIA/剪贴板/视觉模型实验均需用户在场、只读优先、不自动发送、不落盘真实聊天内容。
- 真实微信验收已获用户授权；已发送过一条明确标注的测试消息（可撤回），除此之外不得自动发送。
- 任何“读取微信数据库/内存注入/Hook”方案均不采用。

---

## 9. 实测补充：UIA 不可用与托盘隐藏动作可行性（2026-10-18）

### 9.1 WeChat 4.x UIA 树探测结果（实测）

- `uiautomation` 对真实微信主窗口 `ControlFromHandle(hwnd)` 返回树仅 3 个节点：
  `WindowControl("微信")` + `PaneControl("Weixin")` + 1 个空 Pane；**没有** Edit/List/Button/Text 子控件。
- 结论：当前 WeChat 4.x 版本（Qt 自绘）没有暴露可用的 UIA 控件树，无法用 UIA 读取标题/消息/输入框/发送按钮，也无法用 ValuePattern/InvokePattern 静默操作隐藏窗口。
- 后续可尝试：不同 WeChat 版本/开启系统辅助功能后重测；MSAA/IAccessible 同样需实测。当前按“UIA 不可用”处理。

### 9.2 托盘隐藏状态下的自动找对话（已实测通过）

流程与证据（真实微信，仅查找、不发送、操作后恢复原状态）：
1. 模拟关闭到托盘：窗口 `status=hidden`；
2. `discovery.restore_window(..., activate=True)` → 可见、前台校验通过；
3. OCR 定位搜索框→输入目标→Enter→OCR 定位结果行→点击+Enter（最多 3 次重试）；
4. 顶部 OCR 命中目标标题（`found=true`）；
5. 立即 `ShowWindow(SW_HIDE)` 回到托盘，最终 `status=hidden`；若原本是可见状态，再恢复为用户原状态。

结论：**托盘隐藏状态下“自动找对话”可实现**，代价是操作期间窗口会短暂显示并需要前台（SendInput 限制）。

### 9.3 托盘隐藏状态下的自动发送（未实测发送，方案同构）

- 同 9.2 的“临时恢复→操作→恢复托盘”包装；发送仍需：标题校验（fail-closed）→输入框焦点探针→粘贴→点击发送→双条件验证（输入框清空+气泡出现）。
- 未实测原因：需要再向真实联系人发送一条测试消息，需用户明确同意；dry-run（只填不发）可先行验证。
- 建议产品化：仅用户显式点击“在托盘中执行”时启用；操作期间显示状态；结束恢复原隐藏/可见状态；绝不在未验证标题时发送。