param(
    [string]$Blender = "D:\Games\Steam\steamapps\common\Blender\blender.exe"
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$source = Join-Path $workspace "uv_padding_overlay"
$distribution = Join-Path $workspace "dist"
$package = Join-Path $distribution "uv_padding_overlay-1.4.0.zip"

New-Item -ItemType Directory -Force -Path $distribution | Out-Null

& $Blender --factory-startup --command extension build `
    --source-dir $source `
    --output-filepath $package
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $Blender --factory-startup --command extension validate $package
exit $LASTEXITCODE
