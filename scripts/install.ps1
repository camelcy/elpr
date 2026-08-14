param(
    [string]$VaultPath = 'D:\Obsidian\Steins Gate',
    [switch]$SkipBuild,
    [switch]$Enable
)

$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
$pluginPath = Join-Path $VaultPath '.obsidian\plugins\zotero-excalidraw-sync'
$excalidrawManifest = Join-Path $VaultPath '.obsidian\plugins\obsidian-excalidraw-plugin\manifest.json'

if (-not (Test-Path -LiteralPath $excalidrawManifest)) {
    throw "Excalidraw plugin not found: $excalidrawManifest"
}
if (-not $SkipBuild) {
    & npm.cmd install --prefix $projectPath
    if ($LASTEXITCODE -ne 0) { throw 'npm install failed' }
    & npm.cmd run build --prefix $projectPath
    if ($LASTEXITCODE -ne 0) { throw 'plugin build failed' }
}
if (-not (Test-Path -LiteralPath (Join-Path $projectPath 'dist\main.js'))) {
    throw 'dist\main.js is missing; run npm run build first'
}

New-Item -ItemType Directory -Force -Path $pluginPath | Out-Null
Copy-Item -LiteralPath (Join-Path $projectPath 'dist\main.js') -Destination (Join-Path $pluginPath 'main.js') -Force
Copy-Item -LiteralPath (Join-Path $projectPath 'manifest.json') -Destination (Join-Path $pluginPath 'manifest.json') -Force
Copy-Item -LiteralPath (Join-Path $projectPath 'styles.css') -Destination (Join-Path $pluginPath 'styles.css') -Force

if ($Enable) {
    $enabledPath = Join-Path $VaultPath '.obsidian\community-plugins.json'
    & node.exe (Join-Path $PSScriptRoot 'update-community-plugins.mjs') enable $enabledPath
    if ($LASTEXITCODE -ne 0) { throw 'failed to update community plugin list' }
}

Write-Host "Installed to: $pluginPath"
if (-not $Enable) { Write-Host 'Enable Zotero Excalidraw Sync in Obsidian community plugin settings.' }
