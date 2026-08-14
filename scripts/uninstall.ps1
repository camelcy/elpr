param(
    [string]$VaultPath = 'D:\Obsidian\Steins Gate',
    [switch]$RemoveFixture
)

$ErrorActionPreference = 'Stop'
$pluginsRoot = [IO.Path]::GetFullPath((Join-Path $VaultPath '.obsidian\plugins'))
$pluginPath = [IO.Path]::GetFullPath((Join-Path $pluginsRoot 'zotero-excalidraw-sync'))
if (-not $pluginPath.StartsWith($pluginsRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to remove path outside plugin directory: $pluginPath"
}

$enabledPath = Join-Path $VaultPath '.obsidian\community-plugins.json'
if (Test-Path -LiteralPath $enabledPath) {
    & node.exe (Join-Path $PSScriptRoot 'update-community-plugins.mjs') disable $enabledPath
    if ($LASTEXITCODE -ne 0) { throw 'failed to update community plugin list' }
}
if (Test-Path -LiteralPath $pluginPath) {
    Remove-Item -LiteralPath $pluginPath -Recurse -Force
}
if ($RemoveFixture) {
    $fixture = [IO.Path]::GetFullPath((Join-Path $VaultPath 'Excalidraw\Zotero Sync MVP Fixture.excalidraw.md'))
    $vaultRoot = [IO.Path]::GetFullPath($VaultPath)
    if (-not $fixture.StartsWith($vaultRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove fixture outside vault: $fixture"
    }
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Force }
}
Write-Host 'Plugin removed. Project source and synchronization state were kept.'
