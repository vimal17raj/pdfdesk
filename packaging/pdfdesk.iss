; Inno Setup script: builds PDFDesk-Setup.exe
; Needs: dist-win\PDFDesk (from PyInstaller) and packaging\vendor\tesseract-setup.exe (OCR engine installer).
; Build with:  "C:\Users\<you>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" packaging\pdfdesk.iss
#define AppName "PDFDesk"
#define AppVersion "1.0.0"

[Setup]
AppId={{6C3B7B0E-5E39-4E5A-9B0B-4B1B9A2D7F10}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=PDFDesk
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\PDFDesk.exe
SetupIconFile=pdfdesk.ico
OutputDir=D:\
OutputBaseFilename=PDFDesk-Setup
Compression=lzma2/max
SolidCompression=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=commandline
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
DisableProgramGroupPage=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce

[Files]
Source: "..\dist-win\PDFDesk\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "vendor\tesseract-setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: NeedsTesseract

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\PDFDesk.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PDFDesk.exe"; Tasks: desktopicon

[Run]
; OCR engine (Tesseract), installed silently only if it isn't already on this PC
Filename: "{tmp}\tesseract-setup.exe"; Parameters: "/S"; StatusMsg: "Installing the OCR engine (Tesseract)..."; Flags: waituntilterminated; Check: NeedsTesseract
Filename: "{app}\PDFDesk.exe"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM PDFDesk.exe"; Flags: runhidden; RunOnceId: "StopPDFDesk"

[Code]
function NeedsTesseract: Boolean;
begin
  Result := not (FileExists(ExpandConstant('{commonpf64}\Tesseract-OCR\tesseract.exe'))
              or FileExists(ExpandConstant('{commonpf32}\Tesseract-OCR\tesseract.exe')));
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    if not UninstallSilent then
      MsgBox('Your documents and settings were kept in ' + ExpandConstant('{localappdata}\PDFDesk') +
             '. Delete that folder if you want to remove them too.', mbInformation, MB_OK);
end;
