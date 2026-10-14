<#
.SYNOPSIS
  Prepare the Windows reference machine ("legion") for Empire Earth reference captures.

.DESCRIPTION
  Idempotent. Everything lives under $Root (default C:\EEPort) except:
    - Python 3.12 (winget, user scope: %LOCALAPPDATA%\Programs\Python\Python312)
    - registry: HKCU\Software\SSSI\Empire Earth (+ \Game Options), and the two HKLM keys a
      GOG install of the *base game* writes (WOW6432Node\Stainless Steel Studios\Empire Earth,
      WOW6432Node\Sierra\CDKeys "EmpireEarth"), and Sysinternals EULA flags in HKCU.
  It never touches firewall, Defender, UAC, drivers or Windows features, and never reboots.

  Expected layout (deploy.sh puts it there):
    $Root\game\Empire Earth\...                 the GOG base-game folder (copied from the Mac)
    $Root\game\goggame-1207658777.script        GOG's install script (registry actions, CD key)
    $Root\scripts\, $Root\frida\                this repo's windows/ folder

.PARAMETER Width
  Game resolution written to the registry. The Wine-derived value was 1728x1080; on legion
  (2560x1600 panel, GOG ddraw wrapper in windowed/fit mode) we use 1920x1080 by default.

.PARAMETER ResetRegistry
  Re-apply all registry values even if the SSSI key already exists (the game rewrites its
  options on exit, so by default an existing key is left alone).
#>
[CmdletBinding()]
param(
  [string]$Root = 'C:\EEPort',
  [int]$Width = 1920,
  [int]$Height = 1080,
  [switch]$ResetRegistry,
  [switch]$SkipTools,
  [switch]$WithX64dbg
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$GameDir  = Join-Path $Root 'game\Empire Earth'
$AppDir   = Join-Path $Root 'game'          # GOG's {app}
$ToolsDir = Join-Path $Root 'tools'
$Venv     = Join-Path $ToolsDir 'venv'
$PyPins   = @('frida==17.23.3', 'frida-tools==14.11.0', 'pefile==2024.8.26')

function Say($m) { Write-Host "[setup] $m" }

# ---------------------------------------------------------------- 1. directories
foreach ($d in @('game', 'tools', 'captures', 'scripts', 'frida')) {
  New-Item -ItemType Directory -Force -Path (Join-Path $Root $d) | Out-Null
}

# ---------------------------------------------------------------- 2. hashes (PLAN.md 2.4)
$Canonical = [ordered]@{
  'Empire Earth.exe'             = '957e35d75ba80309d094cbfb7367f7c6d61c5fd19fc5f7ca3f2e1cf636a065ab'
  'Low-Level Engine.dll'         = 'e3f6a2f6d1ec5c3144f7cdd918cf54166de86547d4bbb6b3c9177d3eeb003214'
  'sxlrt233.dll'                 = 'ccf6f97d21bdf07abfb709a19fd7e75c585a972bada6341d61952b4700b4440b'
  'Language.dll'                 = '2539f8b76ee3a6c6b6e03e5ce76d43e891c8b41533d058e249feea5ca8ca3456'
  'DX7HRDisplay.dll'             = '46a69ac011e9a934b1edd484a3f91f3482a5c85a3d47a44eddd1e4545746ff53'
  'DX7HRTnLDisplay.dll'          = 'f0c5b16a7db0409e7915af7ff6ded3ac61eb29835f557aad5c5a68b37085b154'
  'Default.dll'                  = 'ab0e791039e558ac93f250594003995a4037883864cbdbbb6ded6f8faa4f9d3e'
  'TCP Protocol.dll'             = 'b695886b01f63b7f699d204e89f38fcd03fb374a543866ed898381a194c170f0'
  'Miles Sound System Mixer.dll' = '6e1a5145d31dfe325d2c7c5b62daaa49af233bb24fede22c56bc66ac11265e09'
  'Mss32.dll'                    = 'd0db426ebac97dc410b5130c9c74d19bc04151236df85b012823d6cb3d7fb87c'
  'binkw32.dll'                  = '7ef2f1d2b6d833bcc3bea2359975e747b84e166bebfdf92e6a2a7389b8f2c0e6'
}
# GOG additions (not canonical game code, but part of the setup Windows friends run)
$GogExtras = [ordered]@{
  'ddraw.dll' = 'ec5ab4c9d737b18fc9842ea05382a08c54a032d6a608173369645e9cb0040a8a'
  'dxcfg.exe' = 'b30a63708da66067675667d596cf1b6dc1f9de7a018eca529647532c51d68280'
}
if (-not (Test-Path (Join-Path $GameDir 'Empire Earth.exe'))) {
  throw "Game folder missing: $GameDir (run windows/deploy.sh on the Mac first)"
}
$bad = 0
foreach ($set in @($Canonical, $GogExtras)) {
  foreach ($f in $set.Keys) {
    $h = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $GameDir $f)).Hash.ToLower()
    $ok = ($h -eq $set[$f])
    if (-not $ok) { $bad++ }
    Say ('{0} {1,-30} {2}' -f $(if ($ok) { 'OK  ' } else { 'FAIL' }), $f, $h)
  }
}
if ($bad) { throw "$bad module hash mismatch(es)" }

