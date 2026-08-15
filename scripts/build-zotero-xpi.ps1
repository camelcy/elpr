$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$sourcePath = Join-Path $projectPath 'zotero-plugin'
$manifestPath = Join-Path $sourcePath 'manifest.json'
$distPath = Join-Path $projectPath 'dist'

if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Zotero plugin manifest not found: $sourcePath"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $distPath | Out-Null
$outputPath = Join-Path $distPath "zotero-excalidraw-canvas-$($manifest.version).xpi"
$archivePath = Join-Path $distPath "zotero-excalidraw-canvas-$($manifest.version).zip"
if (Test-Path -LiteralPath $outputPath) {
    Remove-Item -LiteralPath $outputPath -Force
}
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -LiteralPath $archivePath -Force
}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::Open($archivePath, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    Get-ChildItem -LiteralPath $sourcePath -Recurse -File | ForEach-Object {
        $entryName = $_.FullName.Substring($sourcePath.Length + 1).Replace('\', '/')
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archive,
            $_.FullName,
            $entryName,
            [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
}
finally {
    $archive.Dispose()
}
Move-Item -LiteralPath $archivePath -Destination $outputPath
Write-Host "Built $outputPath"
