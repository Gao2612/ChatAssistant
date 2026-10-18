# C4 自动找对话 OCR 名字误识改进（郜亚禹类）— 交接证据

第一行记录本轮证据前置条件：`preflight: visible`；应用版本 = **0.5.8 source tree（基线 a3b2994 + C4 未提交改动）**；真实窗口 hwnd=394618（Weixin.exe），DPI=120（125%）。安装版 0.5.7 **不**作为本轮证据。

> 安全声明：全程未发送任何微信消息；未保存任何截图或聊天正文；未读取/落盘真实聊天内容；仅记录窗口状态、会话标题、矩形、布尔值、OCR 行是否含目标、失败 reason。未 git commit/push；未修改用户真实 profile。
> 已按 Lead 调度要求**丢弃**窗口为 minimized/hidden 期间采集的 probe 数据（`c4_probe_out.json`，10:13，minimized），不写入、不作为结论。以下全部证据均在 `preflight: visible` 且已 `activate` 后采集。

---

## 1. 结论

1. **`郜亚禹` 可在不切错人的前提下自动查找/切换**：真实窗口实测 `find_conversation` → `ok=true, reason=ok`，步骤含 `verify`，切换后头部 OCR **精确**读出“郜亚禹”。
2. **`田子夜` 无回归**：同轮实测 `ok=true`，头部 OCR 精确“田子夜”。
3. 真正的根因不是“头部 OCR 一定认错”，而是：**微信 4.x 的搜索结果下拉层是一个独立的分层/弹出表面，WGC（Windows Graphics Capture）主窗口帧里看不到它**。旧实现只看得到 WGC 帧，于是：
   - 永远看不到搜索结果行 → `result_not_found`（郜亚禹 83.9s 超长失败一次）；
   - 更危险的是：WGC 帧里仍能看到**旧会话列表行**，旧逻辑用“子串包含标题”选第一行，可能点到 `包含：田子夜` 这类**消息历史预览行**，从而打开错误会话（旧流程的 `title_mismatch` 校验恰好救回，属 fail-closed 但确有误切换风险）。
4. 修复后仍是**严格 fail-closed**：只点击“OCR 文本精确等于目标”的结果行；输入框行、消息预览行、网络结果行、重复同名行都不会被点击；头部只接受 exact 校验（三路裁剪），**不引入单字容错放行**。结果点击后**绝不按 Enter**（保留 Codex 0.5.8 的安全修改）。
5. 未发送、未落盘；窗口最终状态 `visible`、矩形未变。

## 2. 真实窗口证据（全部 visible + activate）

### 2.1 根因：WGC 帧 vs 屏幕抓取（同一矩形、同一时刻）

`c4_probe3_out.json`（10:21:51，preflight=visible）：搜索框输入目标后，对**同一窗口矩形**分别取：

| 采集方式 | 侧栏 OCR 是否出现目标结果行 | 观察 |
|---|---|---|
| WGC 主窗口帧（旧实现） | **否** | 仍是旧会话列表行（如 `田子夜` 列表行），`row_count=54`，无 `郜亚禹` 结果行 |
| 屏幕抓取同矩形（新实现） | **是** | 精确结果行 `郜亚禹` @ 矩形 [165,156,227,184]；另有 `包含：部亚禹` 消息预览行、`Q郜亚禹`/`Q部亚璞`“搜索网络结果”行、输入框内文字 `部亚禹` [130,60,183,81] |

该证据直接解释了旧实现的两种失败：结果行不可见（result_not_found）与误点“包含：…”预览行（title_mismatch 救回）。

### 2.2 点中精确结果行后：头部 OCR 精确（不再需要容错）

`c4_probe4_out.json`（10:22:49，visible）：用“精确 == 目标”的结果行 [165,156,227,184]，换算客户区坐标点击 (187,170) 后：

- WGC 头部 band 行：`["口", "郜亚禹", "?"]` → exact 命中 `郜亚禹`；
- 固定头部裁剪 1x：`["郜亚禹"]`，`read_title` = `郜亚禹`；
- 即：**正文态 + 前台 + 正确会话**下，头部 OCR 对 `郜亚禹` 是精确可读的；单字误识主要出现在错误的采集面/状态，而不是必须靠模糊匹配解决。

> 备注：该 probe 收尾时按 ESC，实测微信主窗回到 `hidden`（IsWindowVisible=false，矩形不变），随后已恢复 `visible`。因此最终验证流程成功路径不按 ESC；失败路径的 ESC 行为作为风险记录（见 §6）。

### 2.3 最终验证：新代码在真实窗口连续两个目标均成功

