# Build the Windows AG CuteMusic: a folder with everything in it (Python, PyQt6, GStreamer from its own Python
# wheels), a zip of it, and an installer (Inno Setup, per user: no admin rights).
#
#   powershell -ExecutionPolicy Bypass -File windows\make-standalone.ps1
#
# Needs Python 3.10+ (64-bit, python.org) and, for the installer, Inno Setup 6 (iscc.exe; choco install innosetup).
# The GitHub workflow (.github/workflows/windows.yml) runs it on windows-latest.
#
#   build\windows-env              the build venv
#   dist\windows\AG CuteMusic\      the app (AG CuteMusic.exe; gstreamer\ next to it)
#   dist\AG-CuteMusic-<version>-windows.zip
#   dist\AG-CuteMusic-<version>-setup.exe
#
# Environment overrides: PYTHON (the python to build with, default: py -3 / python), NO_INSTALLER=1.

$ErrorActionPreference = 'Stop'
$Root = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $Root

$Init = Get-Content 'cutemusic\__init__.py' -Raw
$AppName = [regex]::Match($Init, "(?m)^APP_NAME = '(.*)'").Groups[1].Value
$AppId = [regex]::Match($Init, "(?m)^APP_ID = '(.*)'").Groups[1].Value
$Version = [regex]::Match($Init, "(?m)^__version__ = '(.*)'").Groups[1].Value
Write-Host "== $AppName $Version"

function Run([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') failed ($LASTEXITCODE)" }
}

$Python = $env:PYTHON
if (-not $Python) { $Python = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' } }
$PythonArgs = if ($Python -eq 'py') { @('-3') } else { @() }

Write-Host '== build venv (build\windows-env)'
New-Item -ItemType Directory -Force build | Out-Null
if (-not (Test-Path 'build\windows-env\Scripts\python.exe')) {
    Run $Python ($PythonArgs + @('-m', 'venv', 'build\windows-env'))
}
$Py = (Resolve-Path 'build\windows-env\Scripts\python.exe').Path
Run $Py @('-m', 'pip', 'install', '--upgrade', '--quiet', 'pip')
Run $Py @('-m', 'pip', 'install', '--upgrade', '--quiet', '-r', 'windows\requirements.txt')
Run $Py @('-c', 'import sys; assert sys.maxsize > 2**32, sys.maxsize; print(sys.version.split()[0])')     # 64-bit

Write-Host '== every module compiles, and imports only modules that exist'
Run $Py @('-m', 'compileall', '-q', 'cutemusic', 'windows')

Write-Host '== icon'
Run $Py @('windows\make_ico.py', "icons\$AppId.svg", 'build\app.ico')

Write-Host '== PyInstaller'
$Dist = Join-Path $Root 'dist\windows'
if (Test-Path $Dist) { Remove-Item -Recurse -Force $Dist }
$env:APP_ICO = Join-Path $Root 'build\app.ico'
Run $Py @('-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN', '--distpath', $Dist,
          '--workpath', 'build\pyinstaller', 'windows\ag-cute-music.spec')
$App = Join-Path $Dist $AppName
$Exe = Join-Path $App "$AppName.exe"
if (-not (Test-Path $Exe)) { throw "no $Exe" }

Write-Host '== GStreamer into the app (gstreamer\)'
$Site = & $Py -c 'import gstreamer_libs, os; print(os.path.dirname(os.path.dirname(gstreamer_libs.__file__)))'
$Target = Join-Path $App 'gstreamer'
New-Item -ItemType Directory -Force $Target | Out-Null
# as the Mac app (macos/make-standalone.sh), and on Windows the C runtime the DLLs need (gstreamer_ext_runtime)
foreach ($Package in 'gstreamer_libs', 'gstreamer_plugins', 'gstreamer_plugins_libs', 'gstreamer_plugins_restricted',
                     'gstreamer_plugins_gpl', 'gstreamer_plugins_gpl_restricted', 'gstreamer_python', 'gstreamer_ext_runtime') {
    $Source = Join-Path $Site $Package
    if (Test-Path $Source) {
        Copy-Item -Recurse -Force $Source (Join-Path $Target $Package)
        Write-Host "  $Package"
    }
}
Get-ChildItem -Recurse -Directory -Filter __pycache__ $Target | Remove-Item -Recurse -Force
# not used by the player: the CUDA, Direct3D 12 and validation tools, headers and static libraries
foreach ($Pattern in '*.lib', '*.h', '*.a', '*.pdb') {
    Get-ChildItem -Recurse -File -Filter $Pattern $Target -ErrorAction SilentlyContinue | Remove-Item -Force
}
foreach ($Folder in 'include', 'share\gst-validate', 'lib\gst-validate-launcher', 'share\gtk-doc', 'share\doc') {
    Get-ChildItem -Recurse -Directory $Target -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName.EndsWith('\' + $Folder) } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}
$Size = (Get-ChildItem -Recurse -File $App | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("  the app: {0:N0} MB" -f $Size)

Write-Host '== self test'
$Report = Join-Path $Root 'build\selftest.txt'
if (Test-Path $Report) { Remove-Item $Report }
$env:AG_CUTEMUSIC_SELFTEST = $Report
$Process = Start-Process -FilePath $Exe -PassThru
$null = $Process.Handle         # (held: the exit code is lost otherwise)
if (-not $Process.WaitForExit(300000)) { $Process.Kill(); throw 'self test: no answer in 5 minutes' }
$Code = $Process.ExitCode
Remove-Item Env:AG_CUTEMUSIC_SELFTEST
if (Test-Path $Report) { Get-Content $Report | ForEach-Object { Write-Host "  $_" } }
if ($Code -ne 0 -or -not (Test-Path $Report)) { throw "self test failed (exit $Code)" }

Write-Host '== zip'
$Zip = Join-Path $Root "dist\AG-CuteMusic-$Version-windows.zip"
if (Test-Path $Zip) { Remove-Item $Zip }
Compress-Archive -Path $App -DestinationPath $Zip -CompressionLevel Optimal
Write-Host "  $Zip"

if ($env:NO_INSTALLER) { return }
Write-Host '== installer (Inno Setup)'
$Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $Iscc) {
    foreach ($Candidate in "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                           "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") {
        if (Test-Path $Candidate) { $Iscc = $Candidate; break }
    }
}
if (-not $Iscc) { Write-Host '  no Inno Setup (iscc.exe): no installer, the zip is there'; return }
Run $Iscc @('/Q', "/DAppName=$AppName", "/DAppId=$AppId", "/DAppVersion=$Version", "/DAppDir=$App",
            "/DOutputDir=$(Join-Path $Root 'dist')", "/DIconFile=$(Join-Path $Root 'build\app.ico')", 'windows\installer.iss')
Write-Host "  $(Join-Path $Root "dist\AG-CuteMusic-$Version-setup.exe")"
