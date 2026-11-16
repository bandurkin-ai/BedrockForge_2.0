# Builds the custom portable .exe (tools/portable.nsi) from app\release\win-unpacked with the
# makensis that electron-builder downloaded. Used by windows.yml and release.yml.
# The cache dir is pinned by ELECTRON_BUILDER_CACHE in the workflow; the default locations are a fallback.
param([switch]$Check)
$ErrorActionPreference = "Stop"

$roots = @($env:ELECTRON_BUILDER_CACHE, "$env:LOCALAPPDATA\electron-builder\Cache", "$env:LOCALAPPDATA\electron-builder") |
  Where-Object { $_ -and (Test-Path $_) }
$makensis = $roots | ForEach-Object { Get-ChildItem -Recurse -Filter makensis.exe $_ -ErrorAction SilentlyContinue } | Select-Object -First 1
if (-not $makensis) { throw "makensis.exe not found (searched: $($roots -join '; '))" }
Write-Host "makensis: $($makensis.FullName)"

$version = (Get-Content app\package.json -Raw | ConvertFrom-Json).version
$out = "app\release\BedrockForge-$version-portable.exe"
& $makensis.FullName /V2 "/DVERSION=$version" "/DSRC=$PWD\app\release\win-unpacked" "/DOUTFILE=$PWD\$out" tools\portable.nsi
if ($LASTEXITCODE -ne 0) { throw "makensis failed" }

if ($Check) {
  $dir = Join-Path $env:RUNNER_TEMP "Портативная BF"
  New-Item -ItemType Directory -Force -Path $dir | Out-Null
  Copy-Item $out $dir
  $exe = Join-Path $dir (Split-Path $out -Leaf)
  $t = Measure-Command { Start-Process -FilePath $exe -ArgumentList "/S", "/extract-only" -Wait }
  if (-not (Test-Path (Join-Path $dir "BedrockForge-$version\BedrockForge.exe"))) { throw "portable did not unpack" }
  "Portable: первая распаковка {0:N1} с" -f $t.TotalSeconds | Tee-Object -Append $env:GITHUB_STEP_SUMMARY
}
