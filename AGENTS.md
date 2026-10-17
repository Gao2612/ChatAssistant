# Agent 项目入口

本仓库当前主体为 Android 聊天助手；新增 Windows 产品方向的权威需求入口如下：
- [Windows 完整版设计（2026-10-17）](docs/windows/WINDOWS_LITE_COMPLETION_DESIGN_2026-10-17.md)
- [Windows 完整版分任务实施计划](docs/windows/WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md)
- [Windows 微信详细方案](docs/windows/WINDOWS_WECHAT_PRODUCT_PLAN.md)
- [Windows Agent 交接](docs/windows/AGENT_HANDOFF.md)
- [Windows Lite 0.2 本机验证](docs/windows/WINDOWS_LITE_VERIFICATION_2026-10-17.md)
- [读取/分析双后端验收清单（2026-10-18）](docs/windows/WINDOWS_LITE_ACCEPTANCE_CHECKLIST_2026-10-18.md)
- [读取/分析双后端整改报告（2026-10-18，本机 `_reports/`）](_reports/backend_read_analysis_split_report.md)
- [微信嵌入实机结果](docs/windows/WECHAT_EMBED_TEST_RESULTS_2026-10-14.md)
- [临时探针源码](windows/probes/wechat-embed/README.md)

任何涉及 Windows、微信嵌入、多开、实时回复、ExportWeChat HTML、客户画像、关系趋势、聊天统计、exe 安装包、读取/分析后端选择的任务，应先阅读完整版设计、分任务实施计划、0.2 本机验证与 2026-10-18 验收清单。设计中的待实施能力不得写成已交付。

2026-10-18 状态：用户点1（外部 API 厂商/地址/Key、读取模型列表、当前生效后端/模型显示、保存入口）与点4（ChatGPT 授权与模型记忆持久化、手动断开）已落码（T0a/T0b）；T1 契约与单实例、T5 历史数据 V2/迁移已完成；T2 账号服务后端已完成（UI 多账号集成待 Lead）；T11 构建追溯工具已落码（main.py 接线待 T4）；T3/T4/T6 进行中。真实进度以 [WINDOWS_LITE_IMPLEMENTATION_PROGRESS.md](docs/windows/WINDOWS_LITE_IMPLEMENTATION_PROGRESS.md) 为准。

## 工作边界
- `windows/lite/` 已有 Windows 0.2 本地实现和测试安装包；旧临时 `SetParent` 探针发现单击/滚动故障。新版顶层窗口停靠已验证鼠标命中、画面和 OCR，但单击/滚动、输入法、扫码及弹窗尚未逐项验收，不得写成完整兼容。
- Windows 工程建议独立放在 windows/，保留 Android app/。
- 根 CLAUDE.md 中 Android 平台规则适用于原 Android 工程，不应误作 Windows 固定路径或依赖要求。
- 用户于 2026-10-14 明确授权编写并上传本次方案；这次文档提交符合用户指令，覆盖 CLAUDE.md 中通用的禁止提交/推送规则。
- 同日用户另行授权上传临时测试系统源码和结果；本次提交不包含真实聊天截图和运行日志。
- 后续提交与发布遵循各自任务的用户授权，不从本次文档授权推导产品部署授权。
- 不自动发送聊天、不修改官方微信、不直接读取微信数据库；实时采集与历史导入是独立通道。
- 保持账号/会话隔离，不把联系人名称作为稳定身份。
- 不提交真实聊天、附件、密钥和个人资料；测试使用合成或脱敏数据。
- 报告事实、推断和数据缺口分开，保留原始消息证据与实际覆盖范围。
- 开始代码修改前确认所需子目录规则；交付说明真实验证和未测项。

