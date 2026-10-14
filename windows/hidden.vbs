' Run a PowerShell script file hidden (no console flash on the user's screen) and wait for it.
' Used by launch.ps1 for jobs that must run in the interactive session.
' Usage: wscript.exe //B //Nologo hidden.vbs C:\path\job.ps1
Dim sh
Set sh = CreateObject("WScript.Shell")
WScript.Quit sh.Run("powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & WScript.Arguments(0) & """", 0, True)
