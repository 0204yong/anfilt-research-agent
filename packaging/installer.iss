; 리서치 에이전트 설치판 인스톨러 (Inno Setup 6)
;
;   "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" packaging\installer.iss
;
; → docs/22 설치판 아키텍처 2절 · docs/23 설치판 구현 계획 0단계
;
; 설계 요점
;  · PrivilegesRequired=lowest — **관리자 권한을 요구하지 않는다.**
;    Program Files에 깔면 쓰기에 관리자가 필요해 자동 업데이트마다 UAC가 뜬다.
;    per-user 설치라야 조용한 업데이트가 가능하다.
;  · 사용자 데이터(%APPDATA%\ANFILT)와 볼트 폴더는 여기서 건드리지 않는다.
;    제거해도 남아야 한다 — 고객 데이터를 인스톨러가 지우면 안 된다.
;  · 업데이트는 같은 AppId로 덮어쓰기 설치하면 된다(6단계에서 /VERYSILENT 사용).

; 별도 런처 exe를 컴파일하지 않는다. 바로가기가 runtime\pythonw.exe 를 직접 부르고
; 아이콘만 씌운다 — 컴파일 산출물이 없으면 백신 오탐 여지가 그만큼 준다
; (→ docs/18 설치형 패키지 2절: PyInstaller onefile 비권장과 같은 이유).
#define AppName "리서치 에이전트"
#define Publisher "ANFILT"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  ; ISCC 호출 시 /DSourceDir="...\dist\ResearchAgent" 로 넘긴다 (build.ps1 이 처리)
  #error SourceDir 를 지정하세요: ISCC /DSourceDir="<빌드 산출물 경로>" installer.iss
#endif

[Setup]
AppId={{7C1E9B84-3F2A-4D6E-9A51-ANFILTRA0001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={localappdata}\Programs\{#Publisher}\ResearchAgent
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=ResearchAgent-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
UninstallDisplayIcon={app}\app.ico
UsePreviousAppDir=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 작업:"
Name: "watchtask";   Description: "자동 모니터링을 매시 실행 (작업 스케줄러 등록)"; GroupDescription: "추가 작업:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; pythonw.exe 로 launcher.py 를 띄우되, 아이콘·표시이름은 제품의 것으로 덮는다
Name: "{group}\{#AppName}"; Filename: "{app}\runtime\pythonw.exe"; \
  Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\runtime\pythonw.exe"; \
  Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; \
  Tasks: desktopicon

[Run]
; ① 바이트코드 선컴파일 — 이게 없으면 **첫 실행이 10초**가 된다(실측).
;    설치 시 15초를 쓰면 첫 기동이 2초로 떨어진다. .pyc 는 설치 시 생성되므로
;    다운로드 용량에는 영향이 없다 (→ docs/23 0단계 실측 결과).
Filename: "{app}\runtime\python.exe"; \
  Parameters: "-m compileall -q -j 0 ""{app}\runtime\Lib\site-packages"" ""{app}\app"""; \
  StatusMsg: "첫 실행을 빠르게 하기 위해 준비 중입니다... (약 15초)"; Flags: runhidden

; ② 자동 모니터링 — 매시 정각. PC가 꺼져 있어 놓친 실행은 켜질 때 따라잡는다
;    (→ docs/23 8단계: watch.is_due 따라잡기와 짝을 이룬다)
Filename: "schtasks"; \
  Parameters: "/Create /F /TN ""ANFILT 리서치에이전트 모니터링"" /SC HOURLY /MO 1 /TR ""\""{app}\runtime\pythonw.exe\"" \""{app}\app\watch_run.py\"""""; \
  Flags: runhidden; Tasks: watchtask

; ③ 설치 직후 실행 (사용자가 체크했을 때만)
Filename: "{app}\runtime\pythonw.exe"; Parameters: """{app}\launcher.py"""; \
  WorkingDir: "{app}"; Description: "{#AppName} 실행"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "schtasks"; Parameters: "/Delete /F /TN ""ANFILT 리서치에이전트 모니터링"""; \
  Flags: runhidden; RunOnceId: "DelWatchTask"

[UninstallDelete]
; 앱이 실행 중 만드는 것들(바이트코드 캐시, 브라우저 프로필)은 제거 시 함께 지운다.
; %APPDATA%\ANFILT (설정·라이선스·로그)와 볼트 폴더는 **일부러 남긴다.**
Type: filesandordirs; Name: "{app}\runtime\Lib\site-packages"
Type: filesandordirs; Name: "{app}\app"
Type: dirifempty;     Name: "{app}"

[Messages]
korean.FinishedLabel=설치가 끝났습니다. 처음 실행하면 지식볼트 폴더를 만들고 API 키를 등록하는 안내가 나옵니다.
