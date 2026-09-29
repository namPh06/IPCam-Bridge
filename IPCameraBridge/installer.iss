#define AppName "IP Camera Bridge"
#define AppVersion "0.3.1"
#define OBSInstaller "OBS-Studio-32.2.2-Windows-x64-Installer.exe"

[Setup]
AppId={{B84E65CA-8D78-41AE-8961-9F36A6C8EC24}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={code:InstallDirectory}
DefaultGroupName={#AppName}
DisableDirPage=yes
DisableProgramGroupPage=yes
UsePreviousAppDir=no
UsePreviousPrivileges=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CreateAppDir=yes
Uninstallable=IsAdminInstallMode
UninstallDisplayIcon={app}\IPCameraBridge.exe
OutputDir=dist-service
OutputBaseFilename=IPCameraBridge-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
RestartIfNeededByRun=no
DisableFinishedPage=yes
SetupLogging=yes

[Files]
Source: "dist-service\IPCameraBridge\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Check: IsAdminInstallMode
Source: "third_party\OBS-NOTICE.txt"; DestDir: "{app}"; Flags: ignoreversion; Check: IsAdminInstallMode
Source: "third_party\{#OBSInstaller}"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: ShouldInstallOBS

[Tasks]
Name: "installobs"; Description: "Cài OBS Studio 32.2.2 (cung cấp thiết bị OBS Virtual Camera)"; GroupDescription: "Thành phần tùy chọn:"; Check: CanChooseOBS

[Icons]
Name: "{commonprograms}\{#AppName}"; Filename: "{app}\IPCameraBridge.exe"; Check: IsAdminInstallMode

[Code]
var
  OwnerPID: Integer;
  InstallFailed: Boolean;

function GetCustomSetupExitCode: Integer;
begin
  if InstallFailed then Result := 1 else Result := 0;
end;

function IsOBSInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf64}\obs-studio\bin\64bit\obs64.exe'));
end;

function CanChooseOBS: Boolean;
begin
  Result := IsAdminInstallMode and not IsOBSInstalled;
end;

function ShouldInstallOBS: Boolean;
begin
  Result := CanChooseOBS and WizardIsTaskSelected('installobs');
end;

function InstallDirectory(Param: String): String;
begin
  if IsAdminInstallMode then
    Result := ExpandConstant('{commonpf64}\IPCameraBridge')
  else
    Result := ExpandConstant('{tmp}\bootstrap');
end;

function GetCurrentProcessId: LongWord;
  external 'GetCurrentProcessId@kernel32.dll stdcall';
function GetFileAttributes(Path: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function HasReparseParent(Path: String): Boolean;
var
  Attributes: LongWord;
  Parent: String;
begin
  Result := False;
  while Length(Path) > 3 do begin
    Attributes := GetFileAttributes(Path);
    if (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0) then begin
      Result := True;
      Exit;
    end;
    Parent := ExtractFileDir(Path);
    if Parent = Path then Exit;
    Path := Parent;
  end;
end;

function InitializeSetup: Boolean;
begin
  OwnerPID := StrToIntDef(ExpandConstant('{param:OWNERPID|0}'), 0);
  if IsAdminInstallMode then
    Result := (OwnerPID > 0) and IsAdmin
  else
    Result := (OwnerPID = 0) and not IsAdmin;
  if not Result then
    SuppressibleMsgBox('Open this installer normally from your Windows account. Do not use Run as administrator. Setup will request administrator permission when needed.', mbError, MB_OK, IDOK);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Target: String;
begin
  Result := '';
  if not IsAdminInstallMode then Exit;
  Target := ExpandConstant('{commonpf64}\IPCameraBridge');
  if (CompareText(ExpandFileName(WizardDirValue), Target) <> 0) or
     HasReparseParent(Target) or
     HasReparseParent(ExpandConstant('{commonappdata}\IPCameraBridge')) then begin
    Result := 'The installation path must be the standard Program Files folder without directory links.';
    Exit;
  end;
  { Keep an existing installation intact; in-place upgrades need a separate transaction. }
  if DirExists(Target) then
    Result := 'IP Camera Bridge is already installed. Uninstall it while keeping camera data, then run this installer again. Setup has not changed the existing installation.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Code: Integer;
  Params: String;
begin
  if (CurStep = ssInstall) and not IsAdminInstallMode then begin
    { Inno permits relaunching Setup only after ssInstall. This outer process
      copies no payload, uses only a temporary directory, and stays alive
      so the elevated helper can authenticate the original user's token. }
    Params := '/ALLUSERS /NORESTART /OWNERPID=' + IntToStr(GetCurrentProcessId);
    if WizardSilent then Params := Params + ' /VERYSILENT /SUPPRESSMSGBOXES';
    if not ShellExec('runas', ExpandConstant('{srcexe}'), Params, '',
                     SW_SHOWNORMAL, ewWaitUntilTerminated, Code) then
      RaiseException('Administrator permission was not granted. Nothing was installed.');
    if Code <> 0 then
      RaiseException('The elevated installer did not finish successfully.');
  end;
  if (CurStep = ssPostInstall) and IsAdminInstallMode then begin
    InstallFailed := True;
    if not Exec(ExpandConstant('{app}\IPCameraBridge.exe'),
                '--install-service --owner-pid ' + IntToStr(OwnerPID),
                ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then
      RaiseException('Could not start the service installer.');
    if Code <> 0 then
      RaiseException('Service registration failed. Camera data was preserved.');
    if ShouldInstallOBS then begin
      if not Exec(ExpandConstant('{tmp}\{#OBSInstaller}'), '/S',
                  ExpandConstant('{tmp}'), SW_HIDE, ewWaitUntilTerminated, Code) then
        SuppressibleMsgBox('IP Camera Bridge was installed, but the optional OBS Studio installer could not start. Install OBS Studio separately.', mbError, MB_OK, IDOK)
      else if Code <> 0 then
        SuppressibleMsgBox('IP Camera Bridge was installed, but OBS Studio installation failed. Install OBS Studio separately.', mbError, MB_OK, IDOK);
    end;
    InstallFailed := False;
  end;
end;

function InitializeUninstall: Boolean;
var
  Params: String;
  Code: Integer;
begin
  Params := '--uninstall-service';
  if not UninstallSilent then
    if MsgBox('Also delete saved service cameras and passwords? Choose No to keep them for reinstall.',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      Params := Params + ' --remove-data';
  { The helper disables the logon task, stops the publisher, then the service,
    and removes registration before Inno can delete any installed binaries. }
  Result := Exec(ExpandConstant('{app}\IPCameraBridge.exe'), Params,
                 ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
  if Result then Result := Code = 0;
  if not Result then
    SuppressibleMsgBox('The service could not be stopped safely. Uninstall has been cancelled; no program files were removed.', mbError, MB_OK, IDOK);
end;
