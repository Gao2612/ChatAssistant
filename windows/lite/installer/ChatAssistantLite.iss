#define AppName "ChatAssistant Lite"
#define AppVersion "0.3.0"
#define AppExeName "ChatAssistantLite.exe"

[Setup]
AppId={{AC7EB68E-5B66-4EC9-BDAE-8031008DB76A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=ChatAssistant
DefaultDirName={localappdata}\Programs\ChatAssistantLite
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
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
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist-release\ChatAssistantLite\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

#if !Defined(VerificationBuild)
[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
#endif

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: postinstall nowait skipifsilent
