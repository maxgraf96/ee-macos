# First logon inside EE-VM (run by autounattend.xml from the unattend CD). Log: C:\firstlogon.log
# - VMware Tools from the tools CD (SVGA 3D driver; the game needs Direct3D)
# - OpenSSH server with the Mac's key (authorized_keys next to this script), so the Mac can
#   drive the VM like legion (windows/deploy.sh with HOST=<vm>)
# - no sleep / screen off, network profile Private (the game's firewall rules are Private-only)
$ErrorActionPreference = 'Continue'
Start-Transcript -Path C:\firstlogon.log -Append

powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

Get-NetConnectionProfile | Set-NetConnectionProfile -NetworkCategory Private

$tools = Get-Volume | Where-Object FileSystemLabel -eq 'VMware Tools' | Select-Object -First 1
if ($tools) {
  $root = "$($tools.DriveLetter):\"
  $setup = @('setup64.exe', 'setup.exe') | ForEach-Object { Join-Path $root $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
  Write-Output "VMware Tools: $setup"
  Start-Process $setup -ArgumentList '/S /v "/qn REBOOT=R"' -Wait
} else {
  Write-Output 'VMware Tools CD not found'
}

# Add-WindowsCapability downloads from Windows Update; give the network a few tries.
for ($i = 0; $i -lt 10; $i++) {
  $cap = Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
  if ((Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0).State -eq 'Installed') { break }
  Start-Sleep -Seconds 30
}
Set-Service sshd -StartupType Automatic
Start-Service sshd
if (-not (Get-NetFirewallRule -Name 'EE-VM-sshd' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name 'EE-VM-sshd' -DisplayName 'OpenSSH Server (EE-VM)' -Direction Inbound `
    -Protocol TCP -LocalPort 22 -Action Allow -Profile Any | Out-Null
}
# ee is an administrator, so sshd reads administrators_authorized_keys (ACL: Administrators + SYSTEM only).
$keys = 'C:\ProgramData\ssh\administrators_authorized_keys'
Copy-Item (Join-Path $PSScriptRoot 'authorized_keys') $keys -Force
icacls $keys /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F'

Get-NetIPAddress -AddressFamily IPv4 | Format-Table InterfaceAlias, IPAddress -AutoSize | Out-String | Write-Output
Stop-Transcript
# The SVGA driver from VMware Tools needs a reboot; autologon brings the desktop back.
Restart-Computer -Force