# ---------------------------------------------------------------- 3. registry
# Values the game wrote under Wine on the Mac (original/gog/sssi-wine-user.reg), converted.
# Install paths follow GOG's script: Installed From Volume = {app}, Directory = \Empire Earth\.
$Sssi = [ordered]@{
  'Ambient Sounds' = 1; 'Animation Smoothing' = 1; 'AutoSave In Milliseconds' = 0x927c0
  'Button MIP Levels to Drop' = 0; 'Computer Player Difficulty Level' = 1
  'CT Send Interval Step Down' = 0x0a; 'CT Send Interval Step Up' = 0x7d; 'Debug OOS Handling' = 1
  'Display Clouds' = 1; 'Display Shadows' = 1; 'Effect Detail' = 2; 'Enable Animal Herds' = 1
  'Game Bit Depth' = 32; 'Game Window Height' = $Height; 'Game Window Width' = $Width
  'Grab Scroll Speed' = 0x11; 'Host Game Name' = 'Unknown'
  'Installed From Directory' = '\Empire Earth\'; 'Installed From Volume' = $AppDir
  'Last Auto Save' = ''; 'Last Game File' = ''; 'Last Open Post Game Stats Page' = 1
  'Last Quick Save' = ''; 'Last Scenario File' = ''; 'Max CT Send Interval' = 0x8ca
  'Max Number Of Sounds (percent)' = 100; 'Max PTs per Second' = 0x7d; 'Min PTs per Second' = 0x0f
  'MIP Levels to Drop' = 0; 'Model Detail' = 2; 'Mouse Sensitivity' = 0x1f; 'Music Enabled' = 1
  'Music Volume' = 0x3e; 'Network Adapter' = 0; 'Number Of AutoSaves' = 10
  'Player Name' = 'Max the Conqueror'; 'PT Rate Tuning Threshold (percent)' = 0x1e
  'Rasterizer Name' = 'Direct3D'; 'Reverb Enabled' = 1; 'Right Hand Mouse Orientation' = 1
  'Screen ID' = 0; 'Show Auto Waypoints' = 0; 'Show Blood' = 1; 'Side Scroll Speed' = 0xc8
  'Sierra.com Game Name' = 'Unknown'; 'Sierra.com User ID' = 'Unknown'; 'Sound Volume' = 100
  'Take End Game Screenshots' = 0; 'Take JPG Screenshots' = 1; 'Terrain MIP Levels to Drop' = 0
  'Texture Bit Depth' = 32; 'Trilinear Filter' = 0; 'Unit Lighting' = 0; 'Unit Scale Factor' = 100
  'UseAreaEffectOverlays' = 1; 'UseCandidateWindow' = 0; 'Wait for VSync' = 1
  'WonNet Password' = ''; 'Zoom Lock' = 100; 'ZoomStyle' = 0
}
$GameOptions = [ordered]@{
  'Allow Custom Civs' = 0; 'Cheat Codes' = 1; 'Difficulty Level' = 1; 'Ending Epoch' = 0x0d
  'Game Speed' = 1; 'Game Unit Limit' = 0x1f4; 'Game Variant' = 0; 'Lock Speed' = 0; 'Lock Teams' = 0
  'Map Size' = 1; 'Map Type' = 'Continental'; 'Record Game' = 0; 'Reveal Map' = 0
  'Starting Epoch' = 0x0e; 'Starting Resources' = 4; 'Wonders For Victory' = 3
}