`c4_final_out.json`（10:25:46，preflight=visible，微信 1531×983 客户区）：

| 目标 | 结果 | reason | steps | 耗时 | 事后头部 OCR 行（band） |
|---|---|---|---|---|---|
| `郜亚禹` | **ok=true** | ok | activate/capture/find_search/paste_title/click_result/verify | 22.53s | `["口","郜亚禹","+"]` → exact `郜亚禹` |
| `田子夜` | **ok=true** | ok | 同上 | 13.12s | `["口","田子夜","+"]` → exact `田子夜` |

- 最终窗口状态：`visible`，hwnd=394618，rect `[180,130,1729,1122]`（未移动/未最小化/未隐藏），前台=微信（查找流程 required activate），当前会话=田子夜（验证前状态）。
- 全程无 Enter 键、无发送；未保存截图/正文。

### 2.4 失败/危险路径的 fail-closed 证据（单测覆盖，见 §4）

旧行为实测（可见窗口、修复前基线）：`郜亚禹` → `result_not_found`（83.9s，未点击结果、未切换）；`田子夜` 在残留搜索状态下 → `search_not_found`（4.4s）。新逻辑把“无法证明是目标”的所有情况都映射为结构化失败：`search_not_found` / `result_not_found` / `result_ambiguous` / `title_mismatch` / `clipboard_failed` / `backend_error`，均不点击结果或 ESC 退出。

## 3. 代码改动

### 3.1 `windows/lite/liteapp/wechat_find.py`（+159/-23 附近；Lead 正在基础上追加 visible/foreground 守卫）

| 位置 | 改动 |
|---|---|
| `capture_client_bgr()` | 改为：先 `PIL.ImageGrab` 抓**前台可见窗口**屏幕矩形（`GetWindowRect` + `GetClientRect` + `ClientToScreen` 映射裁剪到客户区，含搜索结果下拉层）；仅内存，不落盘。失败回退新函数 `_capture_wgc_client_bgr()`（原 WGC 逻辑，保留兼容）。 |
| 新增 `_capture_wgc_client_bgr()` | 原 WGC 采集实现（主窗口表面，不含下拉层），作为无 Pillow/抓屏失败时的回退。 |
| 新增 `_result_rows(rows, title, width, height)` | 只返回“文本精确==目标 且 y0≥10%窗高 且 x 在 0.33w 侧栏内”的结果行；`_title_matches` 做归一化（去空白/冒号/点/竖线）。 |
| `find_conversation()` 结果扫描 | 侧栏 crop 0.32w→0.33w；候选由“`title in row[0]` 子串”改为 `_result_rows(...)`；`len(candidates)>1` → ESC + `result_ambiguous`（同名重复不猜）。 |
| 新增 `_header_secondary_match()` | 第二裁剪 x∈[0.24w,0.62w]、y∈[0.02h,0.11h]，1x + 2x 上采样 + `_read_title_crop()` 三路 **exact** 校验；任一 exact 命中即通过。 |
| 新增 `_read_title_crop()` | 延迟 import `vendor.ocr.read_title`，测试可 patch；避免单测加载真实 OCR 引擎。 |
| 模块 docstring | 记录硬安全规则：仅 exact 结果行、double-row fail-closed、无 Enter、屏幕抓取仅内存。 |
| 保留 | 结果点击后**不按 Enter** 的注释与行为（Codex 0.5.8 删除项，禁止回退）。 |

### 3.2 `windows/lite/tests/test_wechat_find.py`（12 项，全部通过）

新增安全回归：
- `test_no_enter_is_pressed_after_the_result_click`：断言全程 `key_combo` 无 `VK_RETURN`(0x0D)、steps 无 `press_enter`；
- `test_message_preview_row_is_not_the_click_target`：`包含：田子夜` 预览行在精确行之前时，仍只点精确行（点击 y=210 而非 125）；
- `test_preview_rows_only_fail_closed_without_result_click`：只有预览行 → `result_not_found`，仅搜索框点击，ESC；
- `test_search_input_row_is_never_clicked`：输入框内同名文字（y=60）不被当作结果行；
- `test_duplicate_exact_rows_fail_closed`：两行同名 → `result_ambiguous`，不点结果、ESC；
- `test_single_character_difference_is_never_accepted`：头部 `部亚禹`（单字差）→ `title_mismatch`，绝不因“受控容错”放行；
- `test_secondary_header_crop_accepts_exact_name` / `test_read_title_exact_match_is_accepted`：第二裁剪/read_title 的 exact 命中可通过。

