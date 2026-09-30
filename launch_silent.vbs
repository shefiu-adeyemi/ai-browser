' Thin wrapper: starts launcher.py with no console window. All the logic lives in launcher.py.
Dim proj, sh
proj = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = proj
sh.Run "cmd /c where py >nul 2>&1 & if errorlevel 1 (python launcher.py) else (py -3 launcher.py)", 0, False
