param(
    [string]$Blender = "D:\Games\Steam\steamapps\common\Blender\blender.exe",
    [switch]$Benchmark
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot

& $Blender --background --factory-startup --python (Join-Path $workspace "tests\run_tests.py")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if ($Benchmark) {
    & $Blender --background --factory-startup --python (Join-Path $workspace "tests\benchmark.py")
    exit $LASTEXITCODE
}
