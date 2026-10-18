# Windows Lite 集中实机验收矩阵（T12）

状态：**准备中，尚未执行。** 本文件用于 T12 最终验收；在包 SHA 与所有自动/人工项完成前，任何一行都不得标记“通过”。
创建：2026-10-18。计划来源：[实施计划](WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md) T12；设计要求见 [完整版设计](WINDOWS_LITE_COMPLETION_DESIGN_2026-10-17.md)。

## 0. 候选包与执行规则

- 候选安装包：待 T2–T11 合并后重新构建；构建时记录 `build_info.json` 的 `source_digest`、git commit、安装器 SHA-256。
- 当前已安装版本 `0.3.1`（安装器 SHA-256 `6A35A86AD8BD4C1F48C877566BB8F578ABFCDE58DA7282766F1DF2FD7DA0F987`）只用于功能自测，**不是 T12 候选包**。
- 每个场景记录：执行时间、包 SHA、Windows/微信版本、缩放比例、执行人、结果（通过/失败/未运行）、证据（截图路径/日志摘要，不保存真实聊天正文）。
- 只有“有执行权限且真实运行”的项才能写通过；无环境/无样本的项写“未运行”并注明原因。
- 用户只需完成系统无法代办的本人扫码/授权与输入法主观确认；其余由自动化与开发验证记录。

## 1. 环境记录（执行时填写）

| 项 | 值 |
| --- | --- |
| Windows 版本/构建 | 待填（参考 26200.9457） |
| 微信版本与安装路径 | 待填（参考 4.1.15.53 / `C:\Program Files\Tencent\Weixin\Weixin.exe`） |
| 屏幕分辨率与缩放 | 待填（100% / 125% / 150%） |
| Python / PyInstaller（构建） | 待填（当前 venv 3.12.14 / 6.22.3） |
| 候选包 SHA-256 | 待填 |
| 账号类型 | 待填（ChatGPT 套餐 / 兼容 API / 本地模型 / Codex） |

## 2. 自动化前置证据（T12 前必须全绿）

| 检查 | 命令/文件 | 状态 |
| --- | --- | --- |
| 全套单元/回环测试 | `python -m unittest discover -s tests -q`（venv） | 未运行（等待 T2–T6 合并） |
| 构建清单一致 | `tools/verify_build_info.py --info dist-release/ChatAssistantLite/build_info.json --source-root .` | 未运行 |
| 打包与便携冒烟 | `build.ps1`（含 packaged `--smoke`） | 未运行 |
| 隔离安装/自检/卸载 | `tools/verify_install.ps1 -Setup dist-release/ChatAssistantLite-Setup.exe` | 未运行 |
| 真实 Codex 免费 RPC | 设置页「检测 Codex 账号与模型」/ `_reports` 记录 | 0.3.1 已验（账号 plus、7 模型 text,image）；最终包需复验 |
| 日志/包内无密钥与正文 | 代码审查 + 安装包扫描 | 未运行 |

## 3. 真实微信场景矩阵（11 项）

| # | 场景 | 通过标准 | 状态 | 证据/执行人 |
| --- | --- | --- | --- | --- |
| 1 | 已登录微信后启动助手 | 自动找到正确窗口并停靠；退出后恢复原位置 | 未运行 | |
| 2 | 测试账号扫码 | 登录窗到主窗衔接；不退出用户现有账号 | 未运行（需用户扫码） | |
| 3 | 单击 / 滚轮 | 单击更换会话正常、滚动顺畅；旧建议立即停用 | 未运行 | |
| 4 | 中文输入法 | 候选窗位置正确、组合输入不丢失、绝不触发发送 | 未运行（需用户主观确认） | |
| 5 | 图片 / 文件 / 右键弹窗 | 原生可操作，不被助手遮挡或当作聊天分析 | 未运行 | |
| 6 | 缩放 / 跨屏（100/125/150%） | 聊天完整、右栏不压缩中央、跨 DPI 坐标正常 | 未运行 | |
| 7 | 最小化 / 还原 / 退出 | 行为符合设计；恢复后重新确认新帧；异常退出可恢复窗口 | 未运行 | |
| 8 | 自动建议 | 新文字自动产生建议；读取/分析后端与版本正确；无需手动分析 | 未运行 | |
| 9 | 历史联动 | 引用导入资料中的早期证据并可定位；未绑定联系人不引用 | 未运行 | |
| 10 | 两账号 | 交错聊天不串记录/建议/授权；不存在自动发送 | 未运行 | |
| 11 | 授权与安装 | 安装版登录/重启/续期/退出正常；升级/卸载与数据保留通过 | 未运行 | |

