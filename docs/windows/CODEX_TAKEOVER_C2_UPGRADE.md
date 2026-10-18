# Codex 接管 C2 证据：0.4.x → 0.5.8 升级 / 卸载保留数据 / 重装恢复（隔离验证）

- 接管者：DeepSeek `contracts`（Codex 额度空窗期临时接管，2026-10-18）
- 旧版：**ChatAssistant Lite 0.4.3**，git commit `5cafa1a`
- 新版：**ChatAssistant Lite 0.5.8**，git commit `a3b2994`（Lead 审查后的基线；证据跑时 HEAD 为其后代 `48f30fb`，仅文档/其他 C 线改动，未改本验证涉及的版本代码）
- 范围：隔离验证“0.4.3 数据 → 安装 0.5.8 → Key/授权/历史保留 → 默认卸载保留数据 → 重装恢复”
- 结论（**仅限隔离 test-AppId 环境**）：26 个步骤全部通过，`ok=true`；**未**改动仓库 `dist-release/`（Codex 已构建的 0.5.8 包未覆盖/未删除）、**未**触碰真实 profile、**未**在 `E:\ChatAssistantLite` 上安装任何测试包。真实候选包/干净 VM 的最终升级验收仍需 Codex/Lead 用正式包复跑（见 §7 差距）。
- 机器可读结果：`windows/lite/test-output/c2-upgrade-install-result.json`（`ok=true`，generated_at 2026-10-18 10:42:53）
- 证据状态：**已冻结**（2026-10-18 复核期不再重跑安装/卸载；真实安装、注册表与运行进程未再触碰；本报告 §10 为复核问答清单）
- 持久证据副本：`windows/lite/test-output/c2-upgrade-evidence/`（JSON、安装/卸载 Inno 日志、probe 结果、本报告摘要、工件 SHA-256）
- 脚本（新增，未改 liteapp 业务源码）：
  - `windows/lite/tools/c2_upgrade_verify.py` —— 全流程驱动（归档提交→构建→夹具→源级迁移→安装包生命周期→隔离/真实 profile 断言）
  - `windows/lite/tools/c2_profile_probe.py` —— 用指定版本的 `liteapp` 代码写/读合成 profile（只允许显式隔离的 `LOCALAPPDATA`）

## 1. 隔离与安全设计

