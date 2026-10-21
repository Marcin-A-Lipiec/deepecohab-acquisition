param(
    [switch]$StartMenu,
    [string]$ShortcutDirectory,
    [ValidateSet('3.10', '3.11', '3.12')]
    [string]$PythonVersion = '3.12',
    [string]$PySpinWheel,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
try {
    if ($StartMenu -and $ShortcutDirectory) {
        throw 'Choose either -StartMenu or -ShortcutDirectory.'
    }
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCommand) {
        $uvExecutable = $uvCommand.Source
    } else {
        $uvExecutable = Join-Path $env:USERPROFILE '.local/bin/uv.exe'
        if (-not (Test-Path -LiteralPath $uvExecutable -PathType Leaf)) {
            throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'
        }
    }
    $installArguments = @('tool', 'install', '--python', $PythonVersion,
        '--constraints', (Join-Path $PSScriptRoot 'requirements.txt'))
    if ($Force) { $installArguments += '--force' }
    if ($PySpinWheel) {
        if (-not (Test-Path -LiteralPath $PySpinWheel -PathType Leaf)) {
            throw 'PySpin wheel not found. Download the matching vendor wheel first.'
        }
        $resolvedWheel = (Resolve-Path -LiteralPath $PySpinWheel).Path
        if ([System.IO.Path]::GetExtension($resolvedWheel) -ne '.whl') {
            throw 'PySpinWheel must be a vendor .whl file.'
        }
        $installArguments += @('--with', $resolvedWheel)
    }
    $installArguments += $PSScriptRoot
    & $uvExecutable @installArguments
    if ($LASTEXITCODE -ne 0) { throw 'uv installation failed. Read the error above.' }
    $uvExecutableDirectory = & $uvExecutable tool dir --bin
    if ($LASTEXITCODE -ne 0) { throw 'Cannot find uv executable directory.' }
    $shortcutExecutable = Join-Path $uvExecutableDirectory.Trim() 'deepecohab-acquisition-shortcut.exe'
    $shortcutArguments = @()
    if ($StartMenu) { $shortcutArguments += '--start-menu' }
    if ($ShortcutDirectory) { $shortcutArguments += @('--directory', $ShortcutDirectory) }
    & $shortcutExecutable @shortcutArguments
    if ($LASTEXITCODE -ne 0) { throw 'Application installed, but shortcut creation failed.' }
    Write-Host 'DeepEcoHab Acquisition is installed. Open its shortcut to launch.'
    Write-Host 'Use System check in the GUI to verify camera software and select FFmpeg/ffprobe paths.'
} catch {
    Write-Error $_
    exit 1
}
