Option Explicit

Dim fso, shell, root, packagedExe, sourcePath, command, errorText
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root

packagedExe = root & "\F1ClassicLauncher.exe"
If Not fso.FileExists(packagedExe) Then
    packagedExe = root & "\dist\F1ClassicLauncher_Full\F1ClassicLauncher.exe"
End If
If Not fso.FileExists(packagedExe) Then
    packagedExe = root & "\dist\F1ClassicLauncher.exe"
End If

On Error Resume Next
If fso.FileExists(packagedExe) Then
    shell.Run """" & packagedExe & """", 1, False
Else
    sourcePath = root & "\main.py"
    command = "pythonw.exe " & """" & sourcePath & """"
    shell.Run command, 0, False
End If
If Err.Number <> 0 Then
    errorText = Err.Description
    Err.Clear
    MsgBox "Could not start F1 Classic Launcher: " & errorText & vbCrLf & vbCrLf & _
        "Build F1ClassicLauncher.exe, run build_windows\build_swofty_and_launcher.ps1, or install Python with pythonw.exe on PATH.", _
        vbCritical, "F1 Classic Launcher"
End If
