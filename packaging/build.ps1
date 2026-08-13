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
  [switch]$SkipRuntime,         # 런타임을 이미 구운 경우 앱 소스만 갱신
  # 릴리스 매니페스트에 박히는 값들 (→ docs/19 4.2절 · docs/25 릴리스 파이프라인)
  #
  # 설치 파일은 **소스와 다른 저장소**에 둔다. 소스 저장소를 언젠가 비공개로
  # 돌리면 그쪽 Releases 자산은 인증을 요구하게 되고, 토큰이 없는 고객 PC 는
  # 받지 못한다. 그때 배포가 멈추지 않도록 처음부터 갈라 둔다 (→ docs/25).
  [string]$ReleaseBase = "https://github.com/0204yong/anfilt-releases/releases/download",
  [string]$NotesBase = "https://anfilt-homepage.netlify.app/releases",
  [string]$MinSupported = "0.1.0",
  # 라이선스 (→ docs/20). 팩을 빼면 활성화 없이는 조사가 시작조차 안 된다.
  [string]$PubKey = "",              # Ed25519 공개키 (packaging\make_signing_key.py)
  [string]$LicenseServer = "",       # 비우면 core/licensing.py 의 기본값
  [switch]$IncludePack               # 시연·내부용으로 팩을 동봉하고 싶을 때만
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Dist = Join-Path $Out 'ResearchAgent'
$Runtime = Join-Path $Dist 'runtime'
$AppOut = Join-Path $Dist 'app'
$Releases = Join-Path $Out 'releases'      # 델타 zip · latest.json · 인스톨러 사본

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
Copy-Item (Join-Path $PSScriptRoot 'updater.py') $Dist
Copy-Item (Join-Path $PSScriptRoot 'app.ico') $Dist

# ---------------------------------------------------------------- 2-1. 라이선스
# 이 제품의 값어치는 코드가 아니라 프롬프트다 (→ docs/20). 그래서 **팩을 빼고**
# 라이선스로 서버에서 인출하게 한다 — 복제본은 조사를 시작할 부품 자체가 없다.
$packFile = Join-Path $AppOut 'core\prompts\pack.json'
if ($IncludePack) {
  Write-Host "· ⚠️ 팩을 동봉합니다 (-IncludePack) — 라이선스 없이도 동작하는 빌드입니다"
} else {
  # 팩은 뺐는데 공개키가 없으면 **아무도 열 수 없는 빌드**가 나온다.
  # 공개키는 이제 core/licensing.py 에 박혀 있으므로, 그것이 비어 있고
  # -PubKey 도 없을 때만 막는다.
  if (-not $PubKey) {
    $baked = [IO.File]::ReadAllText((Join-Path $AppOut 'core\licensing.py'))
    if ($baked -notmatch '(?m)^PUBKEY_B64 = "[^"]+"') {
      throw "팩을 제외하려면 -PubKey 가 필요합니다 (packaging\make_signing_key.py 로 생성). 시연용이면 -IncludePack 을 주세요."
    }
  }
  if (Test-Path $packFile) { Remove-Item $packFile -Force }
  # ESG 시드 온톨로지 35노트도 이 제품의 값어치다 (→ docs/20) — 함께 뺀다.
  # 팩 안에 `seed` 로 실려 활성화 때 내려온다 (packaging\make_pack.py).
  $seedDir = Join-Path $AppOut 'vault_seed'
  if (Test-Path $seedDir) { Remove-Item $seedDir -Recurse -Force }
  Write-Host "· 프롬프트 팩·시드 제외 — 활성화로만 인출됩니다"
}

