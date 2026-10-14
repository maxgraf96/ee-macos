<#
.SYNOPSIS
  The M1 matrix cases that need only registry options (docs/reference/rmg.md §8: groups A, C, D),
  unattended: per case set Game Options (Map Type, Map Size, Starting Epoch), capture with
  m1_capture.ps1, pack (--verify), replay with rmg_replay.exe, append a row to matrix.md.
  Groups B (player counts) and E (Scenario Editor) need setup-screen work and are not here.

  m1_matrix.ps1 [-Only A01,A02] [-From A07] [-Out C:\EEPort\captures\rmg] [-NoteEvery 10]

  The original Game Options values are saved to <Out>\game-options-before.json and restored at the
  end, also after an error. Every -NoteEvery cases a progress note goes to the mailbox (to-mac).
#>
param(
  [string[]]$Only,
  [string]$From,
  [string]$Out = 'C:\EEPort\captures\rmg',
  [int]$NoteEvery = 10,
  [string]$Mailbox = 'C:\EEPort\workspace\mailbox'
)
$ErrorActionPreference = 'Stop'
$py = 'C:\EEPort\tools\venv\Scripts\python.exe'
$key = 'HKCU:\Software\SSSI\Empire Earth\Game Options'
$scripts = 'Continental', 'Highlands', 'Large Islands', 'Mediterranean', 'Plains', 'Small Islands', 'Tournament Islands'
$sizes = 'Tiny', 'Small', 'Medium', 'Large', 'Huge', 'Gigantic'

$cases = New-Object System.Collections.Generic.List[object]
$k = 0
foreach ($s in $scripts) { foreach ($z in 0..5) { $k++
  $cases.Add([pscustomobject]@{ id = 'A{0:d2}' -f $k; script = $s; size = $z; epoch = $null; seed = 1000 + 37 * $k }) } }
$k = 0
foreach ($seed in 0, 1, 32767, 1000000, 2000000) { $k++
  $cases.Add([pscustomobject]@{ id = 'C{0:d2}' -f $k; script = 'Highlands'; size = 1; epoch = $null; seed = $seed }) }
$k = 0
foreach ($ep in 1, $null, 13) { $k++   # first epoch, the registry default, last epoch (mapping unverified, rmg.md §8.1)
  $cases.Add([pscustomobject]@{ id = 'D{0:d2}' -f $k; script = 'Plains'; size = 2; epoch = $ep; seed = 4001 }) }
if ($Only) { $cases = @($cases | Where-Object { $Only -contains $_.id }) }
if ($From) {
  $i = 0; while ($i -lt $cases.Count -and $cases[$i].id -ne $From) { $i++ }
  if ($i -ge $cases.Count) { throw "no case $From" }
  $cases = @($cases)[$i..($cases.Count - 1)]
}

$before = Get-ItemProperty $key | Select-Object 'Map Type', 'Map Size', 'Starting Epoch'
$beforeFile = Join-Path $Out 'game-options-before.json'
if (-not (Test-Path $beforeFile)) { $before | ConvertTo-Json | Set-Content $beforeFile }
$md = Join-Path $Out 'matrix.md'
if (-not (Test-Path $md)) {
  "| Case | Script | Size | Epoch | Seed | Calls | Replay | Failing calls (first differing region) | Capture s | Pack s | Pack MB |",
  "|---|---|---|---|---|---|---|---|---|---|---|" | Set-Content $md -Encoding UTF8
}

function Note($text) {
  $n = (Get-ChildItem $Mailbox -Recurse -Filter '????-*.md' | ForEach-Object { [int]($_.Name.Substring(0, 4)) } | Measure-Object -Maximum).Maximum + 1
  $f = Join-Path $Mailbox ('to-mac\{0:d4}-m1-matrix-progress.md' -f $n)
  @("id: {0:d4}" -f $n, 'from: legion', 'to: mac', "date: $(Get-Date -Format 'yyyy-MM-dd HH:mm')", 'status: done', 're: 0012', '',
    '# M1 matrix progress (automatic note)', '', $text, '', "Table: ``$md``.") | Set-Content $f -Encoding UTF8
}

