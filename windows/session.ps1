<#
.SYNOPSIS
  Helpers that must run *inside the interactive desktop session* (windows, screenshots, input,
  clean close). launch.ps1 runs this via a scheduled task + hidden.vbs; do not call it from SSH
  directly (SSH runs in session 0, which cannot see or drive the user's desktop).

  session.ps1 -Action windows    [-ProcessName 'Empire Earth'] -Out file.json
  session.ps1 -Action screenshot -Out file.png [-Mode window|screen]  (game window via PrintWindow,
                                                  or the screen area under it via GDI)
  session.ps1 -Action grab       -Out file.jpg [-Scale 1280]  what is really on screen (Desktop
                                                  Duplication via ffmpeg ddagrab; works with the
                                                  wrapper's flip/fullscreen presentation, GDI does not)
  session.ps1 -Action close      [-ProcessName ...]      posts WM_CLOSE to the game's top-level windows
  session.ps1 -Action focus      [-ProcessName ...]
  session.ps1 -Action click      -X 100 -Y 200 [-Relative] [-Right]   (Relative = game client coords)
  session.ps1 -Action move       -X 200 -Y -50          relative mouse motion (raw deltas, in -Step chunks):
                                                  EE draws its own cursor from DirectInput deltas, so
                                                  absolute SetCursorPos does not move it
  session.ps1 -Action click      -Here [-Right]          click wherever the game cursor is
  session.ps1 -Action idle                               ms since the last input in the session + foreground window
  session.ps1 -Action key        -Keys 'ESC,ENTER'       scan-code key presses (DirectInput sees them)
  session.ps1 -Action type       -Text 'hello'           unicode text into the focused window
#>
param(
  [Parameter(Mandatory)][ValidateSet('windows', 'screenshot', 'grab', 'close', 'focus', 'click', 'move', 'key', 'type', 'idle')]
  [string]$Action,
  [string]$ProcessName = 'Empire Earth',
  [string]$Out,
  [int]$X, [int]$Y, [switch]$Relative, [switch]$Right, [switch]$Here, [int]$Step = 10,
  [string]$Keys, [string]$Text, [int]$DelayMs = 120,
  [ValidateSet('screen', 'window')][string]$Mode = 'window',
  [int]$Scale = 0, [int]$Output = 0
)
$ErrorActionPreference = 'Stop'

Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class EEWin {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
  [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr v);
  [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint cmd);
  [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int idx);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [StructLayout(LayoutKind.Sequential)] public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
  [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO li);
  public static uint IdleMs() { var li = new LASTINPUTINFO(); li.cbSize = 8; GetLastInputInfo(ref li); return (uint)Environment.TickCount - li.dwTime; }
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out int v, int size);
  public static int Cloaked(IntPtr h) { int v = 0; DwmGetWindowAttribute(h, 14, out v, 4); return v; }

  [StructLayout(LayoutKind.Sequential)] public struct MOUSEINPUT { public int dx, dy; public uint data, flags, time; public IntPtr extra; }
  [StructLayout(LayoutKind.Sequential)] public struct KEYBDINPUT { public ushort vk, scan; public uint flags, time; public IntPtr extra; }
  [StructLayout(LayoutKind.Explicit)] public struct INPUT {
    [FieldOffset(0)] public uint type;
    [FieldOffset(8)] public MOUSEINPUT mi;   // 64-bit layout (PowerShell is 64-bit)
    [FieldOffset(8)] public KEYBDINPUT ki;
  }
  [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] i, int size);

  public static List<IntPtr> TopWindows(uint pid) {
    var list = new List<IntPtr>();
    EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid) list.Add(h); return true; }, IntPtr.Zero);
    return list;
  }
  public static string Text(IntPtr h) { var s = new StringBuilder(512); GetWindowText(h, s, 512); return s.ToString(); }
  public static string Cls(IntPtr h) { var s = new StringBuilder(256); GetClassName(h, s, 256); return s.ToString(); }
  static void Send(INPUT i) { SendInput(1, new INPUT[] { i }, Marshal.SizeOf(typeof(INPUT))); }
  public static void MouseButton(bool right, bool down) {
    var i = new INPUT(); i.type = 0;
    i.mi.flags = right ? (down ? 0x0008u : 0x0010u) : (down ? 0x0002u : 0x0004u);
    Send(i);
  }
  public static void MouseRel(int dx, int dy) { var i = new INPUT(); i.type = 0; i.mi.dx = dx; i.mi.dy = dy; i.mi.flags = 0x0001; Send(i); }
  public static void Key(ushort scan, bool extended, bool up) {
    var i = new INPUT(); i.type = 1; i.ki.scan = scan;
    i.ki.flags = 0x0008u | (extended ? 0x0001u : 0u) | (up ? 0x0002u : 0u);
    Send(i);
  }
  public static void Unicode(char c, bool up) {
    var i = new INPUT(); i.type = 1; i.ki.scan = c; i.ki.flags = 0x0004u | (up ? 0x0002u : 0u); Send(i);
  }
}
'@
# Per-monitor DPI aware so coordinates and screenshots are in physical pixels.
[void][EEWin]::SetProcessDpiAwarenessContext([IntPtr](-4))

