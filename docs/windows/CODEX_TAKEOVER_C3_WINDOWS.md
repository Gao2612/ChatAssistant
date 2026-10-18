preflight: visible
version: 0.5.8 (source tree; acceptance baseline a3b2994, exported tree 163c759)
wechat_pid: 10968  wechat_hwnd: 394618
isolated_profile: windows/lite/test-output/c3-runs/final-a3b2994/localappdata/ChatAssistantLite

# C3 真实窗口验收证据（停靠 / 最小化 / 真实弹窗 / 托盘恢复）

> 2026-10-18 DeepSeek 接管窗口期由 takeover-wintest 生成；自动化证据只含**窗口矩形、布尔值、
> 计数、助手状态文本/标签**，不含聊天内容与截图；**未发送任何微信消息**。
> 原始 JSON/日志在 `windows/lite/test-output/c3-runs/`（gitignored，含本机窗口坐标，不随仓库发布）。

## 目标与版本

| 项 | 值 |
| --- | --- |
| 应用版本 | **0.5.8**（source tree 运行，非已安装 0.5.7） |
| 验收代码基线 | **a3b2994**（Codex 0.5.8 review fixes）；`liteapp/` 与导出提交逐字节一致 |
| 运行源码快照 | 以 `git archive` 导出提交 **163c759**（差异仅 docs/tests/tools），不含工作区未提交改动 |
| 运行方式 | 项目 venv 运行 `main.py`；`LOCALAPPDATA` 指向一次性隔离目录，真实用户 profile 未读写、未杀用户 0.5.7 实例 |
| 微信 | 官方微信单主窗口；每次 run 前 `window_status == "visible"`；run 后恢复 visible + 原矩形 |
| 隐私 | 状态文本只白名单匹配助手协议消息并排除 editor 子树；聊天文字只记长度；无截图；无发送 |

## 方法（两路互补，均在真实微信窗口上）

1. **外部 UIA/Win32 黑盒探针** `windows/lite/tools/c3_real_window_verify.py`（交付脚本）：
   启动 0.5.8 快照、外部点击设置页勾选/保存、Win32 读矩形/可见性/Z 序/命中测试、临时
   最小化/隐藏并在 finally 恢复。用于黑盒验收（设置路径、镜像、最小化降级、Z 序）。
2. **进程内注入探针** `windows/lite/test-output/c3-dev/inproc_accept.py`（诊断/补证）：
   在同一 0.5.8 快照代码上直接驱动 `MainWindow`/`WindowManager`，读取 attach/detach 的
   状态机 reason（外部黑盒看不到），用于定位 dock 失败原因并完成停靠/托盘场景。

> 未使用任何 fake/stub 窗口；所有矩形/状态均来自真实微信进程（PID 10968 / HWND 394618）。

## 自动验收结果

| # | 场景 | 检查 | 结果 |
| --- | --- | --- | --- |
| 0 | preflight/postflight | 每次 run 前 visible；结束后 visible + 原矩形；进程身份不变 | **通过**（两个有效 run 的 postflight 均 visible+rect 恢复） |
| 1a | 停靠-勾选保存 | 设置页勾选“停靠微信窗口”并保存：配置 `dock_wechat=true` 持久化 | **通过**（黑盒 UIA 与进程内两种路径均验证） |
| 1b | 停靠-attach | `WindowManager` attach → `state=attached, overlay_safe, zorder_ok`；微信矩形 == 中央 aperture；状态“微信已停靠 · 中央微信原生可交互”；未抢前台 | **通过（需足够 aperture）**；正常 1200×820 窗口在 125% DPI 下 aperture 仅 980×420 < 微信原生最小高度 564px，attach 被 **fail-closed 拒绝**（微信不动、只读），见“发现 F1” |
| 2 | 停靠-取消 | 取消勾选保存 → detach；微信矩形恢复到 attach 前矩形（±2px）、visible、非最小化 | **通过**（[93,66,886,630] → 移入 aperture → 精确恢复） |
| 3 | 微信最小化 | 助手不自动还原窗口；状态降级（“微信已隐藏到托盘，暂时无法读取画面；打开微信后可继续。”）；只读编辑区清空；恢复后识别继续 | **通过**（黑盒与进程内 run 均复现，editor_len 20→0→20） |
| 4 | 真实弹窗覆盖 | 同进程顶层弹窗被 `popup_covering` 检测、状态只读、不误读/不误发 | **无法自动判定 → 人工项**：本机微信 4.x 右键菜单在主窗口内绘制，未产生“同进程顶层窗口”（popup_count 0），自动场景不成立，不伪造通过 |
| 5 | 托盘隐藏→恢复 | 隐藏后助手不崩溃、不重复启动微信、读取继续或安全降级；产品“启动微信”按钮恢复 visible+原矩形 | **通过**（读取继续：editor_len 保持 20；微信进程数 5→5 未新增；恢复后 visible+原矩形）；观察到“启动微信”点击与后台托盘截图周期偶发竞争（一次点击被随后 HIDEWINDOW 覆盖，需再点一次），见“发现 F3” |
| 6 | 收尾 | 微信 visible、矩形与原矩形一致、PID/进程创建时间未变 | **通过**（postflight: status=visible, rect==baseline） |

### 结论

- 停靠（勾选/取消/几何恢复/Z 序/不抢前台）、最小化降级与恢复、托盘隐藏读取与恢复：**自动化通过**
  （停靠需要满足 aperture ≥ 微信原生最小高度的环境前提，见 F1）。