$done = 0
try {
  foreach ($c in $cases) {
    $dir = Join-Path $Out $c.id
    if (Test-Path $dir) { "skip $($c.id): $dir exists"; continue }
    Get-Process 'Empire Earth' -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Seconds 2
    Set-ItemProperty $key -Name 'Map Type' -Value $c.script
    Set-ItemProperty $key -Name 'Map Size' -Value $c.size -Type DWord
    Set-ItemProperty $key -Name 'Starting Epoch' -Value $(if ($null -ne $c.epoch) { $c.epoch } else { $before.'Starting Epoch' }) -Type DWord
    "== $($c.id) $($c.script) $($sizes[$c.size]) seed $($c.seed) $(Get-Date -Format 'HH:mm:ss')"
    $t0 = Get-Date
    $log = & "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File C:\EEPort\scripts\m1_capture.ps1 -Case $c.id -Seed $c.seed -Out $Out 2>&1 | Out-String
    $tc = [int]((Get-Date) - $t0).TotalSeconds
    $log | Set-Content (Join-Path $Out "$($c.id).run.log")
    $calls = 0; try { $calls = (Get-Content (Join-Path $dir 'rmg_capture.json') -Raw | ConvertFrom-Json).stats.calls } catch { }
    $verdict = 'no capture'; $fails = ''; $tp = 0; $mb = 0
    if ($calls -gt 0) {
      $t1 = Get-Date
      & $py -I C:\EEPort\m1\replay\rmg_pack.py pack $dir -o (Join-Path $dir 'packs') --verify 2>&1 | Set-Content (Join-Path $dir 'pack.log')
      $tp = [int]((Get-Date) - $t1).TotalSeconds
      $mb = [math]::Round(((Get-ChildItem (Join-Path $dir 'packs') -Filter *.rrp | Measure-Object Length -Sum).Sum / 1MB), 1)
      $rep = & C:\EEPort\tools\rmg_replay.exe --game 'C:\EEPort\game-m1\Empire Earth' --diff-dir (Join-Path $dir 'diff') (Join-Path $dir 'packs\*.rrp') 2>&1
      $rep | Set-Content (Join-Path $dir 'replay.txt')
      $verdict = ($rep | Where-Object { $_ -match 'packs passed' } | Select-Object -Last 1) -replace ' packs passed', ''
      $fails = (($rep | Where-Object { $_ -match '^(MISMATCH|ERROR)' } | ForEach-Object {
        if ($_ -match 'MISMATCH seq=(\d+) EERMPIM(\w+): region \d+ (\S+)') { "$($matches[1]) $($matches[2]) $($matches[3])" } else { $_.Substring(0, [Math]::Min(70, $_.Length)) } }) | Select-Object -Unique) -join '; '
    }
    "| $($c.id) | $($c.script) | $($sizes[$c.size]) | $(if ($null -ne $c.epoch) { $c.epoch } else { 'reg' }) | $($c.seed) | $calls | $verdict | $fails | $tc | $tp | $mb |" | Add-Content $md -Encoding UTF8
    "   calls $calls replay $verdict  $fails"
    $done++
    if ($NoteEvery -and ($done % $NoteEvery) -eq 0) { Note "$done cases done in this run, the last one $($c.id). Latest rows:`n`n$((Get-Content $md | Select-Object -Last $NoteEvery) -join "`n")" }
  }
} finally {
  Get-Process 'Empire Earth' -ErrorAction SilentlyContinue | Stop-Process -Force
  Set-ItemProperty $key -Name 'Map Type' -Value $before.'Map Type'
  Set-ItemProperty $key -Name 'Map Size' -Value $before.'Map Size' -Type DWord
  Set-ItemProperty $key -Name 'Starting Epoch' -Value $before.'Starting Epoch' -Type DWord
  "restored Game Options: $((Get-ItemProperty $key | Select-Object 'Map Type', 'Map Size', 'Starting Epoch') | ConvertTo-Json -Compress)"
}
