#requires -Version 5.1
[CmdletBinding()]
param([switch]$RuntimeOnly)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$desktopRoot = Join-Path $repoRoot 'desktop'
$pin = Get-Content -LiteralPath (Join-Path $desktopRoot 'runtime.json') -Raw | ConvertFrom-Json
if ($pin.version -ne '44.4.5' -or $pin.platform -ne 'win32' -or $pin.arch -ne 'x64' -or
    $pin.archive -ne 'electron-v44.4.5-win32-x64.zip' -or $pin.sha256 -notmatch '^[a-f0-9]{64}$') {
    throw 'Unexpected Electron runtime pin.'
}
$releaseBase = "https://github.com/electron/electron/releases/download/v$($pin.version)/"
if ($pin.url -ne ($releaseBase + $pin.archive) -or
    $pin.checksumSource -ne ($releaseBase + 'SHASUMS256.txt')) {
    throw 'The runtime must come from the pinned official Electron release.'
}

$uiFiles = @(
    'package.json', 'main.cjs', 'preload.cjs', 'bridge-client.cjs', 'window-state.cjs', 'setup-client.cjs',
    'renderer/index.html', 'renderer/style.css', 'renderer/renderer.js', 'renderer/command-menu.js', 'renderer/onboarding.js',
    'assets/jev-icon.ico', 'assets/jev-icon.png', 'assets/jev-mark.svg'
)
$configPath = Join-Path $repoRoot '.local/project.toml'
$pythonPath = Join-Path $repoRoot '.venv/Scripts/python.exe'
if (-not $RuntimeOnly) {
    foreach ($relative in $uiFiles) {
        if (-not (Test-Path -LiteralPath (Join-Path $desktopRoot $relative) -PathType Leaf)) {
            throw "Missing desktop source: $relative"
        }
    }
    foreach ($path in @($pythonPath)) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Missing local application dependency: $path"
        }
    }
}

function Assert-ManagedPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $prefix = $repoRoot.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Build output is outside this repository: $full"
    }
    $cursor = $full
    while ($cursor -and $cursor -ne $repoRoot) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Build output cannot follow a junction or symbolic link: $cursor"
            }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Write-JsonFile([string]$Path, $Value) {
    $json = ($Value | ConvertTo-Json -Depth 8) + [Environment]::NewLine
    [IO.File]::WriteAllText($Path, $json, (New-Object Text.UTF8Encoding $false))
}

function Test-Archive([string]$Path) {
    return ((Test-Path -LiteralPath $Path -PathType Leaf) -and
        ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ieq $pin.sha256))
}

# Search only Electron's known download cache, never the whole user profile.
$archivePath = $null
if ($env:LOCALAPPDATA) {
    $cacheRoot = Join-Path $env:LOCALAPPDATA 'electron/Cache'
    if (Test-Path -LiteralPath $cacheRoot -PathType Container) {
        foreach ($directory in Get-ChildItem -LiteralPath $cacheRoot -Directory) {
            $candidate = Join-Path $directory.FullName $pin.archive
            if (Test-Archive $candidate) { $archivePath = $candidate; break }
        }
    }
}
if (-not $archivePath) {
    $downloadRoot = Join-Path $repoRoot '.local/electron-downloads'
    $archivePath = Join-Path $downloadRoot $pin.archive
    Assert-ManagedPath $archivePath
    if (-not (Test-Archive $archivePath)) {
        [IO.Directory]::CreateDirectory($downloadRoot) | Out-Null
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $client = New-Object Net.WebClient
        try {
            # Verify that the pinned hash still matches the official release before download.
            $sums = $client.DownloadString($pin.checksumSource)
            $pattern = '(?im)^' + [regex]::Escape($pin.sha256) + '\s+\*?' +
                [regex]::Escape($pin.archive) + '\r?$'
            if ($sums -notmatch $pattern) { throw 'Official Electron checksum does not match the pin.' }
            Write-Host "Downloading pinned Electron $($pin.version)..."
            $client.DownloadFile($pin.url, $archivePath)
        } finally { $client.Dispose() }
        if (-not (Test-Archive $archivePath)) { throw 'Downloaded Electron archive failed SHA-256 verification.' }
    }
}

if ($RuntimeOnly) {
    $outputRoot = Join-Path $repoRoot ".local/electron-runtime-$($pin.version)"
    $executableName = 'electron.exe'
} else {
    $outputRoot = Join-Path $repoRoot 'dist/JevContext'
    $executableName = 'JevContext.exe'
}
Assert-ManagedPath $outputRoot
$markerPath = Join-Path $outputRoot '.jev-electron-package.json'
$marker = [ordered]@{ format = 1; owner = 'jev-desktop-builder'; electronVersion = $pin.version; archiveSha256 = $pin.sha256 }
if (Test-Path -LiteralPath $outputRoot) {
    if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
        $existing = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
        if ($existing.owner -ne $marker.owner -or $existing.format -ne 1 -or
            $existing.archiveSha256 -ne $pin.sha256) {
            throw 'Refusing to overwrite a directory with a different package marker.'
        }
    } elseif (@(Get-ChildItem -LiteralPath $outputRoot -Force).Count) {
        throw "Refusing to overwrite an unmanaged nonempty directory: $outputRoot"
    }
}
Assert-ManagedPath $markerPath
[IO.Directory]::CreateDirectory($outputRoot) | Out-Null
Write-JsonFile $markerPath $marker

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
try {
    foreach ($entry in $archive.Entries) {
        $relative = $entry.FullName.Replace('/', '\')
        if ($relative -eq 'electron.exe') { $relative = $executableName }
        $destination = [IO.Path]::GetFullPath((Join-Path $outputRoot $relative))
        if (-not $destination.StartsWith($outputRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unexpected archive entry: $($entry.FullName)"
        }
        Assert-ManagedPath $destination
        if ($entry.FullName.EndsWith('/')) {
            [IO.Directory]::CreateDirectory($destination) | Out-Null
        } else {
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $true)
        }
    }
} finally { $archive.Dispose() }

if (-not $RuntimeOnly) {
    if (Test-Path -LiteralPath (Join-Path $outputRoot 'resources/app.asar')) {
        throw 'Unexpected app.asar would shadow the packaged application; no files were deleted.'
    }
    $appRoot = Join-Path $outputRoot 'resources/app'
    foreach ($relative in $uiFiles) {
        $destination = Join-Path $appRoot $relative
        Assert-ManagedPath $destination
        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
        Copy-Item -LiteralPath (Join-Path $desktopRoot $relative) -Destination $destination -Force
    }
    $launchPath = Join-Path $outputRoot 'launch-config.json'
    Assert-ManagedPath $launchPath
    Write-JsonFile $launchPath ([ordered]@{
        projectRoot = $repoRoot; configPath = $configPath; pythonPath = $pythonPath
    })
}
$executable = Join-Path $outputRoot $executableName
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) { throw 'Packaged executable is missing.' }
if (-not $RuntimeOnly) {
    Assert-ManagedPath $executable
    & (Join-Path $PSScriptRoot 'patch_desktop_icon.ps1') -Executable $executable `
        -Icon (Join-Path $desktopRoot 'assets/jev-icon.ico') -PythonPath $pythonPath | Out-Host
}
Write-Output $executable
