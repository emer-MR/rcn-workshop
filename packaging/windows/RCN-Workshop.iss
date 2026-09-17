; ============================================================================
;  RCN Workshop - instalator Windows (Inno Setup 6)
; ============================================================================
;  Pakuje konsumencki build PyInstallera (one-folder z dist/RCN-Workshop/)
;  w jeden setup.exe ze skrotami, deinstalatorem i bootstrapperem WebView2.
;
;  Wymaga:
;    1. Inno Setup 6.1+ (https://jrsoftware.org/isdl.php) - kompilator ISCC.exe.
;    2. Zbudowanego buildu konsumenta w dist/RCN-Workshop/ (PyInstaller):
;         uv run --python 3.12 --extra desktop --extra build pyinstaller --noconfirm RCN-Workshop.spec
;    3. (opcjonalnie) MicrosoftEdgeWebview2Setup.exe obok tego .iss - oficjalny
;       Evergreen bootstrapper WebView2 (maly, ~2 MB) z:
;       https://developer.microsoft.com/microsoft-edge/webview2/  (sekcja "Evergreen Bootstrapper").
;       Gdy pliku brak, instalator i tak zadziala - tylko pominie auto-instalacje
;       WebView2 (Win11 ma go wbudowanego; Win10 zwykle tez).
;
;  Kompilacja instalatora (z katalogu repo):
;    & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\windows\RCN-Workshop.iss
;  Wynik: packaging\windows\Output\RCN-Workshop-Setup.exe
;
;  DECYZJA (STATUS 2026-06-08): instalacja do C:\RCN Workshop - prosta, widoczna
;  sciezka, spojna z _frozen_data_dir w desktop.py (dane w C:\RCN Workshop\workspaces).
;  Deinstalator NIE kasuje workspaces - dane uzytkownika przezywaja odinstalowanie.
; ============================================================================

#define AppName "RCN Workshop"
; Synchronizowac z app/version.py (tam jest zrodlo prawdy).
#define AppVersion "0.2.0-beta.8"
#define AppPublisher "Michal Raj"
#define AppExeName "RCN-Workshop.exe"
; Katalog z wynikiem PyInstallera (wzgledem tego .iss: ..\..\dist\RCN-Workshop)
#define DistDir "..\..\dist\RCN-Workshop"
; GUID aplikacji - staly, zeby upgrade nadpisywal poprzednia instalacje.
#define AppId "{{8E1C5A2B-4F3D-4A6E-9B7C-RCNWORKSHOP01}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
; Instalacja do C:\RCN Workshop (decyzja STATUS) - uzytkownik moze zmienic.
DefaultDirName=C:\RCN Workshop
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=Output
; Wersja w nazwie pliku -- tester od razu widzi, ktora bete ma na dysku,
; a na serwerze pobierania moga lezec obok siebie kolejne wydania.
OutputBaseFilename=RCN-Workshop-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; --- Licencja i informacje o becie (ekrany kreatora) -----------------------
; AGPL-3.0 z korzenia repo (ekran akceptacji licencji).
LicenseFile=..\..\LICENSE
; Polski ekran PRZED instalacja: co to beta, brak gwarancji, odpowiedzialnosc
; za wykorzystanie danych. Tester musi przez niego przejsc.
InfoBeforeFile=beta-info.txt
; Polski ekran PO instalacji: pierwsze kroki (dogranie workspace, zgłaszanie uwag).
InfoAfterFile=README-TESTER.txt
; --- Aktualizacje (STATUS krok 5, dyskusja 2026-06-10) ---------------------
; AppMutex: desktop.py tworzy nazwany mutex na czas zycia okna -- setup wykrywa
; dzialajaca aplikacje i prosi o jej zamkniecie przed nadpisaniem plikow.
AppMutex=RCNWorkshopAppMutex
CloseApplications=yes
RestartApplications=no
; Aplikacja 64-bit (GDAL/PROJ) -> instalujemy w trybie x64.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Wymaga uprawnien admina (instalacja do C:\ i ewentualny WebView2).
PrivilegesRequired=admin
; UninstallDisplayIcon na .exe aplikacji.
UninstallDisplayIcon={app}\{#AppExeName}
; SetupIconFile=rcn.ico   ; <- odkomentuj, gdy bedzie ikona aplikacji

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "desktopicon"; Description: "Utworz skrot na pulpicie"; GroupDescription: "Skroty:"

[InstallDelete]
; Upgrade: wyczysc _internal PRZED wgraniem nowych plikow. PyInstaller zmienia
; zestaw bibliotek miedzy wersjami, a Inno tylko nadpisuje -- osierocone pliki
; starego buildu w _internal potrafia byc ladowane zamiast nowych (DLL-e).
; BEZPIECZNE: _internal to wylacznie build; dane uzytkownika zyja w {app}\workspaces,
; ktorego NIE dotykamy (podobnie rcn.env i logi).
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; Caly one-folder build PyInstallera. fl:recursesubdirs ciagnie podkatalogi (_internal itd.).
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Licencja AGPL-3.0 obok aplikacji -- uzytkownik ma ja lokalnie, nie tylko w kreatorze.
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
; Instrukcja testera zostaje na dysku (pokazywana tez w kreatorze jako InfoAfter).
Source: "README-TESTER.txt"; DestDir: "{app}"; Flags: ignoreversion
; Opcjonalna konfiguracja dystrybucji (rcn.env obok .iss, NIE commitowany do repo --
; moze zawierac prywatne adresy, np. RCN_SURVEY_URL z linkiem do ankiety zgloszen).
; onlyifdoesntexist: upgrade NIE nadpisuje pliku, ktory tester mogl zmodyfikowac.
Source: "rcn.env"; DestDir: "{app}"; Flags: onlyifdoesntexist skipifsourcedoesntexist
; Bootstrapper WebView2 - dolaczany TYLKO gdy plik istnieje obok .iss (Flags: external nie,
; bo chcemy go spakowac). 'skipifsourcedoesntexist' = brak pliku nie wywala kompilacji.
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall skipifsourcedoesntexist

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Folder z danymi (workspaces)"; Filename: "{app}\workspaces"
Name: "{group}\Odinstaluj {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; 1. WebView2 - instalowany cicho TYLKO gdy nieobecny i gdy bootstrapper dolaczono.
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
  StatusMsg: "Instalowanie srodowiska WebView2..."; \
  Check: WebView2BootstrapperPresent and not WebView2Installed; Flags: waituntilterminated
; 2. Uruchom aplikacje po instalacji (opcjonalnie).
Filename: "{app}\{#AppExeName}"; Description: "Uruchom {#AppName} teraz"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Sprzataj log serwera (tworzony w runtime, Inno go nie sledzi).
Type: files; Name: "{app}\rcn-server.log"
; UWAGA: NIE usuwamy {app}\workspaces - to dane uzytkownika (wzbogacone bazy).
; Inno usuwa tylko pliki zainstalowane z [Files]; workspaces tworzy sie w runtime,
; wiec i tak nie jest sledzony. Zostawiamy swiadomie - patrz naglowek pliku.

[Code]
{ Detekcja WebView2 Evergreen - sprawdza klucz 'pv' (zainstalowana wersja) w
  trzech lokalizacjach uzywanych przez instalator WebView2 (per-machine x64,
  per-machine x86, per-user). Niepusta wersja = WebView2 obecny. }
function WebView2Installed(): Boolean;
var
  Pv: String;
begin
  Result :=
    (RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Pv) and (Pv <> '')) or
    (RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Pv) and (Pv <> '')) or
    (RegQueryStringValue(HKCU, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Pv) and (Pv <> ''));
end;

{ Czy do instalatora dolaczono bootstrapper WebView2 (plik trafil do katalogu tymczasowego). }
function WebView2BootstrapperPresent(): Boolean;
begin
  Result := FileExists(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'));
end;
