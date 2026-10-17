# ChatAssistant Lite（Windows）

用户用官方微信程序登录，助手根据设置中的完整 exe 路径发现窗口，自动连接唯一候选；多个窗口时在设置中选择。中央区域把**未改父级、未改样式的原生微信顶层窗口**对齐到应用开口，以保留微信自己的鼠标、输入法和弹窗处理；停靠失败时显示只读实时画面。微信在聊天页保持未最小化，切到设置/历史页或最小化助手时会随之最小化，返回聊天页自动恢复；退出时恢复微信原位置。旧版 `SetParent` 探针曾出现单击和滚轮失效；新版已实测中央命中微信，但单击、滚轮、中文输入法和弹窗仍需人工验收。

本地 OCR 只读取当前画面中的可见文字。模型准备就绪后，新识别的对话会自动生成右侧可展开的判断和回复建议；建议只供复制，由用户核对并在微信手动发送。右侧展开时应用增加宽度，不压缩中央聊天区。

左侧“历史记录”页可按手动指定的微信账号和联系人导入 HTML/PDF，分页浏览完整导入文字、搜索，并生成离线整理报告。导入文字、文件名和联系人名在本机 SQLite 中经 Windows DPAPI 加密。HTML 作为纯文本解析，不执行脚本或加载远程资源；有密码或无文字层的 PDF 会提示用户。离线报告覆盖全部已导入文字片段，列出原文证据、可识别月份和统计，不把片段数冒称微信原始消息数，也不凭文本推断真实心理。不同账号或同名联系人不会自动合并。

Codex 侧栏里的界面预览在 [`preview/index.html`](preview/index.html)，只使用示例内容；可安装程序以本目录源码为准。

## 运行与打包

需要 Windows 10/11 x64、Python 3.12 和 Inno Setup 6（安装包编译）。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe main.py
.\package.ps1
```

便携程序输出到 `dist-release\ChatAssistantLite`，用户级安装器输出到 `dist-release\ChatAssistantLite-Setup.exe`。打包脚本运行无界面启动测试，失败会停止。

## 本地数据与模型

文字读取（屏幕 → 聊天文字）与分析（聊天文字 → 建议）是两套独立配置，可以分别选择不同服务与模型：

- **读取**：默认本地 OCR（离线，不上传画面）；可选多模态模型（OpenAI 兼容视觉接口）或 Codex 应用（官方 app-server 图像输入）。选择后两者时，只有当前聊天画面会发送给所选服务用于转写；失败自动回退本地 OCR，远程确认后的文字不会被本地 OCR 覆盖。
- **分析**：ChatGPT Plus/Pro 官方 Sign in with ChatGPT（账号模型目录）、兼容 API、本地模型服务（OpenAI 兼容，默认 `http://127.0.0.1:11434/v1`，可不填 Key）或 Codex 应用（官方 app-server，只读沙箱、审批 `never`、拒绝服务端工具请求）。

Codex 后端使用本机已登录的官方 Codex，不读取或复制 Codex 桌面凭据；本软件不会自动读取 Codex 桌面的当前模型或登录令牌。具体可用模型和额度以各服务返回为准。OAuth 凭据、分析与读取 API Key 由 Windows DPAPI 加密到 `%LOCALAPPDATA%\ChatAssistantLite\settings.json`。导入历史在同目录的 `memory.sqlite3`；实时画面与 OCR 快照不持久化、不写日志。只有自动分析准备就绪时，当前可见对话文本才发送到所选分析后端；历史库与离线报告不会自动上传。

官方流程：[Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source)、[账号和用量](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)。

## 当前验证边界

- 单元与本地回环测试覆盖窗口身份、采集状态、授权回调、历史解析、停靠几何恢复、后端路由/读取解析、Codex 协议（假 app-server）和界面门控；它们不能证明真实微信的扫码、多开、单击、滚轮、中文输入法与弹窗都通过。
- 2026-10-18：读取/分析双后端落码，86 项单测通过；真实 Codex app-server 免费 RPC（账号、模型目录）验证通过：账号为 ChatGPT plus，7 个模型均声明支持 `text,image`。未发起真实模型回合以节省额度。
- 2026-10-18：多模态读取、Codex 读取/分析的真实模型回合、本地模型服务均未实测（没有提供视觉接口 Key，本机也没有运行本地模型服务）。
- 2026-10-17 在已登录的官方微信窗口实测：按路径发现 1 个窗口，捕获画面、生成 OCR 快照（11 条可见消息），中央区域命中微信原生窗口；设置页使微信最小化，返回聊天页重新停靠，退出后恢复原位置。测试日志未打印聊天正文。自动建议需要模型授权后验证。
- 2026-10-17 用户完成官方 ChatGPT 网页授权，本机返回 7 个账号模型，并用合成对话完成一次真实模型请求（3 条建议）。仍需在最终安装版 UI 内逐项验收授权重试和令牌过期刷新；程序不会借用 Codex 本地令牌。
- 无卸载项的验证安装包已解包到隔离测试目录，安装后 EXE 使用真实 Windows 图形后端正常启动并退出。正式安装器升级和卸载尚未实测。
- 未提供 ExportWeChat 的具体 HTML 样本；导入器保守保留文字，不保证准确还原每条消息的发送人和时间。PDF 需要可提取文字层。

OCR/定位参考 [jev-chat/jev-chat-windows](https://github.com/jev-chat/jev-chat-windows)，第三方许可随包附带。
