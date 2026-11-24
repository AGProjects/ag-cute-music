; Inno Setup script of the Windows installer (windows/make-standalone.ps1 passes the /D values): the app folder
; into the user's programs (%LOCALAPPDATA%\Programs, no admin rights), a Start menu entry, a desktop one if wanted.

#ifndef AppName
  #define AppName "AG CuteMusic"
#endif
#ifndef AppId
  #define AppId "ag-cute-music"
#endif
#ifndef AppVersion
  #define AppVersion "0"
#endif

[Setup]
AppId={{1706F9D1-FF5B-518D-8FED-9B5AC8FFF54E}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=AG Projects
AppPublisherURL=https://ag-projects.com
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=AG-CuteMusic-{#AppVersion}-setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\{#AppName}.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#AppDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; a new version's GStreamer replaces the old one's whole (no stale plugins left in it)
Type: filesandordirs; Name: "{app}\gstreamer"
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppName}.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppName}.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppName}.exe"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
; an update (cutemusic/updater.py runs us silently with /RELAUNCH=1): the app started again once installed
Filename: "{app}\{#AppName}.exe"; Flags: nowait runasoriginaluser; Check: Relaunch

[Code]
function Relaunch: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;
