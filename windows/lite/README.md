# ChatAssistant Lite（Windows）

Windows 上的**微信会话理解与 AI 回复工作台**：把官方微信的原生窗口停靠在应用中央，读取当前会话，交给所选模型生成回复建议，用户核对后自己复制、在微信里手动发送。桌面端用 PySide6（Qt 6）实现，打包形态是 PyInstaller onedir 便携目录 + Inno Setup 6 用户级安装器。

本文件只描述 `windows/lite/` 这一实现；移动端与整体产品方案的入口见[仓库根 README](../../README.md)。

## 当前状态

| 项目 | 现状 |
|---|---|
| 已装版本 | **0.5.30**（本机注册表项 `ChatAssistant Lite version 0.5.30`，安装目录 `E:\ChatAssistantLite\`） |
| 版本号来源 | `liteapp/build_info.py` 的 `APP_VERSION = "0.5.30"`；`installer/ChatAssistantLite.iss` 的 `AppVersion` 同为 `0.5.30` |
| 仓库分支 | `feat/windows-lite`（工作树 dirty，有未提交改动） |
| 最近构建 | `dist-release/ChatAssistantLite/`（onedir）+ `dist-release/ChatAssistantLite-Setup.exe`；随包的 `build_info.json` 记录分支 `feat/windows-lite`、commit `37c86b2`、`dirty: true` |
| 进行中 | **0.6.0 UI/UX 重设计**：设计稿已落盘、审计证据已入库、设计系统已落码但**尚未接入主窗口**（详见下文「0.6.0 UI 重设计」） |

## 能力范围

- **官方微信原生窗口中央停靠**：按设置里的完整 exe 路径发现窗口，自动连接唯一候选（多个窗口时在设置里选）。中央区域把**未改父级、未改样式的原生微信顶层窗口**对齐到应用开口，保留微信自己的鼠标、输入法和弹窗处理；停靠失败时退化为只读实时画面。相关模块：`discovery.py`、`window_dock.py`、`window_manager.py`、`wechat_locator.py`、`wechat_actions.py`。
- **本地 OCR 与多模态读取**：`windows-capture` 抓帧 + `rapidocr-onnxruntime` 离线识别当前画面中**可见**的文字（不上传画面）；可改选多模态模型（OpenAI 兼容视觉接口）或 Codex 应用（官方 app-server 图像输入）。相关模块：`reading.py`、`realtime.py`、`capture_scheduler.py`、`backends.py`、`codex_backend.py`。
- **右侧 AI 建议**：模型就绪后，新识别的对话自动生成右侧可展开的判断与回复建议；建议只供复制，由用户核对后在微信手动发送。右侧展开时应用增加宽度，不压缩中央聊天区。相关模块：`inference.py`、`inference_queue.py`、`inference_schemas.py`。
- **悬浮球**：独立的桌面悬浮球（收起/展开两态），用于快速查看与唤起。相关模块：`floating.py`。
- **HTML/PDF 历史导入与分析**：按手动指定的微信账号与联系人导入 HTML/PDF，分页浏览、搜索并生成离线整理报告。HTML 按纯文本解析，不执行脚本、不加载远程资源；有密码或无文字层的 PDF 会提示用户。相关模块：`importers/`（`exportwechat_html.py`、`exportwechat_json.py`、`html_generic.py`、`pdf.py`）、`history*.py`、`history_ui.py`。
- **多账号**：账号与联系人分开存储，不同账号或同名联系人不会自动合并。相关模块：`account_store.py`、`conversation_binding.py`、`source_registry.py`、`multi_source_monitor.py`。
- **DSH 传输层**：`dsh_models.py` 只读发现本机 DeepSeek Harness（`~/.dsh`）已配置的 provider/model（展示用掩码 Key，不写 DSH 文件）；`dsh_transport.py` 在 `openai-responses` 方言与内部使用的 Chat Completions 形状之间做单向翻译（不持有密钥、拒绝重定向）。两者都是 2026-10-19 新增、尚未进入已装 0.5.30 的构建。
- **单实例与崩溃恢复**：重复启动不会开第二个窗口，只请求激活已有实例；`recovery_guard.py` 的守护子进程在主进程异常退出后恢复微信窗口原位置。相关模块：`single_instance.py`、`recovery_guard.py`。

## 快速开始（开发态）

### 解释器

```text
G:\codex\wechat-lite-venv\Scripts\python.exe
```

本次核对到的版本：Python **3.12.14**、PySide6 **6.12.0**、pytest **9.1.1**、PyInstaller **6.22.3**（与 `build.ps1` 强制的版本一致）。

### 运行

```powershell
cd G:\codex\ChatAssistant\windows\lite
& G:\codex\wechat-lite-venv\Scripts\python.exe main.py
```

`main.py` 还提供若干无界面入口（都不弹窗，供脚本与安装器使用）：

| 参数 | 作用 |
|---|---|
| `--version-json` | 打印版本与源码摘要的 JSON；冻结版读 exe 同级的 `build_info.json`，缺失时回退并标注 `error` |
| `--self-test [--data-dir <目录>]` | 隔离自检，不碰真实用户配置、不联网；输出 JSON，`ok=false` 时退出码为 1 |
| `--smoke` | 启动 UI 后 1.5 秒自动退出；`build.ps1` 用它在 `QT_QPA_PLATFORM=offscreen` 下做打包后冒烟测试 |
| `--lite-pdf-worker` | PDF 解析子进程入口（导入 PDF 时由主进程拉起） |
| `--recovery-guard` | 崩溃恢复守护子进程入口 |

### 跑测试

```powershell
cd G:\codex\ChatAssistant\windows\lite
& G:\codex\wechat-lite-venv\Scripts\python.exe -m pytest tests -q
```

`tests/` 下共 **88 个 `.py` 文件**，其中 **86 个 `test_*.py`**，另有 `fake_codex_app_server.py`（假 app-server 夹具）和 `__init__.py`。单元与本地回环测试覆盖窗口身份、采集状态、授权回调、历史解析、停靠几何恢复、后端路由、Codex 协议与界面门控；它们**不能**证明真实微信的扫码、多开、单击、滚轮、中文输入法与弹窗都通过（见「已知问题与边界」）。

## 构建与打包

构建脚本默认使用 `windows\lite\.venv`；本目录**没有** `.venv`，所以必须先通过 `CHATASSISTANT_PYTHON` 指向上面那个解释器：

```powershell
cd G:\codex\ChatAssistant\windows\lite
$env:CHATASSISTANT_PYTHON = 'G:\codex\wechat-lite-venv\Scripts\python.exe'

