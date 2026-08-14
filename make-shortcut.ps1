param(
    [Parameter(Mandatory = $true)][string]$Interpreter,
    [Parameter(Mandatory = $true)][string]$Root
)

$root = $Root.TrimEnd('\')
$icon = Join-Path $root 'netpulse.ico'
$script = Join-Path $root 'main.py'

if (-not (Test-Path $script)) {
    Write-Host "  main.py not found in $root"
    exit 1
}

$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop 'NetPulse.lnk'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $Interpreter
$shortcut.Arguments = '"' + $script + '"'
$shortcut.WorkingDirectory = $root
$shortcut.Description = 'NetPulse network usage monitor'
if (Test-Path $icon) {
    $shortcut.IconLocation = $icon
} else {
    Write-Host "  netpulse.ico is missing, the shortcut will use the Python icon."
}
$shortcut.Save()

Write-Host "  Created: $link"
