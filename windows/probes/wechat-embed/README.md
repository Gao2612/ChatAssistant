# 微信窗口嵌入可行性探针

状态：临时验证程序，交互兼容性出现故障，不能用于日常聊天或视作完成的桌面产品。

结果与证据见 [2026-10-14 验证记录](../../../docs/windows/WECHAT_EMBED_TEST_RESULTS_2026-10-14.md)。

## 文件

- WeChatProbe.cs：第二版独立 WinForms 原型的实际源码。
- build.ps1：可从任意路径编译，不依赖原测试机器工作目录。
- legacy-probe-v1.ps1：最初 PowerShell 原型，用于追溯；不是建议运行的版本。

## 编译与运行

Windows 上需要 .NET Framework 和 Windows PowerShell 5.1；通过 powershell.exe 编译，PowerShell 7 的 Add-Type 不支持 WindowsApplication 输出。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build.ps1
.\WeChatProbe-v2.exe
```

此处 Bypass 仅用于这一脚本进程，不改变永久系统策略。编译脚本拒绝覆盖已有 exe；重新编译前先关闭并保留/重命名旧二进制。

原型默认路径为 C:\Program Files\Tencent\Weixin\Weixin.exe，可在路径框编辑或浏览选择。路径仅保存到本地 wechat-path.txt，不提交仓库。
它提供启动、刷新、手动嵌入、恢复独立窗口和侧栏收起。自动跟踪登录窗口与聊天窗口尚未实现。

## 同进程模拟自检

```powershell
$probeTest = Start-Process -FilePath .\WeChatProbe-v2.exe -ArgumentList '--selftest' -Wait -PassThru
$probeTest.ExitCode
Get-Content .\selftest.txt
Get-Content .\geometry-v2.txt
```

自检会短暂创建模拟窗口，核验尺寸贴合、侧栏不重叠、父级和样式恢复。它不连接微信，不能证明微信的输入法、滚动、切换会话或跨进程托管兼容性。

## 真实微信验证边界

先保存草稿，使用测试账号；关闭原型时会尝试恢复微信原父级、样式和位置，不退出微信进程。
恢复 API 成功不保证微信内部焦点/激活状态恢复；本次已经出现单击会话和滚动无反应，用户反馈从任务栏进入微信后恢复正常。
异常后先停止测试，记录状态，与独立微信对照。不要用强制结束进程替代正常恢复。
本程序不读取数据库、不 OCR、不调用模型、不发送消息；运行日志只包含窗口关系和尺寸，不含聊天正文。
不提交截图、真实会话、账号资料、进程 ID 或本机日志。仓库保存源码和脱敏的尺寸证据，不保存发布 exe。
