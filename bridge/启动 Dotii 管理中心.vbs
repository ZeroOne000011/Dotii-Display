Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
bridgeFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
projectFolder = fileSystem.GetParentFolderName(bridgeFolder)
privatePython = projectFolder & "\.codex\packaging-venv\Scripts\pythonw.exe"
If fileSystem.FileExists(privatePython) Then
  pythonCommand = """" & privatePython & """"
Else
  pythonCommand = "pythonw.exe"
End If
command = pythonCommand & " -B """ & bridgeFolder & "\bridge_app.py"" --open-dashboard"
shell.Run command, 0, False
