Set shell = CreateObject("WScript.Shell")
baseDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
cmd = "pythonw.exe """ & baseDir & "\MobiKfxStudio.pyw"""
For i = 0 To WScript.Arguments.Count - 1
    arg = Replace(WScript.Arguments(i), """", "\""")
    cmd = cmd & " """ & arg & """"
Next
shell.Run cmd, 0, False
