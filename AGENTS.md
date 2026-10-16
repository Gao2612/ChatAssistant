# Agent 项目入口

本仓库当前主体为 Android 聊天助手；新增 Windows 产品方向的权威需求入口如下：
- [Windows 微信详细方案](docs/windows/WINDOWS_WECHAT_PRODUCT_PLAN.md)
- [Windows Agent 交接](docs/windows/AGENT_HANDOFF.md)
- [微信嵌入实机结果](docs/windows/WECHAT_EMBED_TEST_RESULTS_2026-10-14.md)
- [临时探针源码](windows/probes/wechat-embed/README.md)
- [Windows 工程入口](windows/README.md)
- [crush 源码参考与模块接入](windows/reference/crush-monitor/README.md)

任何涉及 Windows、微信嵌入、多开、实时回复、ExportWeChat HTML、客户画像、关系趋势、聊天统计、exe 安装包的任务，应先阅读上述两份文档。

## 工作边界
- 正式 Windows 产品未实现；临时探针已完成部分验证并发现单击/滚动故障。不得把父级关系或模拟自检成功写成完整兼容。
- Windows 工程建议独立放在 windows/，保留 Android app/。
- Windows 新基础为本地增强版 crush 源码参考包，保留 MIT 与逐文件来源。
  reference/ 中的源码未接入正式产品，不能把本地新增模块称为上游已有能力。
  迁入前先改应用身份与数据目录，避免接触原 crush 的配置与聊天数据。
- 根 CLAUDE.md 中 Android 平台规则适用于原 Android 工程，不应误作 Windows 固定路径或依赖要求。
- 用户于 2026-10-14 明确授权编写并上传本次方案；这次文档提交符合用户指令，覆盖 CLAUDE.md 中通用的禁止提交/推送规则。
- 同日用户另行授权上传临时测试系统源码和结果；本次提交不包含真实聊天截图和运行日志。
- 后续提交与发布遵循各自任务的用户授权，不从本次文档授权推导产品部署授权。
- 不自动发送聊天、不修改官方微信、不直接读取微信数据库；实时采集与历史导入是独立通道。
- 保持账号/会话隔离，不把联系人名称作为稳定身份。
- 不提交真实聊天、附件、密钥和个人资料；测试使用合成或脱敏数据。
- 报告事实、推断和数据缺口分开，保留原始消息证据与实际覆盖范围。
- 开始代码修改前确认所需子目录规则；交付说明真实验证和未测项。