## 4. 历史与模型专项（T5–T9 完成后补充）

| 检查 | 通过标准 | 状态 |
| --- | --- | --- |
| 0.2 历史库迁移 | 原文数量/顺序/摘要一致；失败回滚；DPAPI 密文保留 | T5 自动化已验；最终包待复验 |
| 全量历史分析 | 覆盖率/证据引用/断点续跑正确；不把片段数当消息数 | 未运行（T7） |
| 档案/趋势/统计/任务界面 | 无可靠时间时显示不可计算；证据可跳转；任务状态一致 | 未运行（T8） |
| 记忆检索与实时建议 | 同名联系人不串；早期事实可命中；建议带引用 | 未运行（T9） |
| 多来源（最多 3 微信） | 两个真实窗口 30 分钟不串线、不互相停采 | 未运行（T10） |

## 5. 发布前检查清单

- [ ] 候选包 SHA-256 与验收记录一致，`build_info.json` 的 `source_digest` 与工作树一致（dirty=false 或已标注）。
- [ ] 全套自动化、`self-test`、`verify_install.ps1`、真实 Codex RPC 复验通过。
- [ ] 11 项真实微信场景与历史/模型专项全部有结论；无“未运行却写通过”。
- [ ] 仓库无密钥/令牌/真实聊天；`_reports` 与 `test-output` 未进入安装包。
- [ ] README、AGENTS、AGENT_HANDOFF、设计、计划、PROGRESS 与本矩阵一致。
- [ ] 安装包、便携包、校验清单、发布说明齐备；提交/发布需用户明确授权。

### 候选包 0.4.1（2026-10-18）

- 版本：0.4.1
- 安装包：`windows/lite/dist-release/ChatAssistantLite-Setup.exe`
- SHA-256：`F8EC437C5E467DDCEC6EA789CC9F1818A7F4C86E42ED89CD2DA604D69D2DC69E`
- Git：`1ac27d7`（feat/windows-lite，构建时 clean）
- 已自动验证：`--version-json`（0.4.1 + source digest）、隔离 `--self-test` exit 0、`--smoke` ×3 exit 0、`tools/verify_install.ps1` 隔离安装/自检/卸载/保留数据 `ok=true`
- 真实微信场景：按本矩阵逐项运行后填写；未运行不写“通过”。
### 0.4.1 UI 自动探测证据（2026-10-18，无截图）

- 安装版主窗口标题：`微信实时助手 · 便捷版 0.4.1`（UIA WindowControl 可见）
- 微信关闭到托盘时，应用状态文本为“微信在运行但窗口不可见；点击‘启动微信’恢复窗口，或从系统托盘打开微信。”——**未重复启动微信**；随后把微信恢复为可见后窗口状态变为 visible（应用下一轮扫描可取到）
- 设置页/自动查找等按钮存在于 UIA 树；通过跨进程 UIA 触发 Qt 导航按钮不可靠（Toggle 不触发页面切换），因此真实点击项保留人工验收
- 其余真实场景仍按本矩阵逐项人工运行；未运行不写“通过”
### 自动回归与真实模型前置条件（2026-10-18）

- 全量回归 3 轮：`python -m unittest discover -s tests -t . -q` → 每轮 **Ran 644, exit 0**（teardown-safe 命令）
- 真实模型验收前置条件当前不满足，需用户处理：
  1) 本机真实 profile 的兼容 API Key 被早期测试/探针覆盖成测试占位值（长度 9 的 `test-key`），`analysis.list_models`/真实分析返回授权失败；已加入占位 Key 警告，需在 App 设置中重新填写真实 Key 并保存。
  2) ChatGPT 套餐真实回合返回额度/不可用错误（InferenceQuotaError），需恢复额度后重试。
- 因此 T9/T12 的真实模型回合（合成历史 + 合成对话）暂未执行，不写“通过”；代码层单测与本地检索证据已覆盖。
### 0.4.1 安装版复核（2026-10-18，自动）

