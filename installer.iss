; Inno Setup script — per-user install, no admin needed.
; Build: iscc installer.iss   (CI passes /DMyAppVersion=x.y.z)

#ifndef MyAppVersion
  #define MyAppVersion "4.10.2"
#endif

[Setup]
AppId={{7E2C9B41-5A83-4F7D-9C1E-BD64A20F31D8}
AppName=Snap
AppVersion={#MyAppVersion}
AppPublisher=MrBeanTheOne
AppPublisherURL=https://github.com/MrBeanTheOne/snap
DefaultDirName={localappdata}\Programs\Snap
DefaultGroupName=Snap
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=Snap-setup
SetupIconFile=assets\snap.ico
UninstallDisplayIcon={app}\Snap.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "dist\Snap\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Snap"; Filename: "{app}\Snap.exe"
Name: "{autodesktop}\Snap"; Filename: "{app}\Snap.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Snap.exe"; Description: "Launch Snap"; Flags: nowait postinstall skipifsilent