.\build.ps1                    # PyInstaller onedir → dist-release\ChatAssistantLite\
.\package.ps1                  # 追加 Inno Setup 6 → dist-release\ChatAssistantLite-Setup.exe
.\package.ps1 -VerifyInstall   # 追加 tools\verify_install.ps1 的安装→自检→卸载回归
```

`build.ps1` 的实际校验与产物：

- 解释器必须是 `PyInstaller 6.22.3`，且 `PySide6`、`rapidocr_onnxruntime`、`windows_capture`、`pypdf` 都必须可导入，`python3.dll` 必须存在。
- 打包命令与仓库里的 `ChatAssistantLite.spec` **无关**：脚本直接以 `python -m PyInstaller --onedir --windowed --name ChatAssistantLite main.py` 加 `--collect-all`（`rapidocr_onnxruntime` / `windows_capture` / `pypdf`）、`--add-data`（`assets`、`licenses`）、`--icon` 打包；产物在 `dist-release\ChatAssistantLite\`，工作目录是 `build-pyinstaller\`。
- 打包后会删掉 `_internal` 里随宿主 PATH 混进来的 `api-ms-win-*.dll` 与 `icu*.dll`（否则 QtWidgets 可能加载失败），再写入并校验 `build_info.json`（源码摘要 + 源码文件数），最后用 `--smoke` 跑一次 90 秒上限的冒烟测试，失败即中止。
- `package.ps1` 依次执行 `build.ps1` → 再校验一次 `build_info.json` → 调 `ISCC.exe` 编译安装器。`ISCC.exe` 先查 PATH，找不到再回退到 `%USERPROFILE%\AppData\Local\Programs\Inno Setup 6\ISCC.exe`。
- 安装器默认**按用户安装**：`{localappdata}\Programs\ChatAssistantLite`，`PrivilegesRequired=lowest`，`Compression=lzma2`；定义 `VerificationBuild` 宏时改为产出 `test-output\ChatAssistantLite-Verification.exe` 且 `Uninstallable=no`（用于隔离验证安装，不写卸载项）。

**产物目录只认 `dist-release\`**。同级还有 `dist`、`dist-console`、`dist-portable`、`dist-pyinstaller`…`dist-pyinstaller5`、`build-*`、`probe-*` 等历史实验目录（最后一次改动停在 2026-10-15/16），不是交付物，也不需要清理才能构建。

## 0.6.0 UI 重设计（进行中）

### 设计真源

[docs/windows/UI_REDESIGN_2026-10-19.md](../../docs/windows/UI_REDESIGN_2026-10-19.md)（774 行）：信息架构、两种窗口尺寸的布局、组件与状态规范、设计 token、分阶段迁移与验收门槛。文档头部状态写的是「待 Lead 评审」；落地以代码与其后续提交为准。

### 核心主张

取消现在**三套并存**的导航与面板模式，改为「左侧主导航 + 中央工作台 + 按需上下文面板」：

- 现状基线（可核对）：左侧导航轨 `rail.setFixedWidth(76)`（`liteapp/ui.py:1254`）；聊天页、历史页、设置页三页并列；右侧 AI 抽屉 `self.ai_panel.setFixedWidth(300)`（`liteapp/ui.py:1375`）。
- 新结构：全局顶部只放应用名、微信连接状态、后台任务状态与窗口控制；当前会话相关的历史参考、风格档案、分析详情收进**可折叠的上下文面板**，不再把 AI 面板当独立页面。
- 四个一级区域：

| 一级区域 | 职责 | 定位 |
|---|---|---|
| 工作台 | 微信连接状态、当前会话、识别预览、回复建议、发送操作 | 默认首页，覆盖最高频任务 |
| 会话档案 | 历史记录、风格档案、好感度、模型分析结果 | 以当前联系人或会话为主线 |
| 任务与记录 | 采集记录、分析历史、发送记录、失败任务 | 支持筛选、重试、查看失败原因 |
| 设置 | 微信连接、ChatGPT 账号、模型、自动分析、快捷键、日志诊断 | 集中配置，不承载日常操作 |

- 尺寸目标：1280×800 为标准工作模式（导航约占 15%、上下文面板初始约占 25%，都是比例目标而非固定像素）；900×500 进入紧凑模式，右侧面板默认收起、预览与识别文字分段切换；最小窗口建议 860×480，低于标准宽度自动进入紧凑模式。适配靠 `minimumWidth` / `sizePolicy` / 拉伸因子，不再依赖 `setFixedWidth`。

### 现状基线截图

[docs/windows/ui-audit-2026-10-19/](../../docs/windows/ui-audit-2026-10-19/)：**26 张 PNG** = 13 张控件抓图（`01-chat-empty-1000.png` … `13-profile-small.png`）+ 13 张同场景的原生窗口截图（`native/` 子目录）。迁移时用它逐屏对比，避免「改完说不清哪里变了」。

### 设计系统

已落地的三个文件：

| 文件 | 内容 |
|---|---|
| `liteapp/design/theme.py` | Token 唯一真源：`COLORS` / `SPACING`（4px 基数）/ `RADIUS` / `BORDER` / `FONT_FAMILY` / `FONT_SIZE` / `FONT_WEIGHT` / `DURATION` / `SIZES`，由 `qss_vars()` 摊平成 `@token -> 值`。纯数据与纯函数，不导入 PySide6、不读写文件。 |
| `liteapp/design/style_loader.py` | QSS 定位与加载：把 `@token` 替换成 token 值；按开发态 `liteapp/resources`、冻结态 `sys._MEIPASS/liteapp/resources`、`_MEIPASS/resources`、exe 同级 `resources` 与 `_internal/...` 依次查找；文件缺失只打印一行警告并返回空串，不让 UI 起不来；结果带 `lru_cache`。 |
| `liteapp/resources/styles/global.qss` | 统一基础样式表（本次核对 421 行）。文件头声明所有颜色、字号、边框、圆角、间距与控件尺寸都用 `@token` 占位，由 `style_loader` 加载时替换；布局、尺寸计算、动态状态与动画由 Python 负责。 |

**落地进度**：三个文件已存在，但 `liteapp/ui.py` 目前**没有任何**对 `style_loader` / design 包的引用（本次核对为 0 处），也就是说样式表还没有挂到主窗口上——这是第 ② 阶段剩下的接线工作。

**包名坑（必读）**：设计稿原文写的是新建 `ui/theme.py`，但本仓库已经有 `liteapp/ui.py`。如果在 `liteapp/` 下再建一个 `ui/` 包，包目录会在导入时遮蔽同名模块 `liteapp.ui`，导致 `from liteapp.ui import MainWindow` 直接坏掉。因此落地包名是 **`liteapp/design/`**（`theme.py` + `style_loader.py`），不要在 `liteapp/` 下创建 `ui/` 目录。

### 分阶段迁移与当前进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| ① 建立基线与回归 | 固化 26 张截图、关键操作测试与窗口尺寸测试；记录连接、识别、生成、复制、发送、切号现有行为；保证可回滚 | 进行中（截图已入库） |
| ② 抽离设计系统 | `resources/styles/global.qss` + `design/theme.py` + 统一状态组件；先统一主窗口、悬浮球、按钮与表单样式，不改业务逻辑 | 进行中（文件已落码，待接入） |
| ③ 重构主布局 | 引入 `MainShell`、`NavigationBar`、`WorkspacePage`、`ContextPanel`，再把现有页面逐步挂到新布局；先迁容器、后调视觉 | 未开始 |
| ④ 拆分业务页面与控制器 | 设置、档案、任务记录迁到独立模块，用信号槽连回原有业务方法；新旧界面用功能开关切换 | 未开始 |
| ⑤ 状态管理补齐与发布 | 统一加载/空态/失败/重试/取消/发送反馈；版本号改为从 `build_info` 单点读取；验证 onedir 打包与安装升级 | 未开始 |

验收门槛（设计稿结论）：主工作流可连续完成；窗口缩放正常；耗时操作不冻结 UI；关键任务具备成功/失败/加载/空态；现有功能无回归；新版本能正常安装、升级和回滚。

## 已知问题与边界

- **真实微信交互仍需逐项验收**：单元与本地回环测试覆盖不到真实微信的**单击、滚轮、中文输入法、扫码登录、弹窗**，也无法证明**远程读取质量**。旧版 `SetParent` 探针曾出现单击和滚轮失效；新版顶层停靠已实测中央命中微信，但上述交互仍需人工逐项验收。
- **`ui.py` 过大待拆**：当前 **4227 行**（本次核对），`MainWindow.__init__` 从第 1150 行到第 1482 行、共 **333 行**。设计稿建议按职责拆成 `ui/main_window.py`、`ui/shell.py`、`ui/pages/*`、`ui/widgets/*`、`ui/controllers/*`，第一阶段允许旧 `ui.py` 继续作为兼容入口。
- **版本号仍是两处硬编码**：`liteapp/build_info.py` 与 `installer/ChatAssistantLite.iss` 各写一遍 `0.5.30`，改版本必须同时改；阶段⑤计划改为从 `build_info` 单点读取。
- **构建依赖本机环境**：`build.ps1` 强制 PyInstaller 6.22.3 与 `python3.dll`；没有 `.venv` 时必须设 `CHATASSISTANT_PYTHON`。安装器需要 Inno Setup 6（本机已装在 `%LOCALAPPDATA%\Programs\Inno Setup 6`）。
- **测试结果以实际运行为准**：本文件不复述历史通过数；「86 项单测通过」等是 2026-10-18 的当时记录，最新状态请按上面的命令自己跑。真实安装器的升级/卸载回归也需要 `-VerifyInstall` 才会执行。
- **DSH 传输层未进已装版本**：`dsh_transport.py` / `dsh_models.py` 是 2026-10-19 的新增未跟踪文件，已装 0.5.30 不含它们；其真实端点联调尚未验证。

## 本地数据与模型

文字读取（屏幕 → 聊天文字）与分析（聊天文字 → 建议）是两套独立配置，可以分别选择不同服务与模型：

- **读取**：默认本地 OCR（离线，不上传画面）；可选多模态模型（OpenAI 兼容视觉接口）或 Codex 应用（官方 app-server 图像输入）。选择后两者时，只有当前聊天画面会发送给所选服务用于转写；失败自动回退本地 OCR，远程确认后的文字不会被本地 OCR 覆盖。
- **分析**：ChatGPT Plus/Pro 官方 Sign in with ChatGPT（账号模型目录）、兼容 API、本地模型服务（OpenAI 兼容，默认 `http://127.0.0.1:11434/v1`，可不填 Key）或 Codex 应用（官方 app-server，只读沙箱、审批 `never`、拒绝服务端工具请求）。
- **模型记忆**：每个分析后端分别记住自己的模型；重开应用或在后端之间来回切换后自动恢复，不会互相覆盖。ChatGPT 授权与模型目录持久保存，只有授权失效或手动「断开 ChatGPT 授权」时才需要重新连接。
- **兼容/本地 API**：先选厂商预设（DeepSeek 官方 / OpenRouter / 通义兼容 / 本地 Ollama / 自定义），填地址与 Key 后可「读取模型列表」（请求接口 `GET /models`）并下拉选择；设置页和聊天页都显示当前生效的后端与模型。

Codex 后端使用本机已登录的官方 Codex，不读取或复制 Codex 桌面凭据；本软件不会自动读取 Codex 桌面的当前模型或登录令牌。具体可用模型和额度以各服务返回为准。OAuth 凭据、分析与读取 API Key 由 Windows DPAPI 加密到 `%LOCALAPPDATA%\ChatAssistantLite\settings.json`。导入历史在同目录的 `memory.sqlite3`；实时画面与 OCR 快照不持久化、不写日志。只有自动分析准备就绪时，当前可见对话文本才发送到所选分析后端；历史库与离线报告不会自动上传。

官方流程：[Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source)、[账号和用量](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)。

## 目录速览

```text
windows/lite/
  main.py                 启动入口（单实例、守护进程、无界面模式）
  build.ps1                PyInstaller onedir 打包 + build_info 校验 + 冒烟
  package.ps1              build.ps1 + Inno Setup 编译（可选安装回归）
  requirements*.txt        运行/构建依赖（构建锁定 PyInstaller 6.22.3）
  liteapp/                 应用代码
    ui.py                  主窗口与现有全部页面（4227 行，待拆）
    floating.py            悬浮球
    design/                设计系统：theme.py（token）+ style_loader.py（@token 加载）
    resources/styles/      global.qss（统一基础样式表）
    importers/             历史导入（HTML / PDF / ExportWeChat）
    dsh_transport.py       DSH responses ⇄ chat completions 翻译
    dsh_models.py          只读发现 DSH 已配置模型
  installer/               Inno Setup 6 脚本
  assets/                  图标与随包资源
  licenses/                第三方许可
  tests/                   88 个 .py（86 个 test_*.py）
  preview/index.html       Codex 侧栏用的界面预览（示例内容，非安装包）
```

## 相关文档

- UI 重设计规范：[UI_REDESIGN_2026-10-19.md](../../docs/windows/UI_REDESIGN_2026-10-19.md)
- 现状基线截图：[ui-audit-2026-10-19/](../../docs/windows/ui-audit-2026-10-19/)
- 验收清单：[WINDOWS_LITE_ACCEPTANCE_CHECKLIST_2026-10-18.md](../../docs/windows/WINDOWS_LITE_ACCEPTANCE_CHECKLIST_2026-10-18.md) · 验收矩阵：[WINDOWS_LITE_ACCEPTANCE_MATRIX.md](../../docs/windows/WINDOWS_LITE_ACCEPTANCE_MATRIX.md)
- 验证记录：[WINDOWS_LITE_VERIFICATION_2026-10-17.md](../../docs/windows/WINDOWS_LITE_VERIFICATION_2026-10-17.md)
- 架构说明：[WINDOWS_LITE_ARCHITECTURE.md](../../docs/windows/WINDOWS_LITE_ARCHITECTURE.md)
- 未完成项：[WINDOWS_LITE_UNFINISHED_2026-10-18.md](../../docs/windows/WINDOWS_LITE_UNFINISHED_2026-10-18.md) · 功能报告：[FEATURE_REPORT_2026-10-18.md](../../docs/windows/FEATURE_REPORT_2026-10-18.md)
- 产品方案：[WINDOWS_WECHAT_PRODUCT_PLAN.md](../../docs/windows/WINDOWS_WECHAT_PRODUCT_PLAN.md) · 智能体交接：[AGENT_HANDOFF.md](../../docs/windows/AGENT_HANDOFF.md)

OCR/定位参考 [jev-chat/jev-chat-windows](https://github.com/jev-chat/jev-chat-windows)，第三方许可随包附带。
