#ifndef AppVersion
  #error AppVersion must be supplied by the release build script
#endif
#ifndef AppExe
  #error AppExe must be supplied by the release build script
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by the release build script
#endif

[Setup]
#ifdef TestMode
AppId=Solverg.TagExplorer.InstallerSmokeTest
PrivilegesRequired=lowest
Uninstallable=no
CreateUninstallRegKey=no
#else
AppId=Solverg.TagExplorer
PrivilegesRequired=admin
#endif
AppName=TagExplorer
AppVersion={#AppVersion}
AppPublisher=Solverg
DefaultDirName={autopf}\TagExplorer
DisableDirPage=yes
UsePreviousAppDir=no
DefaultGroupName=TagExplorer
DisableProgramGroupPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=TagExplorer-Setup
SetupIconFile=..\app.ico
UninstallDisplayIcon={app}\TagExplorer.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\TagExplorer"; Filename: "{app}\TagExplorer.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\TagExplorer"; Filename: "{app}\TagExplorer.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\TagExplorer.exe"; Description: "Launch TagExplorer"; Flags: postinstall nowait skipifsilent unchecked; Check: not StartedByBridge

[Code]
function StartedByBridge: Boolean;
begin
  Result := ExpandConstant('{param:TAGEXPLORER_BRIDGE|0}') = '1';
end;
