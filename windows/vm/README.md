# windows/vm: EE-VM, a second Windows peer on legion

LAN reference captures need two unmodified Windows clients. The Mac's Wine build can't show the
multiplayer lobby: the lobby is a GDI child window and Wine on macOS keeps the last Direct3D frame
on top of it (Wine 10 and 11 alike). So the second peer is a Windows 11 VM on legion. Both peers
run on legion's real x86 CPU, so their x87 results match bit for bit.

| File | Purpose |
|---|---|
| `build.sh` | Mac: builds the unattend CD, copies it and the `.vmx` to `C:\EEPort\vm\EE-VM\` on legion, creates the 64 GB disk |
| `autounattend.xml` | Unattended Windows 11 Pro install: TPM check bypassed (no vTPM), local admin `ee` with autologon, UK formats + US keyboard |
| `firstlogon.ps1` | First logon in the VM: VMware Tools (SVGA 3D), OpenSSH server with the Mac's key, no sleep, network profile Private |
| `EE-VM.vmx.in` | 8 GB RAM, 4 cores, EFI + Secure Boot, NVMe, e1000e bridged to the host LAN, 3D on |

## Steps

Prerequisites on legion: VMware Workstation Pro (Broadcom download, free), the
*Windows Hypervisor Platform* feature (memory integrity keeps Hyper-V running) plus a restart,
and a Windows 11 x64 ISO.

```sh
STATE=/some/private/dir windows/vm/build.sh 'C:\Users\Max\Downloads\Windows11_Client_x64_en-us_26300_9457.iso'
```

Then in VMware on legion: *File → Open* `C:\EEPort\vm\EE-VM\EE-VM.vmx`, power on, and press a key
at "Press any key to boot from CD or DVD". Setup, VMware Tools and OpenSSH run unattended and the
VM reboots to the `ee` desktop (about 20 minutes). The generated password stays in `$STATE/password`.
After that the Mac reaches the VM as `ee@ee-vm.local` with `~/.ssh/id_rsa`, and `HOST=... windows/deploy.sh`
installs the game and tooling as on legion.
