; Inno Setup 简体中文语言文件（项目内置，覆盖安装/卸载向导主要文字）
; 未列出的条目会自动回退到 Inno Setup 的 Default.isl。
[LangOptions]
LanguageName=简体中文
LanguageID=$0804
LanguageCodePage=0
DialogFontName=Microsoft YaHei UI
DialogFontSize=9
WelcomeFontName=Microsoft YaHei UI
WelcomeFontSize=14

[Messages]
; 应用标题
SetupAppTitle=安装程序
SetupWindowTitle=安装 - %1
UninstallAppTitle=卸载程序
UninstallAppFullTitle=%1 卸载

; 通用
InformationTitle=信息
ConfirmTitle=确认
ErrorTitle=错误

; 启动
SetupLdrStartupMessage=将要安装 %1。是否继续？
LdrCannotCreateTemp=无法创建临时文件，安装已中止。
LdrCannotExecTemp=无法在临时目录中运行文件，安装已中止。
SetupFileMissing=安装目录缺少文件 %1。请修正问题或重新获取安装程序。
SetupFileCorrupt=安装文件已损坏，请重新获取安装程序。
SetupAlreadyRunning=安装程序已在运行。
WindowsVersionNotSupported=本程序不支持当前 Windows 版本。
AdminPrivilegesRequired=安装本程序需要以管理员身份登录。
PowerUserPrivilegesRequired=安装本程序需要管理员或 Power Users 组成员身份。
SetupAppRunningError=检测到 %1 正在运行。%n%n请先关闭它的所有窗口，然后点击“确定”继续，或点击“取消”退出。
UninstallAppRunningError=检测到 %1 正在运行。%n%n请先关闭它的所有窗口，然后点击“确定”继续，或点击“取消”退出。

; 退出确认
ExitSetupTitle=退出安装
ExitSetupMessage=安装尚未完成。现在退出将不会安装程序。%n%n你可以稍后再次运行安装程序完成安装。%n%n确定退出安装吗？
ConfirmUninstall=确定要完全卸载 %1 及其所有组件吗？

; 按钮
ButtonBack=< 上一步(&B)
ButtonNext=下一步(&N) >
ButtonInstall=安装(&I)
ButtonOK=确定
ButtonCancel=取消
ButtonYes=是(&Y)
ButtonYesToAll=全部是(&A)
ButtonNo=否(&N)
ButtonNoToAll=全部否(&O)
ButtonFinish=完成(&F)
ButtonBrowse=浏览(&B)...
ButtonWizardBrowse=浏览(&R)...
ButtonNewFolder=新建文件夹(&M)
ButtonStopDownload=停止下载(&S)
ButtonStopExtraction=停止解压(&S)

; 欢迎/目录/任务
ClickNext=点击“下一步”继续，或点击“取消”退出安装。
WelcomeLabel1=欢迎使用 [name] 安装向导
WelcomeLabel2=这将在你的电脑上安装 [name/ver]。%n%n建议在继续之前关闭其他应用程序。
WizardSelectDir=选择安装位置
SelectDirDesc=[name] 应该安装到哪里？
SelectDirLabel3=安装程序将把 [name] 安装到以下文件夹。
SelectDirBrowseLabel=点击“下一步”继续；如需选择其他文件夹，请点击“浏览”。
CannotInstallToNetworkDrive=无法安装到网络驱动器。
CannotInstallToUNCPath=无法安装到 UNC 路径。
DirExistsTitle=文件夹已存在
DirExists=文件夹：%n%n%1%n%n已存在。是否仍要安装到该文件夹？
DirDoesntExistTitle=文件夹不存在
DirDoesntExist=文件夹：%n%n%1%n%n不存在。是否创建该文件夹？
SelectTasksDesc=需要执行哪些附加任务？
SelectTasksLabel2=选择安装 [name] 时需要执行的附加任务，然后点击“下一步”。
WizardSelectProgramGroup=选择开始菜单文件夹
NoProgramGroupCheck2=不创建开始菜单文件夹(&D)

; 安装/完成
WizardReady=准备安装
ReadyLabel1=安装程序已准备好在此电脑上安装 [name]。
ReadyLabel2a=点击“安装”开始安装；如需检查或修改设置，请点击“上一步”。
ReadyLabel2b=点击“安装”开始安装。
WizardPreparing=正在准备安装
PreparingDesc=安装程序正在准备在电脑上安装 [name]。
WizardInstalling=正在安装
InstallingLabel=正在安装 [name]，请稍候。
FinishedHeadingLabel=[name] 安装向导已完成
FinishedLabelNoIcons=[name] 已安装完成。
FinishedLabel=[name] 已安装完成。你可以通过已创建的快捷方式启动应用。
ClickFinish=点击“完成”退出安装程序。
FinishedRestartLabel=要完成 [name] 的安装，需要重新启动电脑。是否立即重启？
FinishedRestartMessage=要完成 [name] 的安装，需要重新启动电脑。%n%n是否立即重启？
YesRadio=是，立即重启电脑(&Y)
NoRadio=否，稍后自行重启(&N)

; 卸载
WizardUninstalling=卸载状态
UninstallStatusLabel=正在从电脑中移除 %1，请稍候。
ConfirmDeleteSharedFileTitle=删除共享文件？
ConfirmDeleteSharedFile2=系统显示以下共享文件已不再被任何程序使用。是否让卸载程序删除该共享文件？%n%n如果有程序仍在使用该文件，删除后可能导致那些程序无法正常工作。如果不确定，请选择“否”；保留该文件不会造成危害。

[CustomMessages]
AutoStartProgram=自动启动 %1
AddonHostProgramNotFound=未在所选文件夹中找到 %1。仍要继续吗？
