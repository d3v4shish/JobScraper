#define MyAppName "JobScraper"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "d3v"
#define MyAppExeName "JobScraper.exe"

[Setup]
AppId={{3A8B0F9C-7C35-4A16-B2CF-01A4A2E95B18}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
OutputDir=..\dist\installer
OutputBaseFilename=JobScraperSetup
SetupLogging=yes
CloseApplications=yes
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no

[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"
Type: files; Name: "{app}\{#MyAppExeName}"

[Files]
Source: "..\dist\JobScraper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function DataRoot(): String;
begin
  Result := ExpandConstant('{userdocs}\JobScraper');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    if UninstallSilent then
      Exit;
    if DirExists(DataRoot()) then
    begin
      if MsgBox(
        'Remove user data under "' + DataRoot() + '" as well?'#13#10#13#10 +
        'Choose No to keep the database, source config, logs, backups, exports, and debug files.',
        mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(DataRoot(), True, True, True);
      end;
    end;
  end;
end;
