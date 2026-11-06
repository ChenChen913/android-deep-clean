# Build reproducible release artifacts from the repository root.
# Usage: powershell -ExecutionPolicy Bypass -File tools/build_dist.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$version = (Get-Content (Join-Path $root "VERSION") -Raw).Trim()
$dist = Join-Path $root "dist"
$stage = Join-Path $root ".build"
$stageSkill = Join-Path $stage "skill"
$stageProject = Join-Path $stage "project"

if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $dist, $stageSkill, $stageProject | Out-Null

# Single source of truth: root CHANGELOG / LICENSE / VERSION are copied into the
# skill directory so the checked-in copies and the package cannot drift.
Copy-Item (Join-Path $root "android-deep-clean\*") $stageSkill -Recurse -Force
# Single source of truth: root CHANGELOG / LICENSE / VERSION win over the copies
# kept inside the skill directory, so packages cannot drift from the repo.
foreach ($name in @("CHANGELOG.md", "LICENSE", "NOTICE", "VERSION")) {
    $source = Join-Path $root $name
    if (Test-Path $source) {
        Copy-Item $source (Join-Path $stageSkill $name) -Force
    }
}
Copy-Item $stageSkill (Join-Path $stageProject "android-deep-clean") -Recurse -Force

foreach ($name in @("tests", "docs")) {
    $source = Join-Path $root $name
    if (Test-Path $source) {
        Copy-Item $source (Join-Path $stageProject $name) -Recurse -Force
    }
}
foreach ($name in @("README.md", "SKILL.md", "CHANGELOG.md", "LICENSE", "NOTICE", "VERSION", ".gitignore")) {
    $source = Join-Path $root $name
    if (Test-Path $source) {
        Copy-Item $source (Join-Path $stageProject $name) -Force
    }
}

$skillZip = Join-Path $dist "android-deep-clean.skill.zip"
$projectZip = Join-Path $dist "android-deep-clean-project-$version.zip"
Remove-Item $skillZip, $projectZip -Force -ErrorAction SilentlyContinue

Compress-Archive -Path (Join-Path $stageProject "android-deep-clean") -DestinationPath $skillZip -Force
Compress-Archive -Path (Join-Path $stageProject "*") -DestinationPath $projectZip -Force
Move-Item $skillZip (Join-Path $dist "android-deep-clean.skill") -Force

Remove-Item $stage -Recurse -Force

Write-Host "built:"
Write-Host "  dist/android-deep-clean.skill"
Write-Host "  dist/android-deep-clean-project-$version.zip"
