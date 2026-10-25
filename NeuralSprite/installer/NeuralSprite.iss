#define MyAppName "Neural Sprite"
#define MyAppVersion "0.29"
#define MyAppPublisher "Neural Sprite"
#define MyAppExeName "Neural Sprite.exe"

[Setup]
AppId={{D0A9B8F1-71B7-4F67-91F2-028028028028}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Neural Sprite
DefaultGroupName=Neural Sprite
OutputDir=..\installer_output
OutputBaseFilename=NeuralSprite_Setup_v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "..\dist\Neural Sprite\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Neural Sprite"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Neural Sprite"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Neural Sprite"; Flags: nowait postinstall skipifsilent