function Get-GameWindows {
  $procs = @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue)
  foreach ($p in $procs) {
    foreach ($h in [EEWin]::TopWindows([uint32]$p.Id)) {
      $r = New-Object EEWin+RECT; [void][EEWin]::GetWindowRect($h, [ref]$r)
      $c = New-Object EEWin+RECT; [void][EEWin]::GetClientRect($h, [ref]$c)
      $pt = New-Object EEWin+POINT; [void][EEWin]::ClientToScreen($h, [ref]$pt)
      [pscustomobject]@{
        pid = $p.Id; hwnd = ('0x{0:x}' -f $h.ToInt64()); visible = [EEWin]::IsWindowVisible($h)
        class = [EEWin]::Cls($h); title = [EEWin]::Text($h)
        rect = @($r.L, $r.T, $r.R, $r.B); client = @($pt.X, $pt.Y, $c.R, $c.B)
        foreground = ([EEWin]::GetForegroundWindow() -eq $h); dpi = [EEWin]::GetDpiForWindow($h)
        style = ('0x{0:x8}' -f [EEWin]::GetWindowLong($h, -16)); exstyle = ('0x{0:x8}' -f [EEWin]::GetWindowLong($h, -20))
        iconic = [EEWin]::IsIconic($h); cloaked = [EEWin]::Cloaked($h)
        responding = $p.Responding; h = $h
      }
    }
  }
}
function Main-Window { Get-GameWindows | Where-Object { $_.visible } | Sort-Object { - ($_.client[2] * $_.client[3]) } | Select-Object -First 1 }

# Input actions only ever go to the game: bring it to the front (ALT tap lifts the foreground
# lock) and refuse to send anything if another window (e.g. a VM console) keeps the focus.
function Ensure-Foreground($mw) {
  if (-not $mw) { throw 'no game window; not sending input' }
  for ($i = 0; $i -lt 3; $i++) {
    if ([EEWin]::GetForegroundWindow() -eq $mw.h) { return }
    [EEWin]::Key(0x38, $false, $false); [EEWin]::Key(0x38, $false, $true)
    if ([EEWin]::IsIconic($mw.h)) { [void][EEWin]::ShowWindow($mw.h, 9) }
    [void][EEWin]::SetForegroundWindow($mw.h); Start-Sleep -Milliseconds 300
  }
  if ([EEWin]::GetForegroundWindow() -ne $mw.h) { throw 'game window is not in the foreground; not sending input' }
}

$ScanCodes = @{
  ESC = 0x01; ENTER = 0x1C; SPACE = 0x39; TAB = 0x0F; BACK = 0x0E
  F1 = 0x3B; F2 = 0x3C; F3 = 0x3D; F4 = 0x3E; F5 = 0x3F; F6 = 0x40; F7 = 0x41; F8 = 0x42; F9 = 0x43; F10 = 0x44
  UP = 0xE048; DOWN = 0xE050; LEFT = 0xE04B; RIGHT = 0xE04D; PAUSE = 0x45; ALT = 0x38; CTRL = 0x1D; SHIFT = 0x2A
}

