; Inno Setup script for SSD Temperature Monitor
; Build:  iscc setup.iss      ->  installer\ssd_temp_monitor_setup.exe
; Requires dist\ssd_temp_monitor.exe (run build_exe.bat first)

#define MyAppName "SSD Temperature Monitor"
#define MyAppVersion "1.7.0"
#define MyAppExeName "ssd_temp_monitor.exe"
#define MyMutex "Local\SSDTempMonitor_SingleInstance"

[Setup]
AppId={{7D1A4C2E-9B3F-4E8A-A6D5-C3F1A9B2E4D7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=SSD Temp Monitor Project
DefaultDirName={autopf}\SSD Temp Monitor
DefaultGroupName={#MyAppName}
; the app itself requires admin (manifest), installer runs elevated anyway
PrivilegesRequired=admin
OutputDir=installer
OutputBaseFilename=ssd_temp_monitor_setup
SetupIconFile=app_icon.ico
Compression=lzma2/max
SolidCompression=yes
; 64-bit Python build -> install into the real "Program Files"
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
WizardStyle=modern
; refuse to install/upgrade while the tray app is still running
AppMutex={#MyMutex}
UninstallDisplayIcon={app}\{#MyAppExeName}

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
    GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart"; Description: "Start automatically at Windows login"; \
    GroupDescription: "Startup:"; Flags: checkedonce

[Files]
Source: "dist\ssd_temp_monitor.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: desktopicon
; auto-start on login (admin manifest of the exe makes Windows show UAC at login)
Name: "{commonstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
    Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; remove the CSV history on uninstall
Type: files; Name: "{userappdata}\..\Local\Temp\ssd_temp_history.csv"
