param(
    [switch]$StartMenu,
    [string]$ShortcutDirectory,
    [ValidateSet('3.10', '3.11', '3.12')]
    [string]$PythonVersion = '3.12',
    [string]$PySpinWheel,
    [string]$ReleaseVersion,
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
    $packageSource = $PSScriptRoot
    $constraintSource = Join-Path $PSScriptRoot 'requirements.txt'
    if ($ReleaseVersion) {
        $repository = 'https://github.com/Marcin-A-Lipiec/deepecohab-acquisition'
        $releaseEndpoint = 'https://api.github.com/repos/Marcin-A-Lipiec/deepecohab-acquisition/releases/'
        if ($ReleaseVersion -eq 'latest') {
            $releaseEndpoint += 'latest'
        } elseif ($ReleaseVersion -match '^v?(\d+\.\d+\.\d+)$') {
            $releaseEndpoint += 'tags/v' + $Matches[1]
        } else {
            throw 'ReleaseVersion must be latest or a stable version such as 1.0.0.'
        }
        # Windows PowerShell 5.1 may otherwise negotiate an obsolete TLS version.
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        try {
            $release = Invoke-RestMethod -Uri $releaseEndpoint -TimeoutSec 15 -Headers @{
                Accept = 'application/vnd.github+json'; 'User-Agent' = 'DeepEcoHAB-Acquisition-installer'
            }
        } catch {
            throw 'Cannot read the public GitHub release. Check the internet connection and Releases page. A repository upload alone is not a published release; private repositories also require separate access.'
        }
        if ($release.draft -ne $false -or $release.prerelease -ne $false -or $release.tag_name -notmatch '^v(\d+\.\d+\.\d+)$') {
            throw 'The selected release is not a published stable vX.Y.Z release.'
        }
        $resolvedVersion = $Matches[1]
        $tag = 'v' + $resolvedVersion
        if ($ReleaseVersion -ne 'latest' -and $tag -ne ('v' + $ReleaseVersion.TrimStart('v'))) {
            throw 'GitHub returned a different version from the requested release.'
        }
        $wheelName = "deepecohab_acquisition-$resolvedVersion-py3-none-any.whl"
        $wheel = @($release.assets | Where-Object { $_.name -ceq $wheelName })
        $expectedUrl = "$repository/releases/download/$tag/$wheelName"
        if ($wheel.Count -ne 1 -or $wheel[0].browser_download_url -cne $expectedUrl) {
            throw 'The release installation package is missing or invalid. Wait for the maintainer to finish publishing the release.'
        }
        $packageSource = $expectedUrl
        $constraintSource = "https://raw.githubusercontent.com/Marcin-A-Lipiec/deepecohab-acquisition/$tag/requirements.txt"
        Write-Host "Selected stable DeepEcoHAB Acquisition $resolvedVersion."
    }
    $runningGui = Get-Process -Name 'deepecohab-acquisition', 'deepecohab-acquisition-cli', 'acquisition-gui', 'deepecohab-recorder' -ErrorAction SilentlyContinue
    if ($runningGui) { throw 'Close DeepEcoHAB Acquisition before installing or updating.' }
    # Recreating a tool must not silently remove its separately installed camera interface.
    $toolDirectory = & $uvExecutable tool dir
    if ($LASTEXITCODE -ne 0) { throw 'Cannot find the uv tool directory.' }
    $existingPython = Join-Path $toolDirectory.Trim() 'deepecohab-acquisition/Scripts/python.exe'
    if (-not $PySpinWheel -and (Test-Path -LiteralPath $existingPython -PathType Leaf)) {
        & $existingPython -I -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('PySpin') else 1)"
        $cameraProbeExit = $LASTEXITCODE
        if ($cameraProbeExit -eq 0) {
            throw 'This installation contains PySpin. Repeat this command with -PySpinWheel and the path to the matching Teledyne wheel so camera support is retained. The existing installation has not been changed.'
        }
        if ($cameraProbeExit -ne 1) { throw 'Cannot inspect the existing camera interface. Repair or inspect this installation before recreating it.' }
    }
    $installArguments = @('tool', 'install', '--python', $PythonVersion,
        '--constraints', $constraintSource)
    if ($Force) {
        # Refresh the app itself, including edits to a local source folder.
        $installArguments += @('--force', '--reinstall-package', 'deepecohab-acquisition')
    }
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
    $installArguments += $packageSource
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
    Write-Host 'Check for updates using Experiment > Check for updates, or deepecohab-acquisition-cli updates.'
} catch {
    Write-Error $_
    exit 1
}
