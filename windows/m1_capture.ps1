<#
.SYNOPSIS
  One M1 random-map capture on legion, unattended (docs/reference/rmg.md §9; mailbox 0006/0007/0012).

  Spawns the rebased game copy with frida\base_guard.js (keeps the plugin DLLs at the M1 base map),
  skips the intro, attaches frida\rmg_capture.js at the main menu, clicks Single Player -> Play
  Random Map -> Start Game with the registry's current game options, waits until rmg_capture writes
  its stop file, then stops the guard and kills the game. Grabs a screenshot at each step into the
  capture directory. Run it in the interactive session (Windows PowerShell 5.1).

  m1_capture.ps1 -Case S00 -Seed 1000 [-Out C:\EEPort\captures\rmg] [-GameDir ...] [-NoStart]

  The mouse moves are relative and were measured on legion (x gain ~0.25, y gain ~2 per unit,
  cursor parked by the game on "Single Player" after the intro). Check the grabs if a step fails.
#>
param(
  [Parameter(Mandatory)][string]$Case,
  [Parameter(Mandatory)][int]$Seed,
  [string]$Out = 'C:\EEPort\captures\rmg',
  [string]$GameDir = 'C:\EEPort\game-m1\Empire Earth',
  [int]$TimeoutSec = 600,
  [switch]$NoStart          # stop at the Game Setup screen (for cases that change setup there)
)
$ErrorActionPreference = 'Stop'
$py = 'C:\EEPort\tools\venv\Scripts\python.exe'
$dir = Join-Path $Out $Case
if (Test-Path $dir) { throw "$dir exists" }
New-Item -ItemType Directory -Force $dir | Out-Null
function S($a) { & "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "& C:\EEPort\scripts\session.ps1 $a" }
function Grab($n) { S "-Action grab -Out `"$dir\$n.jpg`" -Scale 1280" | Out-Null }
if (Get-Process 'Empire Earth' -ErrorAction SilentlyContinue) { throw 'a game is already running' }

# 1. spawn with the base guard (runs until GUARD_STOP)
$guard = Start-Process $py -WindowStyle Hidden -PassThru -ArgumentList @('-I', 'C:\EEPort\frida\run.py', '--spawn',
  '--exe', "`"$GameDir\Empire Earth.exe`"", '--scripts', 'base_guard,modules', '--seconds', '3600', '--every', '30',
  '--stop-file', "`"$dir\GUARD_STOP`"", '--out', "`"$dir\guard`"", '--log', "`"$dir\guard.log`"")
$t0 = Get-Date
while (-not ($g = Get-Process 'Empire Earth' -ErrorAction SilentlyContinue)) {
  if (((Get-Date) - $t0).TotalSeconds -gt 60) { throw 'game did not start' }; Start-Sleep -Milliseconds 500 }
Start-Sleep -Seconds 4
foreach ($i in 1..4) { S '-Action key -Keys ESC' | Out-Null; Start-Sleep -Seconds 3 }
Start-Sleep -Seconds 4; Grab '1-menu'

# 2. attach the capture at the main menu
$cap = Start-Process $py -WindowStyle Hidden -PassThru -ArgumentList @('-I', 'C:\EEPort\frida\run.py', '--pid', $g.Id,
  '--scripts', 'modules,rmg_capture', '--seconds', $TimeoutSec, '--out', "`"$dir`"", '--param', "case=$Case",
  '--param', "seed=$Seed", '--param', "`"stop_file=$dir\STOP`"", '--stop-file', "`"$dir\STOP`"", '--log', "`"$dir\capture.log`"")
Start-Sleep -Seconds 5

# 3. Single Player -> Play Random Map -> Start Game
S '-Action click -Here' | Out-Null; Start-Sleep -Seconds 4
foreach ($m in @('430 -112', '300 40', '800 -30', '0 -12')) { $xy = $m -split ' '; S "-Action move -X $($xy[0]) -Y $($xy[1])" | Out-Null; Start-Sleep -Milliseconds 600 }
Grab '2-random-map'
S '-Action click -Here' | Out-Null; Start-Sleep -Seconds 4
foreach ($m in @('0 400', '0 -60', '0 40')) { $xy = $m -split ' '; S "-Action move -X $($xy[0]) -Y $($xy[1])" | Out-Null; Start-Sleep -Milliseconds 600 }
Grab '3-setup'
if ($NoStart) { "at Game Setup; finish by hand, then: touch $dir\GUARD_STOP"; return }
S '-Action click -Here' | Out-Null
"start clicked $(Get-Date -Format 'HH:mm:ss')"

# 4. wait for rmg_capture's stop file (or the run to end), then tear down
$cap.WaitForExit($TimeoutSec * 1000 + 60000) | Out-Null
Start-Sleep -Seconds 2; Grab '4-ingame'
New-Item -ItemType File -Force "$dir\GUARD_STOP" | Out-Null
$guard.WaitForExit(60000) | Out-Null
Get-Process 'Empire Earth' -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Content "$dir\capture.log"
& $py -I -c "import json,sys; c=json.load(open(sys.argv[1])); g=json.load(open(sys.argv[2])); print('calls', c['stats']['calls'], 'unhooked', list(c.get('unhooked', {})), 'warnings', len(c['warnings']), '| guard loads', g['loads'], 'at base', g['loads_at_base'])" "$dir\rmg_capture.json" "$dir\guard\base_guard.json"
