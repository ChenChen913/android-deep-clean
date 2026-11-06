# Safe installer for the android-deep-clean skill.
# - backs up the existing skill directory instead of deleting it
# - verifies the expected entry files after copying
# - restores the backup automatically if installation fails
param(
    [string]$Destination = "$env:USERPROFILE\.agents\skills\android-deep-clean"
)

$ErrorActionPreference = "Stop"
$source = Split-Path -Parent $PSScriptRoot
$backup = ""
$installed = $false

if (-not (Test-Path (Join-Path $source "SKILL.md"))) {
    throw "源目录缺少 SKILL.md：$source"
}
if (-not (Test-Path (Join-Path $source "scripts\adc.py"))) {
    throw "源目录缺少 scripts\adc.py：$source"
}

$parent = Split-Path -Parent $Destination
if (-not (Test-Path $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
}

if (Test-Path $Destination) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backup = "$Destination.bak-$stamp"
    Move-Item -LiteralPath $Destination -Destination $backup
    Write-Host "已备份旧 Skill：$backup"
}

try {
    Copy-Item -LiteralPath $source -Destination $Destination -Recurse -Force
    foreach ($required in @("SKILL.md", "scripts\adc.py", "scripts\adc_lib\platform.py")) {
        if (-not (Test-Path (Join-Path $Destination $required))) {
            throw "安装后缺少文件：$required"
        }
    }
    $installed = $true
    Write-Host "安装完成：$Destination"
    if ($backup) {
        Write-Host "旧版本保留在：$backup"
        Write-Host "确认新版本可用后再手动删除备份。"
    }
}
catch {
    if (Test-Path $Destination) {
        Remove-Item -LiteralPath $Destination -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($backup -and (Test-Path $backup)) {
        Move-Item -LiteralPath $backup -Destination $Destination
        Write-Host "安装失败，已恢复旧版本：$Destination"
    }
    throw
}

if (-not $installed) {
    throw "安装未完成"
}
