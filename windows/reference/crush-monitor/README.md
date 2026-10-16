# crush-monitor 源码参考包

## 来源与定位

本参考包用于 ChatAssistant Windows 开发，包含完整文件，
不是摘录片段，也没有接入 ChatAssistant 的正式运行流程。

- 上游：[FerryCorleone/crush-monitor](https://github.com/FerryCorleone/crush-monitor)。
- 基础提交：`f5dd2f2bc1e4600e43534f4c58ffddfedfd81d23`。
- 快照日期：2026-10-16。
- 复制对象：用户本地增强工作区，包含未提交修改和新增模块。
- 授权：[原始 MIT 许可](source/LICENSE)，保留原作者版权声明。

`source-manifest.json` 记录每个文件的 SHA256、字节数及来源状态。
参考目录的 `.gitattributes` 禁止自动转换源码换行，保持 Git 中的原始字节。
`tracked-clean` 表示与基础提交一致；`tracked-modified` 表示本地改动；
`local-added` 表示本地新增，不代表 GitHub 上游已经提供。
源码和原说明保持复制时的原文，部分旧说明可能滞后于增强代码。

只复制源码、原有测试、依赖锁文件、构建配置、示例配置和打包图标。
真实密钥、聊天记录、运行日志、截图、已发布 exe 和 node_modules 不在快照中。
`source/` 是来源快照；迁入后的修改应放到正式模块中并标明来源。

## 模块与接入位置

下表目标目录是正式工程的建议位置，当前尚未创建对应产品模块。

| 能力 | 已纳入源码 | 建议接入位置与必要适配 |
| --- | --- | --- |
| Electron 桌面壳 | `scripts/electron-main.ts`、`electron-preload.ts` | `windows/desktop/`；修改应用身份、数据目录、IPC 与窗口布局 |
| 桌面打包 | `scripts/package-win.mjs`、依赖配置与图标 | `windows/packaging/`；替换品牌与应用 ID，补安装、升级和卸载 |
| 聊天界面 | `src/App.tsx`、`style.css`、`main.tsx` | `windows/desktop/renderer/`；拆分实时工作台与历史任务页 |
| 文件导入 | `src/file-import.ts`、`shared/parser.ts` | `windows/importers/`；按导出版本添加 ExportWeChat 适配及预览 |
| 附件和头像 | `scripts/companion-image.ts`、preload 与 IPC | `windows/importers/`；保留路径与来源校验，扩展附件索引 |
| 消息与角色模型 | `shared/types.ts`、`participants.ts` | `windows/domain/`；增加账号、会话、发言人和来源标识 |
| 分批与历史上下文 | `shared/incremental.ts`、`memory.ts` | `windows/tasks/`；加入任务快照、检查点及持久化恢复 |
| 分析调度与进度 | `src/useAnalysis.ts`、`server/analysis.ts` | `windows/analysis/`；分离任务执行、界面状态与分析模板 |
| 模型与配置 | `server/provider*.ts`、`runtime-config.ts` | `windows/analysis/`；保留统一结果校验，独立密钥存储 |
| Codex 接入 | `server/codex-*.ts` | 可选模型连接器；需本机 CLI 与登录，不承诺账号额度 |
| 本地保存 | `src/storage.ts` | `windows/storage/`；借鉴迁移与写入顺序，改为多会话数据层 |
| 报告导出 | `src/export-report.ts`、桌面 PDF IPC | `windows/reports/`；补来源、覆盖范围、证据和通用分析模板 |
| 原有验证 | `tests/` | 保留解析、存储、协议等基准，新增能力按真实接口验证 |

## 已发现的适配边界

1. 源码仍以两人关系分析为业务模型，恋爱标签、好感分和默认提示词
   不能直接用作客户画像或商业跟进规则。
2. IndexedDB 使用固定数据库和 `workspace/current` 单会话槽位。
   它不是账号隔离、多会话库或任务数据库。
3. 配置目录为 `crush-monitor`，桌面桥为 `crushDesktop`，
   打包身份为 `com.crush.monitor`。直接运行可能接触现有 crush 配置。
   正式版须先改为独立应用身份，再设计显式的数据导入。
4. 追加分析会在内存中生成分批工作；不等于已实现持久任务队列。
   `revision` 与消息 ID 可借鉴，但必须增加任务 ID、检查点和证据索引。
5. 通用 HTML 清理与部分结构化导入不能证明兼容任意 ExportWeChat 版本。
   发送人、时间精度、群聊及附件须用脱敏样本验证。
6. 实时微信采集、账号绑定、窗口托管与回复回填均未由本包实现。
   源码中的微信风格界面是自绘界面，不是官方微信窗口。
7. 模型输出的概率和把握度须保留供应商语义差异。
   本地保存不意味着离线推理；需要分析的原文会发往所选模型。
8. 路由、密钥保存和 Electron IPC 应在产品接入时重新核对权限边界。
   本次源码保留不构成完整安全审计或依赖漏洞修复。

## 迁入顺序

1. 建立独立 Electron 应用身份、数据目录与产品名称。
2. 定义账号、会话、参与者、消息来源及导入批次。
3. 迁入文件解析，增加预览和身份确认，再写入正式消息库。
4. 迁入增量分析与模型连接器，补任务持久化与停止、恢复机制。
5. 迁入报告生成，分开本地统计、人工事实和模型推断。
6. 在独立模块接入实时采集与建议流，最终由用户发送。

窗口嵌入实验目前暂停；历史导入和分析可以独立推进。
正式产品目标与验收见 [Windows 方案](../../../docs/windows/WINDOWS_WECHAT_PRODUCT_PLAN.md)。

## 源码检查

在 `source/` 中使用 Node.js 22.12+ 与项目依赖。
复制时保留 `package-lock.json`，不使用全局依赖。

```powershell
npm ci --ignore-scripts --no-audit --no-fund
npm run build
```

安装命令用于源码检查，跳过依赖安装脚本，不下载 Electron 运行时；
因此不能据此认定已完成桌面启动或 exe 打包。
启动真实程序前先完成应用身份和用户数据隔离。

2026-10-16 实际检查结果：

- 69 个快照文件的 SHA256 与复制来源一致，构建后再次核对一致。
- 来源分布：上游未改动 19 个、本地修改 23 个、本地新增 27 个。
- 锁文件安装成功；只在参考包目录安装依赖。
- `npm run build` 通过 TypeScript 检查与 Vite 构建。
- 原有 7 个测试文件共 50 项通过，失败、跳过均为 0。
- 覆盖聊天格式、文件导入、分批、存储迁移、报告、身份资料和附件边界。
- 本次未调用模型 API、运行真实微信、启动桌面窗口或生成安装包。

对应测试命令（PowerShell）：

```powershell
$crushChecks = @(
  'tests/chat-formats.test.ts'
  'tests/file-import.test.ts'
  'tests/incremental.test.ts'
  'tests/storage.test.ts'
  'tests/export-report.test.ts'
  'tests/participants.test.ts'
  'tests/companion-image.test.ts'
)
node --import tsx --test @crushChecks
```