保留原有 4 项（成功、搜索框缺失、标题不匹配 ESC、空标题）。

## 4. 命令与测试结果

```
cd G:\codex\ChatAssistant\windows\lite
G:\codex\wechat-lite-venv\Scripts\python.exe -m unittest tests.test_wechat_find -q
Ran 12 tests in 12.322s
OK
```

（`-v` 全量 12 项明细日志：`C:\Users\16356\AppData\Local\Temp\c4_unit_v.log`；本报告不粘贴真实聊天内容。）
未跑全量 discover（按接管约定由 Lead 统一跑）。

真实窗口验证脚本（只读/只查找、无发送、不落盘截图）：
- `c4_probe2.py` / `c4_probe3.py` / `c4_probe4.py` / `c4_final.py`（位于 `%TEMP%`，非仓库文件；本 MD 摘录输出）。
- 复制方式：`G:\codex\wechat-lite-venv\Scripts\python.exe %TEMP%\c4_final.py`。

## 5. 证据路径汇总

| 证据 | 路径 | 前置 |
|---|---|---|
| WGC vs 抓屏根因 | `C:\Users\16356\AppData\Local\Temp\c4_probe3_out.json` | visible |
| 结果行点击后头部 exact | `C:\Users\16356\AppData\Local\Temp\c4_probe4_out.json` | visible |
| 最终验证（两目标 ok） | `C:\Users\16356\AppData\Local\Temp\c4_final_out.json` | visible |
| 修复前失败基线（可见窗口） | `C:\Users\16356\AppData\Local\Temp\c4_probe2_out.json` | visible |
| 单测 -v 日志 | `C:\Users\16356\AppData\Local\Temp\c4_unit_v.log` | — |
| 已丢弃（minimized 期） | `c4_probe_out.json` | 不使用 |

## 6. 风险与未完成

1. **屏幕抓取依赖“前台 + 无遮挡”**：必须 `activate` 后抓当前屏幕矩形；若微信被其它窗口遮挡（含本 App 置顶），OCR 会读到遮挡内容 → 只会 fail-closed（结果行/头部不匹配），不会切错人；但会降低成功率。Lead 正在加的“抓屏/点击前 `window_status==visible` 且 `GetForegroundWindow==hwnd`，否则 `not_foreground`”守卫正好覆盖该风险。
2. **ESC 语义**：实测个别状态下（会话已打开、焦点在消息区）按 ESC 会让微信主窗变为 `hidden`（矩形不变），已恢复 visible。失败路径的 `_escape` 属既有安全动作；若 ESC 后窗口 hidden，后续操作应直接 fail-closed，不应再抓屏。
3. **单字误识并未被“放行”**：本修复不依赖模糊匹配；若某些字体/DPI/主题下头部仍单字误读，结果保持 `title_mismatch` fail-closed（符合“宁可不切，不可切错”）。是否引入“受控容错”需另行证明唯一性（本轮不采纳；`test_single_character_difference_is_never_accepted` 固化）。
4. **安装版未重打包**：本轮验证是 0.5.8 source tree（a3b2994 + 当前工作区改动），未重建/重装候选包；Codex 复核/打包由 Lead 统一处理。
5. **与 C1 的交叉**：`tools/c1_find_ab_diag.py` monkeypatch `wf.capture_client_bgr` 仍可用（函数名保留）；App UI 路径（ui.py，C1 独占）未在本轮真实点击验证中联动，建议 Lead 在合并后做一次 App 内“自动查找”点击验收。
6. **时长**：`郜亚禹` 首次成功耗时 22.5s（含多次 OCR/抓屏与 header 重试），`田子夜` 13.1s；必要时可后续优化（缓存/减少 header 重试），不影响安全性。
7. **时间窗**：真实窗口时窗为 10:20–10:35，本轮验证在 10:25 完成并已释放窗口；之后未再操作真实微信。

## 7. 复核清单（给 Codex / Lead）

- [ ] `wechat_find.py`：确认无任何 Enter（0x0D）路径；`result_ambiguous` 时不点结果；exact 行条件未被放宽。
- [ ] `wechat_find.py`：Lead 的 visible/foreground 守卫与 `capture_client_bgr`/`click` 的调用顺序一致。
- [ ] 单测：`tests.test_wechat_find` 12 项（含 7 项 C4 安全回归）。
- [ ] 真实窗口复跑（需要 visible 窗口且前台）：`郜亚禹`/`田子夜` 各一次 ok + 头部 exact；失败变体（`鄢子夜` 类）保持 `title_mismatch`。
- [ ] 不发送、不落盘、不 commit/push。
