# ChatAssistant Windows 工程入口

目标是 Windows 聊天工作台与历史分析任务。
当前已整理可复用源码参考包，正式 Windows 产品尚未接通。

## 阅读入口

1. [产品范围与数据边界](../docs/windows/WINDOWS_WECHAT_PRODUCT_PLAN.md)
2. [crush 源码、来源和迁入映射](reference/crush-monitor/README.md)
3. [Agent 交接](../docs/windows/AGENT_HANDOFF.md)

## 当前目录

| 目录 | 用途与状态 |
| --- | --- |
| `reference/crush-monitor/source/` | 本地增强版源码快照，保留原目录关系 |
| `reference/crush-monitor/source-manifest.json` | 每个文件的 SHA256 与来源状态 |
| `probes/wechat-embed/` | 已出现白屏和恢复故障的历史实验，暂停实机运行 |

源码参考包包含桌面壳、界面、文件解析、增量分析、模型接入、
持久化、报告和原有测试。它仍使用 crush 的名称、配置路径与数据模型。
接入正式工程前必须完成应用身份、数据目录与多账号数据模型的适配。
Electron 验证壳和便携 exe 不等于最终安装器已经实现。

模块迁入顺序与未实现能力以源码参考包说明为准。
