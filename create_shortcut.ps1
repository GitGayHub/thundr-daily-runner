$WshShell = New-Object -ComObject WScript.Shell
$Desktop = [Environment]::GetFolderPath('Desktop')

# 1. Main Direct GUI Shortcut (clean launch via pythonw, no black console window)
$GuiShortcutPath = Join-Path $Desktop 'Thundr Rewards.lnk'
$GuiShortcut = $WshShell.CreateShortcut($GuiShortcutPath)
$GuiShortcut.TargetPath = 'C:\Users\Tymur\AppData\Local\Programs\Python\Python312\pythonw.exe'
$GuiShortcut.Arguments = 'C:\VibeCoding\ThundrCoin\gui.py'
$GuiShortcut.WorkingDirectory = 'C:\VibeCoding\ThundrCoin'
$GuiShortcut.Description = 'Thundr Daily Rewards Manager (50 Accounts)'
$GuiShortcut.IconLocation = 'C:\Windows\System32\imageres.dll, 102'
$GuiShortcut.Save()

Write-Host "Created main GUI shortcut: $GuiShortcutPath"
