; Windows installer (Inno Setup 6). Built by .github/workflows/build.yml:
;   ISCC.exe /DAppVersion=1.0.0 packaging\installer.iss
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6C2F4E7A-4B1D-4C9A-9E3B-C4D0C4D0C4D0}
AppName=C4D Render Guard
AppVersion={#AppVersion}
AppVerName=C4D Render Guard {#AppVersion}
VersionInfoVersion={#AppVersion}
AppPublisher=C4D Render Guard
DefaultDirName={autopf}\C4D Render Guard
DefaultGroupName=C4D Render Guard
DisableProgramGroupPage=yes
DisableDirPage=yes
; Per-user install: no administrator rights needed.
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=C4DRenderGuard-Setup-{#AppVersion}
SetupIconFile=..\build\icon.ico
UninstallDisplayIcon={app}\C4DRenderGuard.exe
ChangesAssociations=yes
CloseApplications=yes
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: "desktopicon"; Description: "Desktop icon (drop .c4d files on it to render them)"
Name: "contextmenu"; Description: "Add ""Render with C4D Render Guard"" when right-clicking .c4d files"

[Files]
Source: "..\dist\C4DRenderGuard.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\C4D Render Guard"; Filename: "{app}\C4DRenderGuard.exe"
Name: "{autodesktop}\C4D Render Guard"; Filename: "{app}\C4DRenderGuard.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.c4d\shell\C4DRenderGuard"; ValueType: string; ValueName: ""; ValueData: "Render with C4D Render Guard"; Flags: uninsdeletekey; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.c4d\shell\C4DRenderGuard"; ValueType: string; ValueName: "Icon"; ValueData: """{app}\C4DRenderGuard.exe"""; Tasks: contextmenu
Root: HKCU; Subkey: "Software\Classes\SystemFileAssociations\.c4d\shell\C4DRenderGuard\command"; ValueType: string; ValueName: ""; ValueData: """{app}\C4DRenderGuard.exe"" ""%1"""; Tasks: contextmenu

[Run]
Filename: "{app}\C4DRenderGuard.exe"; Description: "Open C4D Render Guard now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the resume-after-restart entry, then stop a running renderer.
Filename: "{app}\C4DRenderGuard.exe"; Parameters: "autostart uninstall"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveAutostart"
Filename: "{sys}\taskkill.exe"; Parameters: "/F /T /IM C4DRenderGuard.exe"; Flags: runhidden waituntilterminated; RunOnceId: "StopGuard"

[Code]
// Updating: stop the running renderer (and its Cinema 4D) so the exe can be
// replaced. Unfinished renders continue when the app is opened again.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /T /IM C4DRenderGuard.exe', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := '';
end;