switch ($Action) {
  'windows' {
    $w = @(Get-GameWindows | Select-Object * -ExcludeProperty h)
    $json = ConvertTo-Json -InputObject $w -Depth 4
    if ($Out) { Set-Content -Path $Out -Value $json -Encoding UTF8 } else { $json }
  }
  'screenshot' {
    Add-Type -AssemblyName System.Drawing
    $mw = Main-Window
    if ($mw) { $x = $mw.client[0]; $y = $mw.client[1]; $wd = $mw.client[2]; $ht = $mw.client[3] }
    else {
      Add-Type -AssemblyName System.Windows.Forms
      $vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
      $x = $vs.X; $y = $vs.Y; $wd = $vs.Width; $ht = $vs.Height
    }
    $bmp = New-Object System.Drawing.Bitmap $wd, $ht
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    if ($mw -and $Mode -eq 'window') {
      # PrintWindow with PW_RENDERFULLCONTENT also captures D3D/flip-model content.
      $hdc = $g.GetHdc(); [void][EEWin]::PrintWindow($mw.h, $hdc, 2); $g.ReleaseHdc($hdc)
    } else { $g.CopyFromScreen($x, $y, 0, 0, $bmp.Size) }
    $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    "screenshot $wd x $ht at ($x,$y) -> $Out"
  }
  'grab' {
    $ff = Join-Path (Split-Path $PSScriptRoot) 'tools\ffmpeg\bin\ffmpeg.exe'
    $vf = 'hwdownload,format=bgra'
    if ($Scale) { $vf += ",scale=${Scale}:-1" }
    & $ff -hide_banner -loglevel error -f lavfi -i "ddagrab=output_idx=${Output}:framerate=10" -vf $vf -frames:v 1 -q:v 3 -y $Out
    "grab exit $LASTEXITCODE -> $Out"
  }
  'close' {
    $ws = @(Get-GameWindows | Where-Object { $_.visible })
    foreach ($w in $ws) { [void][EEWin]::PostMessage($w.h, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) }  # WM_CLOSE
    "posted WM_CLOSE to $($ws.Count) window(s)"
  }
  'focus' {
    # Restore (if minimised, e.g. after another window took focus) and bring to front. A tap on
    # ALT lifts the foreground lock for this background process.
    $mw = Main-Window
    if ($mw) {
      [EEWin]::Key(0x38, $false, $false); [EEWin]::Key(0x38, $false, $true)
      if ($mw.iconic) { [void][EEWin]::ShowWindow($mw.h, 9) }
      [void][EEWin]::SetForegroundWindow($mw.h); Start-Sleep -Milliseconds 500
      "focused $($mw.hwnd): foreground=$([EEWin]::GetForegroundWindow() -eq $mw.h) iconic=$([EEWin]::IsIconic($mw.h))"
    } else { 'no window' }
  }
  'click' {
    $mw = Main-Window
    Ensure-Foreground $mw
    $sx = $X; $sy = $Y
    if (-not $Here) {
      if ($Relative -and $mw) { $sx = $mw.client[0] + $X; $sy = $mw.client[1] + $Y }
      [void][EEWin]::SetCursorPos($sx, $sy)
      Start-Sleep -Milliseconds $DelayMs
    }
    [EEWin]::MouseButton($Right, $true); Start-Sleep -Milliseconds 80; [EEWin]::MouseButton($Right, $false)
    if ($Here) { 'clicked at game cursor' } else { "clicked ($sx,$sy)" }
  }
  'move' {
    $mw = Main-Window
    Ensure-Foreground $mw
    if ($mw) {
      # Re-centre the system cursor first: legacy DirectInput derives deltas from the (clipped)
      # system cursor, so moves that hit a screen edge get lost. SetCursorPos itself is not seen.
      [void][EEWin]::SetCursorPos([int](($mw.rect[0] + $mw.rect[2]) / 2), [int](($mw.rect[1] + $mw.rect[3]) / 2))
      Start-Sleep -Milliseconds 50
    }
    $n = [Math]::Max([Math]::Ceiling([Math]::Abs($X) / $Step), [Math]::Ceiling([Math]::Abs($Y) / $Step))
    $dx = 0; $dy = 0
    for ($i = 1; $i -le $n; $i++) {
      $tx = [int][Math]::Round($X * $i / $n); $ty = [int][Math]::Round($Y * $i / $n)
      [EEWin]::MouseRel($tx - $dx, $ty - $dy); $dx = $tx; $dy = $ty
      Start-Sleep -Milliseconds 15
    }
    "moved ($X,$Y) in $n steps"
  }
  'idle' {
    $fg = [EEWin]::GetForegroundWindow(); $fpid = 0; [void][EEWin]::GetWindowThreadProcessId($fg, [ref]$fpid)
    $pn = (Get-Process -Id $fpid -ErrorAction SilentlyContinue).ProcessName
    "idle_ms=$([EEWin]::IdleMs()) foreground=$pn '$([EEWin]::Text($fg))'"
  }
  'key' {
    $mw = Main-Window
    Ensure-Foreground $mw
    foreach ($k in ($Keys -split ',')) {
      $k = $k.Trim().ToUpper()
      if ($ScanCodes.ContainsKey($k)) { $sc = $ScanCodes[$k] } else { throw "unknown key $k" }
      $ext = ($sc -band 0xE000) -eq 0xE000; $sc = $sc -band 0xFF
      [EEWin]::Key([uint16]$sc, $ext, $false); Start-Sleep -Milliseconds 60; [EEWin]::Key([uint16]$sc, $ext, $true)
      Start-Sleep -Milliseconds $DelayMs
    }
    "keys $Keys"
  }
  'type' {
    Ensure-Foreground (Main-Window)
    foreach ($ch in $Text.ToCharArray()) { [EEWin]::Unicode($ch, $false); [EEWin]::Unicode($ch, $true); Start-Sleep -Milliseconds 30 }
    "typed $($Text.Length) chars"
  }
}
