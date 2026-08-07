# 설치판 빌드 — 임베디드 파이썬 런타임 + 앱 소스를 배포 폴더로 굽는다.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1
#   powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Out D:\dist -SkipRuntime
#
# → docs/22 설치판 아키텍처 2절(디스크 레이아웃) · docs/23 설치판 구현 계획 0단계
#
# ⚠️ 빌드 경로는 **짧게** 유지해야 한다. Windows 롱패스가 꺼진 상태(기본값)에서
#    pip가 깊은 벤더 경로를 풀다 MAX_PATH(260자)에 걸린다. 기본 작업 폴더가
#    C:\Users\<사용자>\AppData\Local\Temp\ra-build 인 이유다.

param(
  [string]$Out = "$env:LOCALAPPDATA\Temp\ra-build\dist",
  [string]$Work = "$env:LOCALAPPDATA\Temp\ra-build",
  [string]$PyVersion = "3.12.10",
  [switch]$SkipRuntime          # 런타임을 이미 구운 경우 앱 소스만 갱신
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Dist = Join-Path $Out 'ResearchAgent'
$Runtime = Join-Path $Dist 'runtime'
$AppOut = Join-Path $Dist 'app'

function Size-MB($path) {
  if (-not (Test-Path $path)) { return 0 }
  [math]::Round(((Get-ChildItem $path -Recurse -File -EA SilentlyContinue |
    Measure-Object Length -Sum).Sum) / 1MB, 0)
}

New-Item -ItemType Directory -Force -Path $Work, $Dist | Out-Null

# ---------------------------------------------------------------- 1. 런타임
if (-not $SkipRuntime) {
  if (Test-Path $Runtime) { Remove-Item -Recurse -Force $Runtime }
  $zip = Join-Path $Work "python-$PyVersion-embed-amd64.zip"
  if (-not (Test-Path $zip)) {
    Write-Host "· 임베디드 파이썬 $PyVersion 내려받는 중..."
    Invoke-WebRequest -UseBasicParsing -OutFile $zip `
      -Uri "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip"
  }
  Expand-Archive -Path $zip -DestinationPath $Runtime

  # 임베디드 배포판은 기본적으로 site-packages를 안 본다 — ._pth 를 열어 준다
  $tag = "python" + ($PyVersion -split '\.')[0] + ($PyVersion -split '\.')[1]
  @"
$tag.zip
.
Lib\site-packages
import site
"@ | Out-File -FilePath (Join-Path $Runtime "$tag._pth") -Encoding ascii
  New-Item -ItemType Directory -Force -Path (Join-Path $Runtime 'Lib\site-packages') | Out-Null

  $getpip = Join-Path $Work 'get-pip.py'
  if (-not (Test-Path $getpip)) {
    Invoke-WebRequest -UseBasicParsing -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $getpip
  }
  & (Join-Path $Runtime 'python.exe') $getpip --no-warn-script-location -q

  Write-Host "· 의존성 설치 중... (수 분)"
  & (Join-Path $Runtime 'python.exe') -m pip install -q --no-warn-script-location `
    --disable-pip-version-check -r (Join-Path $RepoRoot 'requirements.txt') keyring
  if ($LASTEXITCODE -ne 0) { throw "의존성 설치 실패" }
  Write-Host ("  설치 직후: {0} MB" -f (Size-MB $Runtime))

  # ── 다이어트 ────────────────────────────────────────────────
  # google-api-python-client 는 Google Slides 엔진 전용(98MB). 그 엔진은
  # ImportError 를 잡아 안내를 내고, 키가 없으면 UI에 뜨지도 않으므로
  # 빼도 기능 저하가 없다 (→ core/ppt_engines/gslides_engine.py:31).
  & (Join-Path $Runtime 'python.exe') -m pip uninstall -y -q `
    google-api-python-client google-auth-httplib2 httplib2 uritemplate 2>&1 | Out-Null

  $sp = Join-Path $Runtime 'Lib\site-packages'
  foreach ($n in @('pip', 'setuptools', 'wheel', '_distutils_hack', 'pkg_resources')) {
    Get-ChildItem $sp -Filter "$n*" -EA SilentlyContinue | Remove-Item -Recurse -Force -EA SilentlyContinue
  }
  Remove-Item (Join-Path $sp 'distutils-precedence.pth') -Force -EA SilentlyContinue
  Get-ChildItem $sp -Recurse -Directory -Filter __pycache__ -EA SilentlyContinue |
    Remove-Item -Recurse -Force -EA SilentlyContinue
  Get-ChildItem $sp -Recurse -Directory -EA SilentlyContinue |
    Where-Object { $_.Name -in @('tests', 'test', 'testing') } |
    Remove-Item -Recurse -Force -EA SilentlyContinue
  Write-Host ("  다이어트 후: {0} MB" -f (Size-MB $Runtime))
}

# ---------------------------------------------------------------- 2. 앱 소스
if (Test-Path $AppOut) { Remove-Item -Recurse -Force $AppOut }
New-Item -ItemType Directory -Force -Path $AppOut | Out-Null
foreach ($f in @('app.py', 'ui_common.py', 'watch_run.py')) {
  Copy-Item (Join-Path $RepoRoot $f) $AppOut
}
foreach ($d in @('core', 'pages', 'vault_seed')) {
  Copy-Item (Join-Path $RepoRoot $d) $AppOut -Recurse
}
# .streamlit 은 **통째로 복사하지 않는다** — 로컬 secrets.toml 이 딸려 나가면
# 대표 API 키가 고객 PC로 배포된다. 필요한 파일만 골라 담는다.
New-Item -ItemType Directory -Force -Path (Join-Path $AppOut '.streamlit') | Out-Null
Copy-Item (Join-Path $RepoRoot '.streamlit\config.toml') (Join-Path $AppOut '.streamlit')

Get-ChildItem $AppOut -Recurse -Directory -Filter __pycache__ -EA SilentlyContinue |
  Remove-Item -Recurse -Force -EA SilentlyContinue

# ── 비밀 유출 방어 (빌드 실패로 처리) ──────────────────────────
# 검사 대상은 **우리가 담은 앱 영역만**. runtime\ 은 서드파티 패키지라
# certifi\cacert.pem(정상 CA 번들) 같은 파일이 있어 여기서 보면 오탐이 난다.
$leaks = Get-ChildItem $AppOut -Recurse -File -Force -EA SilentlyContinue |
  Where-Object { $_.Name -in @('.env', 'secrets.toml') -or $_.Extension -in @('.pem', '.key', '.p12') }
if ($leaks) {
  $leaks | ForEach-Object { Write-Host "  유출 위험 파일: $($_.FullName)" }
  throw "빌드 산출물에 비밀 파일이 포함되었습니다 — 중단"
}
$pat = 'sk-ant-api[0-9]{2}-[A-Za-z0-9_-]{20,}|AIzaSy[A-Za-z0-9_-]{30,}|sk-proj-[A-Za-z0-9_-]{20,}'
$hits = Get-ChildItem $AppOut -Recurse -File -Include *.py, *.toml, *.json, *.md -EA SilentlyContinue |
  Select-String -Pattern $pat -List -EA SilentlyContinue
if ($hits) {
  $hits | ForEach-Object { Write-Host "  키 패턴 발견: $($_.Path)" }
  throw "빌드 산출물에서 API 키 패턴이 발견되었습니다 — 중단"
}

Copy-Item (Join-Path $PSScriptRoot 'launcher.py') $Dist
Copy-Item (Join-Path $PSScriptRoot 'app.ico') $Dist

# ---------------------------------------------------------------- 3. 버전
$appVersion = (Get-Content (Join-Path $RepoRoot 'packaging\VERSION') -EA SilentlyContinue)
if (-not $appVersion) { $appVersion = '0.1.0-dev' }
# BOM 없이 쓴다 — Out-File -Encoding utf8 은 BOM을 붙이고, 그러면 파이썬이
# utf-8 로 읽을 때 json.loads 가 실패한다 (0단계 이후 실측으로 잡은 버그).
# 읽는 쪽(core/edition.py)도 utf-8-sig 로 방어하지만 굽는 쪽부터 깨끗하게 둔다.
$versionJson = @{ app_version = "$appVersion".Trim(); runtime_version = $PyVersion } |
  ConvertTo-Json
[IO.File]::WriteAllText((Join-Path $Dist 'version.json'), $versionJson,
  [Text.UTF8Encoding]::new($false))

# 개발용 실행 스크립트 (인스톨러가 만드는 바로가기와 같은 명령)
@"
@echo off
start "" "%~dp0runtime\pythonw.exe" "%~dp0launcher.py"
"@ | Out-File (Join-Path $Dist '리서치에이전트.cmd') -Encoding oem

# ---------------------------------------------------------------- 4. 인스톨러 (선택)
# Inno Setup 이 깔려 있으면 설치 파일까지 굽는다. 없으면 폴더 배포만 하고 넘어간다.
$iscc = @(
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",   # /CURRENTUSER 설치 (UAC 없음)
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
  Write-Host "· 인스톨러 컴파일 중..."
  & $iscc "/DSourceDir=$Dist" "/DAppVersion=$($appVersion.Trim())" `
    (Join-Path $PSScriptRoot 'installer.iss') | Select-Object -Last 3
  if ($LASTEXITCODE -ne 0) { throw "인스톨러 컴파일 실패" }
} else {
  Write-Host "· Inno Setup 미설치 — 폴더 배포만 생성 (설치 파일은 건너뜀)"
}

Write-Host ""
Write-Host ("빌드 완료: {0}" -f $Dist)
Write-Host ("  runtime {0} MB · app {1} MB · 합계 {2} MB" -f `
  (Size-MB $Runtime), (Size-MB $AppOut), (Size-MB $Dist))