function Set-Values($path, $values) {
  New-Item -Path $path -Force | Out-Null
  foreach ($k in $values.Keys) {
    $v = $values[$k]
    if ($v -is [int]) { New-ItemProperty -Path $path -Name $k -Value $v -PropertyType DWord -Force | Out-Null }
    else { New-ItemProperty -Path $path -Name $k -Value ([string]$v) -PropertyType String -Force | Out-Null }
  }
}

$SssiKey = 'HKCU:\Software\SSSI\Empire Earth'
if ($ResetRegistry -or -not (Test-Path $SssiKey)) {
  Say "writing $SssiKey (+ Game Options), resolution ${Width}x${Height}"
  Set-Values $SssiKey $Sssi
  Set-Values "$SssiKey\Game Options" $GameOptions
} else {
  Say "$SssiKey exists; leaving it (use -ResetRegistry to overwrite)"
}

# HKLM keys from GOG's install script, base game only (skip Mad Doc / AoC / EEAOC).
# GOG's installer is 32-bit, so its HKLM writes land in WOW6432Node (as in the Wine prefix).
$Script = Join-Path $AppDir 'goggame-1207658777.script'
if (Test-Path $Script) {
  $actions = (Get-Content -Raw $Script | ConvertFrom-Json).actions
  foreach ($a in $actions) {
    $arg = $a.install.arguments
    if ($a.install.action -ne 'setRegistry' -or $arg.root -ne 'HKLM') { continue }
    if ($arg.subkey -like '*Mad Doc*' -or $arg.valueName -eq 'EEAOC') { continue }
    $sub = $arg.subkey -replace '^Software\\', 'SOFTWARE\WOW6432Node\'
    $path = "HKLM:\$sub"
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null; Say "created $path" }
    if ($arg.valueName) {
      if ($arg.valueType -eq 'binary') {
        $bytes = [Convert]::FromBase64String($arg.valueData)
        New-ItemProperty -Path $path -Name $arg.valueName -Value $bytes -PropertyType Binary -Force | Out-Null
      } else {
        New-ItemProperty -Path $path -Name $arg.valueName -Value $arg.valueData -PropertyType String -Force | Out-Null
      }
      Say "set $path :: $($arg.valueName) ($($arg.valueType))"
    }
  }
} else {
  Say "WARNING: $Script not found; HKLM GOG keys (CD key) not written"
}
# Not applied on purpose: GOG's directplay.cmd (DISM feature, may need reboot; base EE does not
# import dplay) and firewall.cmd (inbound allow rule; see docs/reference/legion.md).
& reg.exe export 'HKCU\Software\SSSI' (Join-Path $Root 'captures\registry-sssi.reg') /y | Out-Null

if ($SkipTools) { Say 'skipping tools'; return }