- 真实弹窗覆盖：**必须人工**（本机微信菜单非独立顶层窗口）；人工步骤见下。
- 无聊天内容/截图落盘；全程未发送微信消息；“发送到微信”按钮从未被脚本点击。

## 关键发现（交 Lead/Codex 复核，不自行改业务代码）

- **F1（产品健壮性/UX，低风险）**：`WindowManager.attach` 的 `MIN_APERTURE_*`（300）小于真实微信
  原生最小窗口高度；窗口不足时 attach 以 `geometry_mismatch` fail-closed（微信不动、只读，安全），
  但界面提示为“微信窗口状态异常：中央为只读画面，请重新连接。”，未说明“窗口太小/请放大助手窗口”。
  建议：把真实微信最小尺寸纳入 precheck 并在该状态给出可操作提示（如“放大助手窗口后重试”）。
- **F2（环境干扰，非产品）**：本机微信物理矩形在多次 run 间呈 0.8/1.0/1.25 倍变化
  （[92,66,885,574]↔[115,83,1106,718]↔[144,104,1383,898] 等），伴随 DPI=120/96 变化，来自外部
  （非本脚本）；因此跨 run 绝对几何不可比，本证据只用每个 run 自己的 preflight 矩形做恢复目标。
- **F3（边缘竞争，低概率）**：托盘隐藏时 worker 用“离屏短暂显示 + PrintWindow”抓帧（设计如此），
  会周期性把微信矩形移到 x=-30000 并短暂 `IsWindowVisible=true`；用户此时点“启动微信”可能被同一
  周期的 `SWP_HIDEWINDOW` 再隐藏一次（观察到 1 次，重试后成功）。建议 product 在 restore 与托盘
  抓帧之间加互斥，或恢复后校验并重试一次。
- **F4（验收环境）**：外部 UIA 探针看不到 `WindowManager` 的 attach reason，dock 归因需进程内探针；
  已分别保留黑盒证据（设置/镜像/最小化）与进程内证据（dock/tray 状态机）。

## 必须人工确认的最小步骤（人眼才能判断）

1. **停靠视觉**（1 分钟，窗口需足够高）：连接微信 → 勾选“停靠微信窗口”保存 → 目视中央区域是原生
   微信且可直接点击/滚动、无裁剪/无其他窗口透出；取消勾选后微信回到原位置。
   注意：小屏/高缩放时如提示“窗口状态异常”，先把助手窗口拉高（aperture 需 ≥ 微信最小高度）再试。
2. **真实弹窗**（1 分钟）：在微信里右键菜单或双击图片打开真实预览 → 目视中央变只读画面、
   不出现新建议/发送入口；关闭后识别恢复。（自动化无法检测本机菜单窗口）
3. **托盘真实关闭**（1 分钟）：点微信关闭按钮（真实关闭到托盘）→ 目视任务栏无微信、托盘图标在；
   点助手“启动微信”→ 微信在原位置出现且识别继续。
4. **最小化**（30 秒）：点微信最小化 → 助手不自动弹出微信；手动恢复后识别继续。
5. **发送安全**（需额度，A4 人工项）：勾选授权后点“发送到微信”应出现二次确认；本自动化从未点击发送。
6. **DPI/多屏**（可选）：100%/125%/150% 或多显示器下重复停靠，确认 aperture 尺寸前提与点击命中。

## 运行记录与证据

| 项 | 值 |
| --- | --- |
| 有效 run A（外部 UIA 黑盒） | 2026-10-18 10:50:39–10:52:14；app PID 28596；隔离 profile `test-output/c3-runs/final-a3b2994/localappdata/ChatAssistantLite`；preflight visible [92,66,885,574]；postflight visible（外部 DPI 变化导致绝对矩形漂移） |
| 有效 run B（进程内驱动，补证 dock/tray 状态机） | 2026-10-18 10:56:44–10:57:11；preflight visible [93,66,886,630]；postflight visible+原矩形；aperture 980×660 |
| 黑盒证据 JSON | `windows/lite/test-output/c3-runs/final-a3b2994/c3-evidence-20261018-105214.json` + `run3.log` |
| 进程内证据 JSON | `windows/lite/test-output/c3-runs/inproc-a3b2994/c3-inproc-evidence.json` |
| 单测（脱敏/几何） | `windows/lite/tests/test_c3_evidence_helpers.py`，8 项通过 |
| 作废 run（不作结论） | run1 10:42：外部误杀本任务 app PID 34372（已由 Lead 确认）；run2 10:45：测试夹具窗口过矮导致 aperture=980×296 < 300，dock fail-closed；两者均无产品结论 |

## 已知限制

- 弹窗场景依赖微信把菜单/预览实现为同进程顶层窗口；本机微信 4.x 不满足，保留人工项。
- 托盘场景用 `SW_HIDE` 复现“关闭到托盘”的 Win32 状态（`IsWindowVisible=false`），恢复走产品
  “启动微信”按钮；真实点关闭按钮留人工步骤。
- 本机存在外部进程反复改微信窗口尺寸/DPI（F2），跨 run 绝对坐标不可比；每个 run 内用 preflight
  矩形做恢复目标，外部变化在证据中标注而不是判产品失败。
- 真实模型建议与发送（A1/A2/A4）不在 C3 范围；0.5.8 尚未打包安装，验收对象是 source tree。
