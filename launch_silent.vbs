' Launches AI Browser Agent silently (no console window)
Dim proj
proj = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' Start the server without a window
Dim sh
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c cd /d """ & proj & """ && python agent_ui.py", 0, False

' Wait 4 seconds then open the browser
WScript.Sleep 4000
sh.Run "http://localhost:8000", 1, False