if ($PubKey -or $LicenseServer) {
  $lic = Join-Path $AppOut 'core\licensing.py'
  $src = [IO.File]::ReadAllText($lic)
  # `= ""` 만 노리면 공개키가 박힌 뒤로는 -PubKey 가 **조용히 무시된다** —
  # 키를 갈았는데 구 공개키가 나가는 사고가 된다. 값이 있든 없든 덮는다.
  if ($PubKey) { $src = $src -replace '(?m)^PUBKEY_B64 = "[^"]*"', ('PUBKEY_B64 = "' + $PubKey + '"') }
  if ($LicenseServer) { $src = $src -replace '(?m)^SERVER_DEFAULT = ".*"', ('SERVER_DEFAULT = "' + $LicenseServer + '"') }
  [IO.File]::WriteAllText($lic, $src, [Text.UTF8Encoding]::new($false))
  Write-Host "· 라이선스 설정 주입 완료"
}

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
# app\ 안에도 같은 파일을 둔다 — 델타 업데이트는 app\ 만 갈아 끼우므로,
# 여기에 없으면 업데이트 후에도 옛 버전으로 보고해 같은 업데이트를 영원히
# 다시 권하게 된다 (6단계 실측으로 잡은 버그). 읽는 쪽은 app\ 것을 우선한다.
[IO.File]::WriteAllText((Join-Path $AppOut 'version.json'), $versionJson,
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

# ---------------------------------------------------------------- 5. 릴리스 산출물
# 자동 업데이트가 받는 두 가지를 여기서 함께 굽는다 (→ 설계서 19 4.4절).
#   · app-<버전>.zip  — 델타. 런타임이 그대로일 때 받는 것 (수 MB)
#   · latest.json     — 홈페이지 releases/ 에 올릴 매니페스트
# 델타 zip 의 루트는 **app 폴더의 내용**이다. updater.py 가 <설치폴더>\app\ 에
# 그대로 풀기 때문이다 — 한 겹 더 감싸면 app\app\ 이 된다.
$ver = "$appVersion".Trim()
$deltaName = "app-$ver.zip"
$deltaPath = Join-Path $Releases $deltaName
New-Item -ItemType Directory -Force -Path $Releases | Out-Null
if (Test-Path $deltaPath) { Remove-Item $deltaPath -Force }
Compress-Archive -Path (Join-Path $AppOut '*') -DestinationPath $deltaPath -Force
Write-Host ("· 델타 zip: {0} ({1} MB)" -f $deltaName, [math]::Round((Get-Item $deltaPath).Length / 1MB, 1))

function Sha-Of([string]$path) { (Get-FileHash $path -Algorithm SHA256).Hash.ToLower() }

$manifest = [ordered]@{
  version         = $ver
  released        = (Get-Date -Format 'yyyy-MM-dd')
  runtime_version = $PyVersion
  min_supported   = $MinSupported
  notes_url       = "$NotesBase/$ver"
  critical        = $false
  delta           = [ordered]@{
    url    = "$ReleaseBase/v$ver/$deltaName"
    sha256 = (Sha-Of $deltaPath)
    size   = (Get-Item $deltaPath).Length
  }
}
$setup = Get-ChildItem (Join-Path $PSScriptRoot 'dist') -Filter '*Setup*.exe' -EA SilentlyContinue |
  Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($setup) {
  Copy-Item $setup.FullName $Releases -Force
  $manifest['installer'] = [ordered]@{
    url    = "$ReleaseBase/v$ver/$($setup.Name)"
    sha256 = (Sha-Of $setup.FullName)
    size   = $setup.Length
  }
} else {
  Write-Host "· 인스톨러가 없어 매니페스트에 installer 항목을 넣지 않았습니다"
}

# BOM 없이 — 클라이언트가 utf-8-sig 로 방어하지만 굽는 쪽부터 깨끗하게 (version.json 과 같은 이유)
[IO.File]::WriteAllText((Join-Path $Releases 'latest.json'),
  ($manifest | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
Write-Host ("· 매니페스트: {0}" -f (Join-Path $Releases 'latest.json'))
Write-Host ""
Write-Host "  올리는 순서 (→ docs/25 릴리스 파이프라인)"
Write-Host "   1) 자산 먼저:  gh release create v$ver -R 0204yong/anfilt-releases ``"
Write-Host ("        `"{0}\*`" --title `"v{1}`"" -f $Releases, $ver)
Write-Host "   2) 매니페스트 나중:  latest.json → Company_Homepage\releases\ 커밋·푸시"
Write-Host ""
Write-Host "   ⚠️ 순서를 뒤집지 말 것. 매니페스트가 먼저 올라가면 고객은 아직 없는"
Write-Host "      파일을 받으러 가서 실패한다."

Write-Host ""
Write-Host ("빌드 완료: {0}" -f $Dist)
Write-Host ("  runtime {0} MB · app {1} MB · 합계 {2} MB" -f `
  (Size-MB $Runtime), (Size-MB $AppOut), (Size-MB $Dist))
