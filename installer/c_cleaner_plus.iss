#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "C Cleaner Plus"
#define AppDisplayName "C盘强力清理工具"
#define AppPublisher "Kiowx"
#define AppURL "https://github.com/Kiowx/c_cleaner_plus"
#define AppExeName "c_cleaner_plus.exe"

[Setup]
AppId={{ACF190D8-8DC8-4EC6-9F6E-DB4FD898F296}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} v{#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={autopf}\C Cleaner Plus
DefaultGroupName=C Cleaner Plus
DisableProgramGroupPage=yes
AllowNoIcons=yes
OutputDir=..\dist
OutputBaseFilename=c_cleaner_plus-v{#AppVersion}-windows-x64-setup
SetupIconFile=..\app.ico
UninstallDisplayIcon={app}\{#AppExeName}
LicenseFile=..\LICENSE
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
CloseApplications=yes
RestartApplications=no
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\c_cleaner_plus\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppDisplayName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppDisplayName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; Keep Setup's elevated token because the application manifest requires administrator access.
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppDisplayName, '&', '&&')}}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ""Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {{ $_.TaskName -like 'C盘强力清理工具*' } | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveScheduledTasks"

[UninstallDelete]
Type: files; Name: "{app}\cdisk_cleaner_bootstrap.json"

[Code]
procedure EnsureBootstrapConfig;
var
  LocatorPath: string;
  Payload: string;
begin
  LocatorPath := ExpandConstant('{app}\cdisk_cleaner_bootstrap.json');
  if FileExists(LocatorPath) then
    Exit;

  Payload := '{"config_dir":"%LOCALAPPDATA%\\C Cleaner Plus\\configs","skip_legacy_migration":false,"legacy_migration_acknowledged":false}';
  if not SaveStringToFile(LocatorPath, Payload, False) then
    Log('Unable to create runtime configuration locator: ' + LocatorPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    EnsureBootstrapConfig;
end;