- 单实例：已有实例运行时再次启动，第二进程 **exit 0、约 1.1s 退出**，进程数不变（主进程+恢复守护），未打开第二个窗口
- `--version-json`：0.4.1、clean、119 个源文件；输出不含 `C:\Users` 等个人路径
- `--self-test --data-dir <真实 profile>`：**fail-closed**，`ok=false / isolated=false`，退出码 **1**，真实 profile 文件列表与修改时间无变化（拒绝在正式目录执行自检）
- 隔离 `--self-test`、portable/installed `--smoke`×3、`tools/verify_install.ps1`（install/self-test/uninstall/保留数据）此前已通过
- 真实微信场景仍待人工逐项执行
### OCR 自动找对话（真实窗口，2026-10-18）

- 场景：安装版 0.4.1 已通过 UI 连接微信（状态“正在本地识别 · 中央实时画面”，镜像预览，停靠开关为关；OCR 文字区长度约 390 字符）。
- 旧固定比例方案在真实窗口上失败（`title_mismatch`），改为 OCR 定位搜索框/结果行 + 严格标题校验 + 前台锁兼容激活后：应用处于前台时自动查找“田子夜” **ok=true**（activate→capture→find_search→paste_title→click_result→verify，约 11s）。
- 全程未发送消息；失败路径会按 ESC 关闭搜索并 fail-closed。
- 0.4.1 安装包尚未包含该修复，需下一次打包验证 UI 内的同一流程。
### 候选包 0.4.3（2026-10-18，OCR 自动找对话 / 前台激活修复）

- 版本 0.4.3，安装包 SHA-256：`1F0E7AE47F9FC84E2BD6045AC802E8E3744ED1A661510AA3455416055B17550B`
- Git `5cafa1a`（clean）；`--version-json` 0.4.3，125 个源文件；隔离 `--self-test` exit 0；`package.ps1 -VerifyInstall` install/self-test/uninstall/保留数据 `ok=true`；`--smoke` exit 0
- 已覆盖安装到 `E:\ChatAssistantLite`（0.4.3 窗口标题确认）
- 代码级真实验收：应用前台时 OCR 自动找对话“田子夜” `ok=true`（约 11s，未发送消息）
- 应用内一键自动找对话的跨进程点击验证受 UIA/连接时序影响仍不稳定，保留人工点击验收（0.4.1 已验证应用内可连接微信并显示镜像预览）
### OCR 标题误识限制（自动找对话，2026-10-18）

- 真实窗口实测：自动找“田子夜”通过（严格标题校验）。
- “郜亚禹”在当前 OCR 下头部常被识别成“部亚禹/鄢子夜”一类单字差异；自动找对话会**fail-closed**（ESC 关闭、不发送、不切换），不会冒险切到错误会话。已加入结果行重试与头部 2x 放大校验，仍无法稳定区分时保持失败。
- 结论：自动找对话对 OCR 可读的名字可用；不可读/易混名字需人工操作或后续 UIA/名称索引方案（见 OCR 依赖分析文档）。
### 镜像预览缩放实证（0.4.3，自动）

- 应用窗口宽度 +140px 后：应用窗口已改变，**原生微信窗口矩形完全不变**（`[243,141,1482,934]` → 同值），确认镜像预览默认模式不会移动/缩放原生微信。
- 停靠开关默认关闭（0.4.1 同项实测 ToggleState=0）。
- 应用内连接微信与 OCR 识别（当前识别文字非空，实测 390 字符）此前已在 0.4.1/0.4.3 验证；UIA 跨进程连接按钮偶发因窗口刷新时序失败，真实用户点击不受影响，保留人工复验。
### 候选包 0.5.2（D-UI 最终候选，2026-10-18）

- 版本 0.5.2；安装包 SHA-256 `34740332974CDCEB840E8588F46E28C333B39BE3081560D40F531E83F48042C2`
- Git `3dea3b1`（clean）、126 个源文件；`--version-json` 0.5.2；隔离 `--self-test` exit 0；`--smoke` exit 0；`package.ps1 -VerifyInstall` install/self-test/uninstall/保留数据 `ok=true`；已覆盖安装到 `E:\ChatAssistantLite`
- D-UI：哑光纸 + 青瓷设计系统；窗口 1000×520 / 最小 900×500，小屏（1536×864@150%）下内容完整显示；状态行（连接/后端/模型/分析）可见；AI 建议拨片、只读提示、占位 Key 警告可见
- 全量测试 660 项 exit 0（含 3 项设计系统测试）
- 真实微信场景（连接、托盘、自动查找、发送、弹窗/双窗口）仍按 T12 人工逐项验收；本候选包不含未提交改动
### 候选包 0.5.3（D-UI 最终候选，2026-10-18）

