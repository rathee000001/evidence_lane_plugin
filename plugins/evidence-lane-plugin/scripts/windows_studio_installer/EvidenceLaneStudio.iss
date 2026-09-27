#define MyAppName "Evidence Lane Studio"
#define MyAppVersion "4.0.10"
#define MyAppPublisher "Evidence Lane"
#define MyAppURL "https://evidencelane.org"
#define MyAppExeName "EvidenceLaneStudio.exe"
#define MyAppUserModelId "EvidenceLane.Studio"

[Setup]
AppId=EvidenceLane.Studio
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
UninstallDisplayName={#MyAppName}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/docs/troubleshooting
AppUpdatesURL={#MyAppURL}/docs/releases
AppComments=Read-only Windows observer for the Evidence Lane local engine.
DefaultDirName={localappdata}\Programs\Evidence Lane Studio
DefaultGroupName=Evidence Lane Studio
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=.
OutputBaseFilename=EvidenceLaneStudioSetup
SetupIconFile=EvidenceLaneStudio.ico
UninstallDisplayIcon={app}\EvidenceLaneStudio.ico
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=force
RestartApplications=no
ChangesAssociations=no
VersionInfoVersion=4.0.10.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Files]
Source: "EvidenceLaneStudioShell.exe"; DestDir: "{app}"; DestName: "{#MyAppExeName}"; Flags: ignoreversion
Source: "EvidenceLaneStudio.ico"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
Type: files; Name: "{userprograms}\{#MyAppName}.lnk"
Type: files; Name: "{userdesktop}\{#MyAppName}.lnk"

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--open"; WorkingDir: "{code:GetRuntimeEngineRoot}"; IconFilename: "{app}\EvidenceLaneStudio.ico"; AppUserModelID: "{#MyAppUserModelId}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--open"; WorkingDir: "{code:GetRuntimeEngineRoot}"; IconFilename: "{app}\EvidenceLaneStudio.ico"; AppUserModelID: "{#MyAppUserModelId}"

[Registry]
Root: HKCU; Subkey: "Software\Evidence Lane\Studio"; ValueType: string; ValueName: "RuntimeRoot"; ValueData: "{code:GetRuntimeRoot}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\EvidenceLaneStudio.exe"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\EvidenceLaneStudio.exe"; ValueType: string; ValueName: "Path"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\EvidenceLaneStudio.exe"; ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\EvidenceLaneStudio.exe"; ValueType: string; ValueName: "AppUserModelId"; ValueData: "{#MyAppUserModelId}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\EvidenceLaneStudio.exe"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: "{app}\EvidenceLaneStudio.ico"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\EvidenceLaneStudio.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" --open"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\EvidenceLaneStudio.exe\Capabilities"; ValueType: string; ValueName: "ApplicationName"; ValueData: "{#MyAppName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\EvidenceLaneStudio.exe\Capabilities"; ValueType: string; ValueName: "ApplicationDescription"; ValueData: "Read-only Windows observer for Evidence Lane projects."; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\Applications\EvidenceLaneStudio.exe\Capabilities"; ValueType: string; ValueName: "ApplicationIcon"; ValueData: "{app}\EvidenceLaneStudio.ico"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\RegisteredApplications"; ValueType: string; ValueName: "Evidence Lane Studio"; ValueData: "Software\Classes\Applications\EvidenceLaneStudio.exe\Capabilities"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--inspect"; StatusMsg: "Verifying Evidence Lane Studio…"; Flags: runhidden waituntilterminated

[Code]
function GetRuntimeRoot(Param: String): String;
begin
  Result := ExpandConstant('{param:RUNTIMEROOT|C:\Apps\EvidenceLaneStudio}');
end;

function GetRuntimeEngineRoot(Param: String): String;
begin
  Result := AddBackslash(GetRuntimeRoot('')) + 'engine';
end;