1. **工作目录独立**：所有归档、构建、安装、profile 都在 `G:\codex\c2-takeover-work\`（仓库外独立临时目录），仓库 `windows/lite/dist-release/` 与 `test-output/` 中的 Codex 0.5.8 构建均未改动。
2. **测试安装包使用独立 AppId/AppName**：从两个 git 归档构建的 portable 目录会被 Inno Setup 编译成 **test-only** 安装包：
   - `AppId={{C2TAKEOVER-...}`（与真实 `{{AC7EB68E-5B66-4EC9-BDAE-8031008DB76A}` 不同）；
   - `AppName="ChatAssistant Lite C2 Test"`，删除 `[Icons]`（不创建/替换真实开始菜单快捷方式）；
   - `CloseApplications=no`（绝不尝试关闭用户正在运行的真实 `ChatAssistantLite.exe`）；
   - 数据目录语义与真实安装包完全相同（`%LOCALAPPDATA%\ChatAssistantLite`，由应用代码决定）。
3. **隔离 LOCALAPPDATA**：每次安装版 app / probe 子进程都显式设置 `LOCALAPPDATA=<work>\isolated-localappdata`；`c2_profile_probe.py` 会强制校验：
   - 必须传入 `--isolated-localappdata`，且与子进程实际 `LOCALAPPDATA` 规范化路径完全一致，否则拒绝运行；
   - 传入真实 `--forbidden-profile`（`C:\Users\...\AppData\Local\ChatAssistantLite`），若解析相同则拒绝；
   - driver 拒绝把 work dir 放在真实 profile 之下。
4. **真实 profile 护栏**：运行前后对真实 `%LOCALAPPDATA%\ChatAssistantLite` 做文件清单/大小/`settings.json`/`memory.sqlite3` SHA-256 快照并断言 `before == after`（结果 JSON 中 `isolation.real_profile_unchanged=true`）。
5. **每个测试安装前清理残留**：删除 HKCU Uninstall 下只含 `C2TAKEOVER` 标记的测试卸载项（**不碰真实 ChatAssistant 键**），避免前次中断的测试安装让 Inno 认为“已安装”而失败。
6. 所有数据均为合成值（settings/账号/聊天/token 均为 `c2-synthetic-*`），不包含真实凭据/聊天内容。

## 2. 工件（git 历史构建，未覆盖仓库 dist-release）

| 工件 | 说明 | 大小 | SHA-256 |
| --- | --- | --- | --- |
| 0.4.3 portable | `git archive 5cafa1a windows/lite` → archived `build.ps1`（PyInstaller 6.22.3，venv）在隔离目录构建，build_info.json 自校验通过、`--version-json`=0.4.3、`--smoke` exit 0 | 347,797,429 B | — |
| 0.5.8 portable | `git archive a3b2994 windows/lite` → 同样构建，`--version-json`=0.5.8、`--smoke` exit 0 | 347,808,454 B | — |
| `C2-old-Setup.exe` | 0.4.3 test-only Inno 安装包（ISCC 6） | 102,735,630 B | `42F805399842737A4D617B9CADAA6ECB98452939E726A1289D780E7996B941B5` |
| `C2-new-Setup.exe` | 0.5.8 test-only Inno 安装包（ISCC 6） | 102,729,189 B | `AF0EAFC4222377B4DC28FCA6FAD352E8FCFFBDE42CC12EFD3BAA3488CAAE101E` |

> 构建命令（可重复）：
> `$env:CHATASSISTANT_PYTHON='G:\codex\wechat-lite-venv\Scripts\python.exe'; $env:LOCALAPPDATA='<work>\build-profile-<ver>'; $env:QT_QPA_PLATFORM='offscreen'; powershell -NoProfile -File <archived-root>\build.ps1`
> 测试安装包由 `c2_upgrade_verify.py` 自动改写归档内的 `installer/ChatAssistantLite.iss`（唯一 test AppId/名称、无图标、CloseApplications=no、隔离 OutputDir）后调用 ISCC 编译。

## 3. 0.4.x 数据夹具（由旧代码生成，不是新代码冒充）

- 由 **0.4.3 代码本身**（归档 commit `5cafa1a`，`c2_profile_probe.py write` 在该版本的 `liteapp` 下运行）创建：
  - `settings.json`：合成 host_id、分析 API Key、读取 Key、wechat_path、backend=api、model、base_url、style；单账号 legacy `subscription`（合成 access/refresh/id token、client_id、subject、email、scopes）与模型目录；旧版 `settings.save` 会按 0.4.3 的账号迁移逻辑持久化 `accounts`。
  - `memory.sqlite3`：0.4.3 `HistoryStore` 创建联系人 + 2 个来源 + 4 条合成记录 + 1 条 derived memory。
- **schema 事实（如实记录）**：0.4.3 的 `history_models.SCHEMA_VERSION` 已经是 **2**，因此升级读取时 `from_version=2, to_version=2, migrated=false, created=false`——这是 V2→V2 的读取兼容，不是 0.2 V1→V2 迁移。0.2/0.3 的 V1→V2 迁移由 T5 迁移测试单独覆盖。
- 写入结果的 `settings` 中 `api_key`/`reading_key` 只记录 SHA-256；不落真实密钥。

## 4. 源级验证结果（0.4.3 写 → 0.4.3/0.5.8 读，隔离 profile）

| 步骤 | 结果 |
| --- | --- |
| 0.4.3 代码写 profile | 通过；写入代码身份 = 0.4.3（归档 package root + APP_VERSION=0.4.3 + schema=2） |
| 0.4.3 读回刚写数据 | 通过，0 mismatches |
| 0.5.8 读 0.4.3 数据 | **通过，0 mismatches**；Key/读取 Key 摘要、host_id、backend/model/base_url/wechat_path/style、ChatGPT subject/client_id/email、模型目录、注册记录全部保留 |
| 0.5.8 重存（模拟升级后首次保存/账号迁移写）再读 | 通过，0 mismatches |
| 历史数据读回 | record_count=4、char_count=113、source_count=2、source_version=2、memory_version=2、derived_memory_count=1、texts_digest=`476b5632…4992c9`、证据文本摘要一致 |
| 静态安装/卸载保留语义（0.4.3 & 0.5.8 `.iss`） | `[UninstallDelete]` 不存在、无 `[Code]` 删除调用；`DefaultDirName={localappdata}\Programs\ChatAssistantLite`、`PrivilegesRequired=lowest`；卸载默认不会删除 `%LOCALAPPDATA%\ChatAssistantLite` |

## 5. 真实安装包生命周期（隔离 test-AppId、隔离 LOCALAPPDATA）

驱动命令（实际执行，日志在 `test-output/c2-upgrade-evidence/logs/`）：

```powershell
cd G:\codex\ChatAssistant\windows\lite
G:\codex\wechat-lite-venv\Scripts\python.exe tools\c2_upgrade_verify.py `
  --old-root G:\codex\c2-takeover-work\old-0.4.3\root `
  --new-root G:\codex\c2-takeover-work\new-0.5.8\root `
  --work-dir G:\codex\c2-takeover-work\verify-install6 --skip-build `
  --old-setup  G:\codex\c2-takeover-work\verify-install6\setups\C2-old-Setup.exe `
  --new-setup  G:\codex\c2-takeover-work\verify-install6\setups\C2-new-Setup.exe `
  --json-out   test-output\c2-upgrade-install-result.json
```

结果（26 步全部 ok，exit=0；括号内为 Inno `/LOG`）：

| 步骤 | exit | 证据/说明 |
| --- | --- | --- |
| 清理残留测试卸载项 | — | 仅删除含 `C2TAKEOVER` 标记的 HKCU 键，真实 ChatAssistant 键保留 |
| 安装 0.4.3 | **0** | `inno-install-old.log` 显示 `Installation process succeeded`；`ChatAssistantLite.exe`+`unins000.exe` 存在 |
| 安装版 0.4.3 `--version-json` | 0 | `app_version=0.4.3` |
| 安装版 0.4.3 `--smoke`（隔离 profile=旧数据） | 0 | 旧版可启动并读取该 profile |
| 升级安装 0.5.8（同一 test AppId，覆盖 `/DIR`） | **0** | `inno-install-upgrade.log` 成功 |
| 升级后 `--version-json` | 0 | `app_version=0.5.8` |
| 升级后 `--smoke` | 0 | 新版可启动 |
| 升级后 `--self-test --data-dir <隔离>` | 0 | 隔离自检通过 |
| 升级后读取保留数据（0.5.8 probe） | 通过 | Key/授权/历史 0 mismatches（同 §4） |
| 默认卸载（`unins000.exe /VERYSILENT`） | **0** | 主 exe 已删除（安装目录仅剩卸载日志类残留文件，exe/应用文件已移除） |
| 卸载后数据目录保留 | 通过 | `settings.json`、`memory.sqlite3` 及 profile 内 4 个文件前后 SHA-256 完全一致；`settings.json`=`f20eaa0b…4e9613b`、`memory.sqlite3`=`ba0e94fd…ac8b3f935` |
| 卸载后仍可读数据（0.5.8 probe） | 通过 | 0 mismatches |
| 重装 0.5.8 | **0** | `inno-install-reinstall.log` 成功 |
| 重装后 `--version-json` / `--smoke` | 0 / 0 | `app_version=0.5.8`；启动成功 |
| 重装后读取保留数据 | 通过 | 0 mismatches（历史记录数/摘要/版本、Key 摘要、ChatGPT 授权全保留） |
| 最终清理测试安装 | 0 | 删除测试 AppId 安装；真实安装 `E:\ChatAssistantLite`（0.5.7，注册表键 `{AC7EB68E…}_is1`）与其 3 个运行进程均未受影响 |
| 真实 profile before==after | 通过 | `C:\Users\16356\AppData\Local\ChatAssistantLite` 文件清单/大小/SHA 前后一致（`real_profile_unchanged=true`） |

## 6. “保留了哪些数据”清单（升级/卸载/重装后核对）

- 设置：`host_id`、分析 API Key（SHA 摘要一致）、读取 API Key 摘要、`wechat_path`、`backend=api`、`model`、`base_url`、`style`、`reading_backend`；
- ChatGPT 授权映射：`accounts` 注册记录（registration_id）、`subject`、`client_id`、`email`、模型目录 `[["C2 Old Chat Model","c2-old-model-1"]]`、`subscription` 视图；
- 历史库：联系人 scope、2 个来源、4 条记录正文与顺序、字符数、source/memory 版本、derived memory（1 条）、证据引用可读且文本摘要一致；
- 数据文件：`settings.json`、`memory.sqlite3` 在卸载前后 SHA-256 一致；目录内共 4 个文件全部保留。

## 7. 与“真实候选包/干净 VM 升级”的差距（必须如实保留）

1. **test-only AppId/名称**：真实升级依赖同一个正式 AppId 的 Inno 覆盖安装；本次为不破坏机器上已安装的 0.5.7/0.5.8 候选，改用不同 test AppId + `/DIR` 隔离目录，因此**真实 AppId 的“升级安装”注册表/uninstaller 替换行为**未被直接执行（`.iss` 的 `[UninstallDelete]`/文件列表/数据目录语义与正式脚本相同，静态核对通过）。
2. **0.4.x 安装包是 git 历史重建**：portable/安装包由 `5cafa1a` 源码用当前 venv（PyInstaller 6.22.3）+ Inno 6 重建，不是当初发布的 0.4.x 二进制；旧数据由同 commit 的旧代码生成，格式/DPAPI/路径与 0.4.3 一致，但未覆盖“用户机器上原 0.4.x exe 首次运行产生的 profile”的字节级差异（例如旧构建的 Qt/字体无关数据不影响 profile）。
3. **非干净 VM、未跨用户**：本机 Windows 用户级安装，未验证干净 Win10/11 VM、标准用户/多用户、漫游 profile、安装包升级中断/回滚、旧程序拒写新库等 T12 场景。
4. **真实用户 profile 不在本证据内**：真实 profile 仅做“未被触碰”断言；用户实际的旧 0.4.x 数据升级需用正式候选包在干净 VM 上由 Codex/Lead 复跑。
5. **history V2→V2 非迁移**：上述 `migrated=false` 是正确事实；V1→V2（0.2/0.3）迁移已有 T5 专项测试，不在本 C2 场景中。
6. **本机环境副作用（已知、可恢复、不影响数据）**：早期一次未加隔离参数的 test installer 试验曾替换/删除本机开始菜单 `ChatAssistant Lite.lnk`（指向 `E:\ChatAssistantLite` 的正式快捷方式）。此后所有测试均改用 test-only AppId/AppName 且删除 `[Icons]`，未再触碰真实快捷方式。当前真实安装目录、卸载注册表键 `{AC7EB68E…}_is1` 和 3 个运行实例均完好；**后续真实 0.5.8/0.5.9 安装会重新创建该开始菜单快捷方式**，无需人工修复，也不涉及任何用户数据。若在正式包安装前需要恢复，可重装/重建候选包。

## 8. 给 Lead/Codex 的后续建议

1. 用正式候选包（Lead clean rebuild 后的 0.5.8 或后续版本）在干净 Win10/11 VM 上执行 T12-01：真实 0.4.x 安装包 → 覆盖升级 0.5.8 → 重启/续期 → 默认卸载保留数据 → 重装恢复；核对本报告 §6 清单。
2. 若需要真实 AppId 升级证据：在 VM 快照中安装旧候选包后再装新候选包（不要在开发机覆盖已安装 0.5.7/0.5.8）。
3. 继续把本报告的“隔离 test-AppId 环境”与“静态 .iss 数据保留语义”记入验收矩阵 S-V01，但**不要写成“真实安装包升级已通过”**；资格表述为“隔离重建包 + 源级数据升级已验证；真实候选包/干净 VM 待复核”。

## 9. 证据文件索引

| 路径 | 内容 |
| --- | --- |
| `windows/lite/test-output/c2-upgrade-install-result.json` | 26 步完整 JSON（ok=true；含版本/commit、isolation、from/to version、每步 exit、inno log 路径、保留文件 SHA、真实 profile 前后快照） |
| `windows/lite/test-output/c2-upgrade-evidence/logs/` | Inno install/upgrade/reinstall/uninstall 日志、smoke/version/self-test 日志、probe 读写结果 |
| `windows/lite/test-output/c2-upgrade-evidence/lifecycle-summary.txt` | 本报告 §5/§6 的机器摘要（`c2_evidence_summary.py` 输出） |
| `windows/lite/test-output/c2-upgrade-evidence/setups-sha256.txt` | 两个 test 安装包 SHA-256 |
| `windows/lite/test-output/c2-upgrade-evidence/isolated-settings.json` / `isolated-memory.sqlite3` | 卸载后保留的合成 profile 实体（无真实数据） |
