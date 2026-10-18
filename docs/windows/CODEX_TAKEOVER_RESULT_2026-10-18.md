# Codex 额度假手接管收尾（2026-10-18 11:00）

本文件是 C1–C5 临时接管结果与交还 Codex 的索引。详细证据见各 `CODEX_TAKEOVER_C*.md`。

## 结果

| 任务 | DeepSeek 接管结果 | Codex 面板 | 证据 |
| --- | --- | --- | --- |
| C1 App 内自动查找按钮路径 | **完成（带限定）**：隔离 0.5.8 source tree，UIA ValuePattern + InvokePattern 触发按钮 → find_started/switched=true 5.2s，无发送，窗口 visible/rect 同 run 不变；native 鼠标路径与 A/B 差分未完成，列为测试器限制 | completed 100（带限定） | `CODEX_TAKEOVER_C1_UI_FIND.md` |
| C2 0.4.x→0.5.8 升级/卸载/保留数据 | **完成（隔离验证）**：26/26 步 ok，安装/覆盖升级/默认卸载保留数据/重装恢复；真实 profile 未动；V2→V2 如实；test-only AppId/历史重建/非干净 VM 差距保留 | completed 100（带差距） | `CODEX_TAKEOVER_C2_UPGRADE.md` |
| C3 真实窗口停靠/弹窗/最小化/托盘恢复 | **部分完成**：停靠 attach/detach、最小化、托盘恢复自动通过；真实弹窗无法自动判定（微信 4.x 菜单非顶层窗口）→ 人工；dock 窗口过小提示 F1、托盘恢复竞争 F3 为产品建议 | in_progress 85（待人工签收） | `CODEX_TAKEOVER_C3_WINDOWS.md` |
| C4 OCR 名字误识 | **完成**：搜索下拉层屏幕抓取 + 只点精确结果行 + 头部三路 exact + 无 Enter；真实窗口郜亚禹 ok=true 22.5s、田子夜 ok=true 13.1s；Lead 加 visible/foreground 守卫与回归 | completed 100 | `CODEX_TAKEOVER_C4_OCR.md` |
| C5 T9/T10 合成验收 | **完成（合成）**：fake 模型服务 + 双源仿真 17 项 OK；真实模型/双账号仍待用户 | completed 100（注明合成） | `CODEX_TAKEOVER_C5_SYNTHETIC.md` |

## 提交

- Codex 未提交的 0.5.8 基线：`a3b2994`
- C4：`54436b6` + `48f30fb`
- C2：`28c03a7` + `d87253a` + `44abae2`
- C1：见本轮 C1 commit；C3：见本轮 C3 commit
- 全量测试：C1/C3 提交前 **750 tests OK / 37 skipped / exit 0**（`test-output/lead_full_after_c1c2c4c5.log`）

## 需 Codex/用户后续

1. C1 native 鼠标路径、真实 A/B 差分、C3 真实弹窗/托盘按钮/停靠视觉、A4 发送确认、真实模型额度/双微信账号仍需人工/Codex 复验。
2. C3 的产品建议：dock attach 失败提示改为“请放大助手窗口”；托盘离屏抓帧与“启动微信”恢复互斥/重试。
3. 工作区提交后需 clean rebuild 0.5.9 并覆盖安装，再交用户签收。
4. 真实聊天内容/截图未落盘，未发送任何消息；C2 的 test installer 曾删除快捷方式，现已重建。

## 交还 Codex（14:51 后）

Codex 恢复后先看 `CODEX_TAKEOVER_2026-10-18.md`、各 C* 证据与看板 `TAKE-C*`，只复核/补漏；C3 与 manual 项由用户/Codex 复签。不要重复已完成的 C1/C2/C4/C5 实现。
## 后续（2026-10-18 12:15）：悬浮窗与用户反馈修复

- 0.5.12 已安装（SHA `4C63636F1130AB128CE5DA15010C39BADCBA106125EB35A2285B8C35E724A7BB`）：悬浮窗、预览适应/居中、AI 面板最大化可见、托盘隐藏 GDI 修复、模型 id 规范。
- 新增 Codex 14:51 后任务：C6 悬浮窗真实化/不确定行为、C7 托盘隐藏读取可靠性、C8 布局与旧模型显示名迁移验证。
- 全量 753 OK/37 skipped；剩余用户/Codex 人工项见未完成清单。
## 2026-10-18 13:30：好感度/固定联系人/专属档案

- 0.5.15（SHA `AD7F3DDF…C92375DA`）已安装：悬浮球好感度环+6 维本地估算、固定联系人自动分析门控、DPAPI 联系人档案、悬浮球视觉优化；全量 799 OK。
- 参考仓库：https://github.com/FerryCorleone/crush-monitor （6 维 affinity：主动延续/回应投入/关心体贴/自我开放/亲密表达/实际行动；边界封顶 25）。
- Codex 14:51 后：C9 好感度模型化校准、C10 HTML/PDF 全量档案/同名匹配、C11 悬浮球 UX 复核。
- 事故：合成配置覆盖真实 settings，DeepSeek API Key 丢失，已恢复路径/模型/账号，待用户重填 Key。