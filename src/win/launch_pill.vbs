Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
installDir = fso.GetParentFolderName(fso.GetParentFolderName(scriptDir))
ps1Path = scriptDir & "\slsk_floating_pill.ps1"
sh.Run "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -STA -File """ & ps1Path & """ -InstallDir """ & installDir & """", 0, False
