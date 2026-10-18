<<<<<<< HEAD
=======
# ChatAssistant
>>>>>>> b921339

ChatAssistant 是 Windows 桌面端的微信辅助工具：把官方微信的原生窗口停靠在应用中央，读取当前会话，交给所选模型生成判断与回复建议，用户在微信里自己核对、手动发送。主实现是 [`windows/lite/`](windows/lite/README.md)（ChatAssistant Lite），桌面端用 PySide6（Qt 6）编写。

> 只处理你自己设备上、你自己有权查看的聊天；程序不自动发送，回复由你在微信里手动发出。

## 能力范围（`windows/lite/`）

- **官方微信原生窗口中央停靠**：把未改父级、未改样式的原生微信顶层窗口对齐到应用开口，保留微信自己的鼠标、输入法和弹窗处理；停靠失败时退化为只读实时画面。
- **本地 OCR 与多模态读取**：`windows-capture` 抓帧 + `rapidocr-onnxruntime` 离线识别当前画面中可见的文字（不上传画面）；也可改选 OpenAI 兼容视觉接口或 Codex 应用（官方 app-server 图像输入）。
- **右侧 AI 建议**：模型就绪后，新识别的对话自动生成右侧可展开的判断与回复建议；建议只供复制。右侧展开时应用增加宽度，不压缩中央聊天区。
- **悬浮球**：独立的桌面悬浮球（收起 / 展开两态），用于快速查看与唤起。
- **HTML / PDF 历史导入**：按手动指定的微信账号与联系人导入 HTML/PDF，分页浏览、搜索并生成离线整理报告；HTML 按纯文本解析，不执行脚本、不加载远程资源。
- **多账号**：账号与联系人分开存储，不同账号或同名联系人不会自动合并。
- **DSH 传输层**：`dsh_transport.py` / `dsh_models.py` 只读发现本机 DeepSeek Harness 已配置的 provider / model（展示用掩码 Key），并在 `openai-responses` 方言与内部 Chat Completions 形状之间做单向翻译；两者尚未进入已装版本。

**已装版本 0.5.30**，当前工作分支 `feat/windows-lite`。版本号来源与安装目录等易过期细节以 [`windows/lite/README.md`](windows/lite/README.md) 为准。

## 处理流程

```text
官方微信原生窗口（中央停靠）
        ↓ 本地 OCR / 多模态读取
当前会话文字（读取结果）
        ↓ 所选分析后端
判断与回复建议（右侧面板）
        ↓ 用户复制 / 手动发送
微信输入框
```

读取（屏幕 → 对话文字）与分析（对话文字 → 建议）是两套独立配置，可以分别选择不同服务与模型。

## 0.6.0 UI/UX 重设计（进行中）

取消现有的「76px 导航轨 + 三页并列 + 右侧 AI 抽屉」，改为「**左侧主导航 + 中央工作台 + 按需上下文面板**」。四个一级区域：

| 一级区域 | 职责 |
|---|---|
| 工作台 | 微信连接状态、当前会话、识别预览、回复建议、发送操作 |
| 会话档案 | 历史记录、风格档案、好感度、模型分析结果 |
| 任务与记录 | 采集记录、分析历史、发送记录、失败任务 |
| 设置 | 微信连接、模型、自动分析、快捷键、日志诊断 |

- 设计真源：[`docs/windows/UI_REDESIGN_2026-10-19.md`](docs/windows/UI_REDESIGN_2026-10-19.md)（信息架构、布局、组件与状态规范、设计 token、分阶段迁移与验收门槛）。
- 现状基线：[`docs/windows/ui-audit-2026-10-19/`](docs/windows/ui-audit-2026-10-19/) 存有 13 张控件抓图 + 13 张同场景原生截图，迁移时逐屏对比。

## 开发、测试与打包

解释器路径、`pytest tests`、PyInstaller onedir + Inno Setup 6 的确切命令、产物目录与构建环境要求，见 [`windows/lite/README.md`](windows/lite/README.md)。本文件不复述这些命令，避免两处漂移。

## 本地数据与隐私

- OAuth 凭据与分析 / 读取 API Key 由 Windows DPAPI 加密保存在 `%LOCALAPPDATA%\ChatAssistantLite\settings.json`；导入的历史在同目录的 `memory.sqlite3`。
- 实时画面与 OCR 快照不持久化、不写日志；只有自动分析准备就绪时，当前可见的对话文本才会发送到所选分析后端。
- 历史库与离线报告不会自动上传。

## 验收边界

- 真实微信的**单击、滚轮、中文输入法、扫码登录、弹窗**，以及**远程读取质量**，仍需逐项人工验收；单元与本地回环测试覆盖不到这些。
- 旧 `SetParent` 探针出现过单击 / 滚动异常，不代表新的顶层停靠方案已通过全部交互验收；[微信嵌入测试结果](docs/windows/WECHAT_EMBED_TEST_RESULTS_2026-10-14.md) 记录的是当时探针的结论。
- 未验收项不得视为已修复；清单见 [验收清单](docs/windows/WINDOWS_LITE_ACCEPTANCE_CHECKLIST_2026-10-18.md) 与 [验证记录](docs/windows/WINDOWS_LITE_VERIFICATION_2026-10-17.md)。

## 相关目录

| 路径 | 说明 |
|---|---|
| [`windows/lite/`](windows/lite/) | ChatAssistant Lite 应用实现（主程序、设计系统、导入器、安装器） |
| [`windows/probes/wechat-embed/`](windows/probes/wechat-embed/README.md) | 微信窗口嵌入探针与兼容性测试记录 |
| [`docs/windows/`](docs/windows/) | 产品方案、架构、UI 重设计、验收清单与审计证据 |

## 文档入口

- Windows 产品方案：[`docs/windows/WINDOWS_WECHAT_PRODUCT_PLAN.md`](docs/windows/WINDOWS_WECHAT_PRODUCT_PLAN.md)
- 智能体交接：[`docs/windows/AGENT_HANDOFF.md`](docs/windows/AGENT_HANDOFF.md)；工作区协作约定见 [`AGENTS.md`](AGENTS.md)
- Lite 实现说明：[`windows/lite/README.md`](windows/lite/README.md)

## 许可

代码以 [MIT](LICENSE) 协议开源。仅供个人学习与研究使用，请遵守微信等软件的许可协议与当地法律法规，作者不对使用后果负责。
