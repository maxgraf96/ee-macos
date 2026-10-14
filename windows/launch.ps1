<#
.SYNOPSIS
  Start/stop/inspect Empire Earth on legion's *interactive* desktop from an SSH session.

.DESCRIPTION
  OpenSSH runs our commands in session 0, which has no access to the user's desktop. To get the
  game (and helpers that need the desktop) into the logged-on user's session we register a
  scheduled task with LogonType Interactive for the user who owns explorer.exe and start it.
  Tasks live under Task Scheduler folder \EEPort\ and are removed by "cleanup".

  launch.ps1 status                          interactive session + game process info
  launch.ps1 start                           start the game (working dir = game folder)
  launch.ps1 stop [-Force]                   WM_CLOSE via session helper; -Force kills after timeout
  launch.ps1 session '<session.ps1 args>'    run session.ps1 in the user's session, print output
  launch.ps1 frida   '<run.py args>'         run frida/run.py in the user's session (hidden)
  launch.ps1 modules                         ListDLLs view of the running game
  launch.ps1 record  '<seconds> <out.wav>'   WASAPI loopback recording (loopback_record.py) in the user's session
  launch.ps1 cleanup                         unregister \EEPort\ tasks (game must not be running)
#>
param(
  [Parameter(Position = 0)][ValidateSet('status', 'start', 'stop', 'session', 'frida', 'cleanup', 'modules', 'record')]
  [string]$Action = 'status',
  [string]$Root = 'C:\EEPort',
  [int]$TimeoutSec = 60,
  [switch]$Force,
  [Parameter(Position = 1)][string]$ArgLine = ''
)
$ErrorActionPreference = 'Stop'
$GameDir = Join-Path $Root 'game\Empire Earth'
$GameExe = Join-Path $GameDir 'Empire Earth.exe'
$Jobs = Join-Path $Root 'captures\jobs'
New-Item -ItemType Directory -Force -Path $Jobs | Out-Null

function Get-InteractiveUser {
  $e = Get-Process explorer -IncludeUserName -ErrorAction SilentlyContinue | Sort-Object StartTime | Select-Object -First 1
  if (-not $e) { throw 'nobody is logged on interactively (no explorer.exe)' }
  [pscustomobject]@{ User = $e.UserName; SessionId = $e.SessionId; ExplorerPid = $e.Id }
}

function Get-Game { Get-Process -Name 'Empire Earth' -ErrorAction SilentlyContinue }

function Start-InSession([string]$Name, [string]$Exe, [string]$Arguments, [string]$WorkDir) {
  $u = Get-InteractiveUser
  $a = if ($Arguments) { New-ScheduledTaskAction -Execute $Exe -Argument $Arguments -WorkingDirectory $WorkDir }
       else { New-ScheduledTaskAction -Execute $Exe -WorkingDirectory $WorkDir }
  $p = New-ScheduledTaskPrincipal -UserId $u.User -LogonType Interactive -RunLevel Limited
  # Priority 4 = normal process priority (Task Scheduler's default 7 is below normal).
  $s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
         -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -Priority 4
  Register-ScheduledTask -TaskPath '\EEPort\' -TaskName $Name -Action $a -Principal $p -Settings $s -Force | Out-Null
  Start-ScheduledTask -TaskPath '\EEPort\' -TaskName $Name
}

# Run a PowerShell snippet hidden in the user's session; wait for it and return its output.
function Invoke-InSession([string]$Name, [string]$Body, [int]$Timeout = 120) {
  $id = '{0}-{1}' -f $Name, (Get-Date -Format 'HHmmssfff')
  $job = Join-Path $Jobs "$id.ps1"; $out = Join-Path $Jobs "$id.out"; $done = Join-Path $Jobs "$id.done"
  Set-Content -Path $job -Encoding UTF8 -Value @"
`$ErrorActionPreference = 'Continue'
try { & { $Body } *>&1 | Out-File -FilePath '$out' -Encoding UTF8 -Width 400 }
catch { `$_ | Out-File -FilePath '$out' -Append -Encoding UTF8 }
finally { Set-Content -Path '$done' -Value 'done' }
"@
  Start-InSession $Name 'wscript.exe' "//B //Nologo `"$Root\scripts\hidden.vbs`" `"$job`"" $Root
  $t0 = Get-Date
  while (-not (Test-Path $done)) {
    if (((Get-Date) - $t0).TotalSeconds -gt $Timeout) { throw "session job $id timed out" }
    Start-Sleep -Milliseconds 250
  }
  if (Test-Path $out) { Get-Content $out }
}

