#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Executable,
    [string]$Icon,
    [string]$PythonPath,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$packageRoot = Join-Path $repoRoot 'dist/JevContext'
$allowedExecutable = Join-Path $packageRoot 'JevContext.exe'
if (-not $Executable) { $Executable = $allowedExecutable }
if (-not $Icon) { $Icon = Join-Path $repoRoot 'desktop/assets/jev-icon.ico' }
if (-not $PythonPath) { $PythonPath = Join-Path $repoRoot '.venv/Scripts/python.exe' }
$Executable = [IO.Path]::GetFullPath($Executable)
$Icon = [IO.Path]::GetFullPath($Icon)
$PythonPath = [IO.Path]::GetFullPath($PythonPath)
if (-not $Executable.Equals($allowedExecutable, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Icon updates are restricted to dist/JevContext/JevContext.exe; runtime caches cannot be patched.'
}

function Assert-ManagedPath([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($repoRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Icon output is outside this repository: $full"
    }
    $cursor = $full
    while ($cursor -and $cursor -ne $repoRoot) {
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Icon output cannot follow a junction or symbolic link: $cursor"
            }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

$reportPath = Join-Path $packageRoot 'icon-verification.json'
$extractedPath = Join-Path $packageRoot 'JevContext.resources.ico'
foreach ($path in @($Executable, $reportPath, $extractedPath)) { Assert-ManagedPath $path }
foreach ($path in @($Executable, $Icon, $PythonPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing icon patch dependency: $path" }
}
$marker = Get-Content -LiteralPath (Join-Path $packageRoot '.jev-electron-package.json') -Raw | ConvertFrom-Json
$pin = Get-Content -LiteralPath (Join-Path $repoRoot 'desktop/runtime.json') -Raw | ConvertFrom-Json
if ($marker.owner -ne 'jev-desktop-builder' -or $marker.format -ne 1 -or
    $marker.archiveSha256 -ne $pin.sha256 -or $marker.electronVersion -ne $pin.version) {
    throw 'The executable is not in the current managed Electron package.'
}

# pywin32 is already pinned in the project's Windows Python environment. The update cycle
# preserves existing resources, replacing only RT_GROUP_ICON (14) and RT_ICON (3).
# https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-updateresourcew
$pythonCode = @'
import ctypes
import hashlib
import json
import struct
import sys
from pathlib import Path

import win32api
import win32con

exe, icon, report_path, extracted_path = map(Path, sys.argv[1:5])
verify_only = sys.argv[5] == 'verify'
if exe.stat().st_nlink != 1:
    raise ValueError('Refusing to update an executable with hard links')

def digest(value):
    return hashlib.sha256(value).hexdigest()

def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def resources(path):
    # LOAD_LIBRARY_AS_DATAFILE never runs the executable or its entry point.
    module = win32api.LoadLibraryEx(str(path), 0, win32con.LOAD_LIBRARY_AS_DATAFILE)
    try:
        return {(kind, name, language): win32api.LoadResource(module, kind, name, language)
                for kind in win32api.EnumResourceTypes(module)
                for name in win32api.EnumResourceNames(module, kind)
                for language in win32api.EnumResourceLanguages(module, kind, name)}
    finally:
        win32api.FreeLibrary(module)

raw = icon.read_bytes()
if len(raw) > 8 * 1024 * 1024 or len(raw) < 6:
    raise ValueError('Unexpected ICO size')
reserved, kind, count = struct.unpack_from('<HHH', raw)
expected_sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
if (reserved, kind, count) != (0, 1, len(expected_sizes)):
    raise ValueError('Expected a nine-size Windows ICO')
entries, payloads, sizes = [], [], []
offset = 6 + 16 * count
for index in range(count):
    entry = raw[6 + 16 * index:22 + 16 * index]
    width, height, colors, entry_reserved, planes, bits, length, location = struct.unpack('<BBBBHHII', entry)
    width, height = width or 256, height or 256
    if width != height or entry_reserved or location != offset or not length or offset + length > len(raw):
        raise ValueError('ICO entries must be square and contiguous in table order')
    entries.append(entry)
    payloads.append(raw[offset:offset + length])
    sizes.append(width)
    offset += length
if sorted(sizes) != expected_sizes or offset != len(raw):
    raise ValueError('ICO resolutions or payload length differ from the required assets')

before_hash = file_digest(exe)
before = resources(exe)
groups = [key for key in before if key[0] == win32con.RT_GROUP_ICON]
# The pinned Electron 44.4.5 image has one group (ID 1, en-US). Keep that identity so
# Explorer's default executable icon and all existing native references choose the JEV group.
if groups != [(win32con.RT_GROUP_ICON, 1, 1033)]:
    raise ValueError('Unexpected Electron icon group; review the new runtime before patching')
group_key = groups[0]
language = group_key[2]
group = struct.pack('<HHH', 0, 1, count) + b''.join(
    entry[:12] + struct.pack('<H', index + 1) for index, entry in enumerate(entries))
if not verify_only:
    handle = win32api.BeginUpdateResource(str(exe), False)
    try:
        for resource_kind, name, resource_language in before:
            if resource_kind == win32con.RT_ICON:
                win32api.UpdateResource(handle, resource_kind, name, None, resource_language)
        for index, payload in enumerate(payloads, 1):
            win32api.UpdateResource(handle, win32con.RT_ICON, index, payload, language)
        win32api.UpdateResource(handle, win32con.RT_GROUP_ICON, 1, group, language)
    except BaseException:
        win32api.EndUpdateResource(handle, True)
        raise
    else:
        win32api.EndUpdateResource(handle, False)

after = resources(exe)
if after.get(group_key) != group:
    raise ValueError('Extracted RT_GROUP_ICON does not match the ICO directory')
actual_icons = {key: value for key, value in after.items() if key[0] == win32con.RT_ICON}
expected_icons = {(win32con.RT_ICON, index, language): payload for index, payload in enumerate(payloads, 1)}
if actual_icons != expected_icons:
    raise ValueError('Extracted RT_ICON images differ from the input ICO')
unrelated_before = {key: value for key, value in before.items() if key[0] not in (3, 14)}
unrelated_after = {key: value for key, value in after.items() if key[0] not in (3, 14)}
if unrelated_before != unrelated_after:
    raise ValueError('A non-icon resource changed during icon replacement')

# Reconstruct a standalone ICO from the actual PE group and RT_ICON resources, not the input.
directory, images = [], []
offset = 6 + 16 * count
for index in range(count):
    entry = after[group_key][6 + 14 * index:20 + 14 * index]
    resource_id = struct.unpack_from('<H', entry, 12)[0]
    image = after[(win32con.RT_ICON, resource_id, language)]
    directory.append(entry[:12] + struct.pack('<I', offset))
    images.append(image)
    offset += len(image)
extracted = struct.pack('<HHH', 0, 1, count) + b''.join(directory) + b''.join(images)
if extracted != raw:
    raise ValueError('Reconstructed executable ICO is not byte-identical to the source')
extracted_path.write_bytes(extracted)
if not verify_only:
    # Refresh only this executable's Shell item; never reset Explorer or its icon cache.
    # SHCNE_UPDATEITEM | SHCNF_PATHW, documented by SHChangeNotify.
    notify = ctypes.WinDLL('shell32').SHChangeNotify
    notify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_void_p]
    notify.restype = None
    notify(0x00002000, 0x0005, str(exe), None)
report = {
    'verified': True, 'operation': 'verify' if verify_only else 'replace',
    'executable': str(exe), 'icon_source': str(icon), 'extracted_icon': str(extracted_path),
    'source_ico_sha256': digest(raw), 'extracted_ico_sha256': digest(extracted),
    'executable_sha256_before': before_hash, 'executable_sha256_after': file_digest(exe),
    'group_id': 1, 'language': language, 'sizes': sizes,
    'resource_ids': list(range(1, count + 1)), 'non_icon_resources_unchanged': True,
    'shell_item_update_notified': not verify_only,
    'images': [{'size': size, 'bytes': len(payload), 'sha256': digest(payload)}
               for size, payload in zip(sizes, images)],
}
report_path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'verified': True, 'report': str(report_path), 'extracted_icon': str(extracted_path)}))
'@

$operation = if ($VerifyOnly) { 'verify' } else { 'replace' }
$pythonCode | & $PythonPath -X utf8 - $Executable $Icon $reportPath $extractedPath $operation
if ($LASTEXITCODE -ne 0) { throw "Executable icon $operation failed; the package is not ready." }