- 版本 0.5.3；安装包 SHA-256 `00946EDF830E1A1D9A936A01E01592DE1D60A83205465F7400FD943CA3EDA549`
- Git `6d40c4a`（clean）、126 个源文件；`--version-json` 0.5.3；隔离 `--self-test` exit 0；`--smoke` exit 0；`package.ps1 -VerifyInstall` install/self-test/uninstall/保留数据 `ok=true`；已覆盖安装到 `E:\ChatAssistantLite`
- D-UI：状态行单行可见（连接状态 / 后端+模型 / 分析状态）；1000×520 窗口 / 900×500 最小，小屏不裁切；截图见 `windows/lite/test-output/final-chat.png`
- 全量 660 项测试 exit 0
- 人工 T12 清单见 `docs/windows/T12_MANUAL_ACCEPTANCE_HANDOFF_2026-10-18.md`
### 候选包 0.5.4（托盘保留修复，2026-10-18）

- 版本 0.5.4；安装包 SHA-256 `5F5DE851805B8E25894DDB14D615BCF98F0374799AD49457153F9E009AAE8832`
- Git `96e39a2`（clean）、127 个源文件；`--version-json` 0.5.4；隔离 `--self-test` exit 0；`--smoke` exit 0；`package.ps1 -VerifyInstall` install/self-test/uninstall/保留数据 `ok=true`
- 修复：微信关闭到托盘后，扫描不再断开采集；同一 hwnd 存在隐藏窗口时保留连接并提示“微信已隐藏到托盘，仍在后台读取”。真实自动连接后隐藏窗口：状态文案出现（“仍在后台读取”/“暂时无法读取画面”），恢复后微信重新可见；自动连接设置已还原。
- 自动检查中未稳定取到识别文字长度（UIA 读 QPlainTextEdit 内容不稳定），托盘隐藏时的实际 OCR 文字需人工看界面确认；worker 隐藏捕获路径有单测覆盖。
- 全量 662 项测试 exit 0
### 托盘隐藏读取真实通过（0.5.4 安装版，2026-10-18）

- 自动连接一个正常会话后：`识别到的聊天文字` 非空（11 字符），随后用 `ShowWindow(SW_HIDE)` 模拟关闭到托盘
- 隐藏期间：采集连接保持，识别文字仍为 11 字符（未清空、未断连），微信窗口保持隐藏
- 恢复：`discovery.restore_window` 后微信重新可见、识别文字保持；测试临时开启的 `auto_connect` 已还原为 false
- 结论：S-V04 托盘隐藏读取通过（自动检查，未保存聊天内容）；真实点击关闭到托盘的行为一致
### 候选包 0.5.5（点4 模型记忆修复，2026-10-18）

- 版本 0.5.5；安装包 SHA-256 `1D84EFDA2B70799E3BEEB2ECC5F5913A92B4BE820E02A81C3FCB24801C517B6C`
- Git `ac23099`（clean）、128 个源文件；`--version-json` 0.5.5；隔离 `--self-test` exit 0；`--smoke` exit 0；`package.ps1 -VerifyInstall` ok=true；已覆盖安装到 `E:\ChatAssistantLite`
- 点4 真实验证：连续两次关闭/重开 App，设置页均显示“已连接 ChatGPT：ch…@163.com”，账号记录与授权保留，不触发浏览器授权；`chatgpt_model` 选择现在在点选时立即持久化（`settings.remember_model`，不触碰凭据），重开 App 保留
- 全量 665 项测试 exit 0
### UI 内自动找对话自动探测（0.5.5，2026-10-18）

- 自动连接后通过应用自己的设置页按钮（UIA InvokePattern）触发自动查找：
  - “田子夜”：应用内返回“搜索结果里没有找到该联系人”，未切换、未发送；同一真实窗口用代码级 `find_conversation` 直接调用仍是 `ok=true`（约 11s）
  - “文件传输助手”同样返回未找到
- 判断：跨进程 UIA 触发时，搜索框/结果时序与真实人工点击不同（可能搜索覆盖层未刷新）；代码级真实验收已通过，但“应用内点击”路径仍需人工复验。
- 结论：S-V10 保持未签收；人工点击是最终判据，不以该自动探测结果写通过。
### 候选包 0.5.6（托盘捕获恢复加固，2026-10-18）