# ---------------------------------------------------------------- 4. Python + Frida
$Py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
if (-not (Test-Path $Py)) {
  Say 'installing Python 3.12 (winget, user scope)'
  & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements `
      --accept-source-agreements --source winget --disable-interactivity
  if (-not (Test-Path $Py)) { throw "Python not found at $Py after winget" }
}
if (-not (Test-Path (Join-Path $Venv 'Scripts\python.exe'))) {
  Say "creating venv $Venv"
  & $Py -m venv $Venv
}
$VPy = Join-Path $Venv 'Scripts\python.exe'
& $VPy -m pip install --disable-pip-version-check -q --upgrade pip
& $VPy -m pip install --disable-pip-version-check -q @PyPins
Say ('frida ' + (& $VPy -c 'import frida; print(frida.__version__)'))

# ---------------------------------------------------------------- 5. Sysinternals (portable)
$Sys = Join-Path $ToolsDir 'sysinternals'
New-Item -ItemType Directory -Force -Path $Sys | Out-Null
foreach ($z in @('ListDlls', 'ProcessMonitor', 'ProcessExplorer', 'PSTools', 'Handle', 'VMMap')) {
  $marker = Join-Path $Sys ".$z.done"
  if (Test-Path $marker) { continue }
  $zip = Join-Path $env:TEMP "$z.zip"
  Say "downloading Sysinternals $z"
  Invoke-WebRequest -UseBasicParsing -Uri "https://download.sysinternals.com/files/$z.zip" -OutFile $zip
  Expand-Archive -LiteralPath $zip -DestinationPath $Sys -Force
  Remove-Item $zip
  Set-Content -Path $marker -Value (Get-Date -Format o)
}
# Pre-accept EULAs (HKCU only) so the tools never block on a dialog in a non-interactive session.
foreach ($t in @('ListDLLs', 'Process Monitor', 'Process Explorer', 'PsExec', 'PsList', 'PsKill', 'Handle', 'VMMap')) {
  New-Item -Path "HKCU:\Software\Sysinternals\$t" -Force | Out-Null
  New-ItemProperty -Path "HKCU:\Software\Sysinternals\$t" -Name EulaAccepted -Value 1 -PropertyType DWord -Force | Out-Null
}

# ---------------------------------------------------------------- 6. ffmpeg (portable; screen grabs)
# The GOG wrapper presents via D3D9 flip/fullscreen, so GDI screenshots are black or stale;
# session.ps1 -Action grab uses ffmpeg's ddagrab (Desktop Duplication API) instead.
$Ff = Join-Path $ToolsDir 'ffmpeg'
if (-not (Test-Path (Join-Path $Ff 'bin\ffmpeg.exe'))) {
  Say 'downloading ffmpeg (BtbN win64 gpl build)'
  $zip = Join-Path $env:TEMP 'ffmpeg.zip'
  Invoke-WebRequest -UseBasicParsing -OutFile $zip `
    -Uri 'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
  $tmp = Join-Path $ToolsDir 'ffmpeg-tmp'
  Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
  Move-Item (Get-ChildItem $tmp | Select-Object -First 1).FullName $Ff
  Remove-Item $tmp, $zip -Recurse
}

# ---------------------------------------------------------------- 7. x64dbg (optional, portable)
if ($WithX64dbg) {
  $X = Join-Path $ToolsDir 'x64dbg'
  if (-not (Test-Path (Join-Path $X 'release\x32\x32dbg.exe'))) {
    $rel = Invoke-RestMethod -UseBasicParsing -Uri 'https://api.github.com/repos/x64dbg/x64dbg/releases/latest' `
             -Headers @{ 'User-Agent' = 'eeport-setup' }
    $asset = $rel.assets | Where-Object { $_.name -like 'snapshot_*.zip' } | Select-Object -First 1
    Say "downloading x64dbg $($asset.name)"
    $zip = Join-Path $env:TEMP $asset.name
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip
    Expand-Archive -LiteralPath $zip -DestinationPath $X -Force
    Remove-Item $zip
  }
}

# pktmon is built in (C:\Windows\System32\PktMon.exe); nothing to install. No Npcap/Wireshark.
Say 'done'
