; Inno Setup script -- per-user install, no admin needed.
; Build: iscc installer.iss   (CI passes /DMyAppVersion=x.y.z)

#ifndef MyAppVersion
  #define MyAppVersion "4.10.7"
#endif

[Setup]
AppId={{7E2C9B41-5A83-4F7D-9C1E-BD64A20F31D8}
AppName=Snap
AppVersion={#MyAppVersion}
AppPublisher=MrBeanTheOne
AppPublisherURL=https://github.com/MrBeanTheOne/Snap
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
; Snap hides to the tray instead of closing, so Restart Manager's WM_CLOSE only made it
; disappear and setup then failed with "cannot close the application" against a locked
; Snap.exe. AppMutex detects the instance up front -- tray-hidden, no window, still caught --
; and asks the user to quit it, which is actionable where the silent failure was not.
AppMutex=Global\SnapDownloaderApp
CloseApplications=no
LicenseFile=LICENSE

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "dist\Snap\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\Snap"; Filename: "{app}\Snap.exe"
Name: "{autodesktop}\Snap"; Filename: "{app}\Snap.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Snap.exe"; Description: "Launch Snap"; Flags: nowait postinstall skipifsilent
