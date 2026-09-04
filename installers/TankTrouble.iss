; ==================================================================
; 坦克动荡 (TankTrouble) - Inno Setup 安装脚本
; ==================================================================
; 编译命令：
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installers\TankTrouble.iss
; 依赖：先运行 PyInstaller 生成 dist\TankTrouble\ 目录
; ==================================================================

#define MyAppName      "坦克动荡"
#define MyAppNameEn    "TankTrouble"
#define MyAppVersion   "1.0.11"
#define MyAppPublisher "TankTrouble Team"
#define MyAppExeName   "TankTrouble.exe"
#define MyAppIcon      "..\app\assets\icon\app_icon.ico"
#define MyAppOutputDir "..\installers\Output"
#define MyAppSourceDir "..\dist\TankTrouble"

; ------------------------------------------------------------------
[Setup]
; 基本信息
AppId={{B8C3A1D2-4E5F-6789-ABCD-EF0123456789}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppNameEn}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir={#MyAppOutputDir}
OutputBaseFilename={#MyAppNameEn}-{#MyAppVersion}-Setup
Compression=lzma2/ultra64
SolidCompression=yes

; 安装器外观
SetupIconFile={#MyAppIcon}
WizardStyle=modern
DisableProgramGroupPage=yes

; 管理员权限（程序文件目录需要）
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline dialog

; ------------------------------------------------------------------
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

; ------------------------------------------------------------------
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked
Name: "startupicon"; Description: "开机自动启动";    GroupDescription: "附加图标:"; Flags: unchecked

; ------------------------------------------------------------------
[Files]
; 整个 PyInstaller onedir 产物拷到安装目录
Source: "{#MyAppSourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

; ------------------------------------------------------------------
[Icons]
Name: "{group}\{#MyAppName}";             Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}";        Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";       Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}";       Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

; ------------------------------------------------------------------
[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