- 版本 0.5.6；安装包 SHA-256 `8E950167A23D8D072659EDF437620A25BD22E31EA859DCAD88E71F83239B3928`
- Git `14cce10`（clean）、129 个源文件；`--version-json` 0.5.6；隔离 `--self-test` exit 0；`--smoke` exit 0；`package.ps1 -VerifyInstall` ok=true；已覆盖安装到 `E:\ChatAssistantLite`
- 发现并修复：托盘捕获后窗口可能停在离屏位置/仍可见；现在用 `SWP_HIDEWINDOW` 原子恢复几何+隐藏，若仍可见再隐藏；新增“隐藏窗口几何/可见性恢复”测试
- 自动找对话：代码级真实查找“田子夜” `ok=true`；通过 UI 按钮跨进程触发仍偶发 `result_not_found/search_not_found`（应用内自动连接、读数本次成功：编辑区 106 字符），已加大搜索重试与 4 帧结果等待，仍需人工点击复验；不写通过
- 全量 666 项测试 exit 0
### 候选包 0.5.7（动作期间暂停采集，2026-10-18）

- 版本 0.5.7；安装包 SHA-256 `35C37FEC197C9B088FB79F36F831B9624093BD6D3427B898AFA679B57CE3E34E`
- Git `32fc8f2`（clean）、130 个源文件；`--version-json` 0.5.7；隔离 `--self-test` exit 0；`--smoke` exit 0；`package.ps1 -VerifyInstall` ok=true
- 真实复现：应用已连接时，第二个 WGC 会话（动作里的 OCR 采集）会被 UI 的持续采集抢帧，导致 `search_not_found/result_not_found`；改为动作前 `stop_capture()`、动作结束后 `wechat_action_done` 自动重连
- 修复后 UI 跨进程触发仍然得到 `没有找到微信搜索框`；代码级直接查找在相同窗口 `ok=true`。说明还有跨进程/前台时序因素，UI 点击路径保留人工验收，不写通过
- 全量 668 项测试 exit 0
### 候选包 0.5.9（Codex 接管后 clean build，2026-10-18）

- 版本 0.5.9；安装包 SHA-256 `119FCC834E2D280CA3093EB804AB2835A786E65D720F85D893A86A52240BE1B6`
- Git `13c7ff3`（clean）、147 个源文件；`--version-json` 0.5.9；隔离 `--self-test` exit 0；`--smoke` exit 0；`package.ps1 -VerifyInstall` ok=true；已覆盖安装到 `E:\ChatAssistantLite`；桌面/开始菜单快捷方式由安装器重建
- 包含 DeepSeek 临时接管的 C1（安全 UI find 路径/H3/H4/P2-10）、C2（隔离升级验证工具）、C3（真实窗口验收工具）、C4（屏幕抓取+exact+foreground 守卫）、C5（合成验收）以及 Codex 0.5.8 基线
- 全量：**750 tests OK / 37 skipped / exit 0**（提交前）；完整 clean-VM/真实模型/A4 发送/双微信仍待用户/Codex
### 候选包 0.5.12（悬浮窗 + 布局/托盘修复，2026-10-18）

- 版本 0.5.12；安装包 SHA-256 `4C63636F1130AB128CE5DA15010C39BADCBA106125EB35A2285B8C35E724A7BB`
- Git `a571d17`（clean，149 源文件）；version-json/自检/smoke/隔离安装全通过；已覆盖安装到 `E:\ChatAssistantLite`
- **悬浮窗模式**：主界面“悬浮窗”按钮 → 置顶无边框小窗（状态/会话名/分析状态/“分析当前对话”/“主界面”），主窗隐藏后采集与分析继续；完整功能仍在主界面
- **布局**：预览默认“适应区域”并居中，100%/150%/200% 也居中；AI 建议面板切换不再扩大窗口，最大化时仍在窗内显示
- **托盘隐藏读取**：修复 tray_capture 64 位 GDI 类型声明与 cloaked/offscreen 判定；minimized 隐藏抓帧实测返回 438×451，不再误报“暂时无法读取画面”
- **模型**：真实 profile 的 `DeepSeek-V4.1-Flash`（显示名，HTTP 400）规范为可调用 id `deepseek-flash`；合成文本真实回合返回 3 条建议
- 全量 **753 tests OK / 37 skipped / exit 0**
- 14:51 后 Codex 任务：C6 悬浮窗真实化/不确定行为、C7 托盘隐藏读取可靠性、C8 布局与旧模型显示名迁移验证