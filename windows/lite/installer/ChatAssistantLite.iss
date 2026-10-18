#define AppName "ChatAssistant Lite"
#define AppVersion "0.5.28"
#define AppExeName "ChatAssistantLite.exe"

[Languages]
Name: "chinesesimp"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Setup]
AppId={{AC7EB68E-5B66-4EC9-BDAE-8031008DB76A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ChatAssistant
DefaultDirName={localappdata}\Programs\ChatAssistantLite
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
SetupIconFile=..\assets\ChatAssistant.ico
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
#if Defined(VerificationBuild)
Uninstallable=no
OutputDir=..\test-output
OutputBaseFilename=ChatAssistantLite-Verification
#else
Uninstallable=yes
OutputDir=..\dist-release
OutputBaseFilename=ChatAssistantLite-Setup
#endif
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
Source: "..\dist-release\ChatAssistantLite\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

#if !Defined(VerificationBuild)
[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon
#endif

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: postinstall nowait skipifsilent
