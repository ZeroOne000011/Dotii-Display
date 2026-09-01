Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
bridgeFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
command = "pythonw.exe -B """ & bridgeFolder & "\bridge_app.py"" --open-dashboard"
shell.Run command, 0, False