switch ($Action) {
  'status' {
    $u = Get-InteractiveUser
    "interactive user: $($u.User) session $($u.SessionId) (explorer pid $($u.ExplorerPid))"
    $g = @(Get-Game)
    if (-not $g) { 'game: not running' }
    foreach ($p in $g) {
      $c = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)"
      'game: pid {0} session {1} started {2} cpu {3:n1}s ws {4:n0} MB threads {5}' -f $p.Id, $p.SessionId, $p.StartTime, $p.CPU, ($p.WorkingSet64 / 1MB), $p.Threads.Count
      "  cmdline: $($c.CommandLine)"
    }
    $err = Get-Process -Name 'WerFault', 'WerFaultSecure' -ErrorAction SilentlyContinue
    if ($err) { "WARNING: WerFault running (crash dialog?): $($err.Id -join ',')" }
  }
  'start' {
    if (Get-Game) { throw 'game already running' }
    Start-InSession 'EmpireEarth' $GameExe '' $GameDir
    $t0 = Get-Date
    while (-not ($g = Get-Game)) {
      if (((Get-Date) - $t0).TotalSeconds -gt $TimeoutSec) { throw 'game process did not appear' }
      Start-Sleep -Milliseconds 200
    }
    "started pid $($g.Id) in session $($g.SessionId)"
  }
  'stop' {
    $g = Get-Game
    if (-not $g) { 'not running'; break }
    Invoke-InSession 'close' "& '$Root\scripts\session.ps1' -Action close"
    $t0 = Get-Date
    while (Get-Game) {
      if (((Get-Date) - $t0).TotalSeconds -gt $TimeoutSec) {
        if ($Force) { Stop-Process -Id $g.Id -Force; 'killed after timeout'; break }
        throw 'game still running after WM_CLOSE (use -Force to kill)'
      }
      Start-Sleep -Milliseconds 250
    }
    if (-not (Get-Game)) { 'game exited' }
  }
  'session' {
    Invoke-InSession 'session' "& '$Root\scripts\session.ps1' $ArgLine" $TimeoutSec
  }
  'frida' {
    # Start-Process (ShellExecute, hidden) so python - and a game it spawns - do not inherit the
    # job's output pipe; otherwise the job only finishes when the game exits.
    $log = Join-Path $Jobs ('frida-{0}.log' -f (Get-Date -Format 'HHmmssfff'))
    $body = "`$p = Start-Process -FilePath '$Root\tools\venv\Scripts\python.exe' -WindowStyle Hidden -PassThru " +
            "-ArgumentList '`"$Root\frida\run.py`" $ArgLine --log `"$log`"'; `$p.WaitForExit(); Get-Content '$log'"
    Invoke-InSession 'frida' $body ([Math]::Max($TimeoutSec, 600))
  }
  'record' {
    # Loopback capture needs the user's audio session (session 1). Needs soundcard + numpy in the venv.
    $body = "`$p = Start-Process -FilePath '$Root\tools\venv\Scripts\python.exe' -WindowStyle Hidden -PassThru " +
            "-ArgumentList '`"$Root\scripts\loopback_record.py`" $ArgLine'; `$p.WaitForExit(); 'exit ' + `$p.ExitCode"
    Invoke-InSession 'record' $body ([Math]::Max($TimeoutSec, 900))
  }
  'modules' {
    # ListDLLs (Sysinternals) view of the running game: path, base, size, version.
    $g = Get-Game
    if (-not $g) { throw 'game not running' }
    # Note: from this 64-bit session ListDLLs lists the WOW64 (64-bit) side; the 32-bit module
    # list with bases comes from frida/modules.js.
    & "$Root\tools\sysinternals\Listdlls.exe" -accepteula -nobanner -v $g.Id
  }
  'cleanup' {
    if (Get-Game) { throw 'game is running; stop it first' }
    Get-ScheduledTask -TaskPath '\EEPort\' -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    'removed \EEPort\ tasks'
  }
}
