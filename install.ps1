<#
.SYNOPSIS
    Installs the minimax-h3-colab skill into Antigravity and/or Codex skills directories.
.USAGE
    .\install.ps1
    .\install.ps1 -Dest "C:\custom\skills"
    .\install.ps1 -Force
#>
[CmdletBinding()]
param(
    [string]$Dest,
    [switch]$Force
)

$skillName = "minimax-h3-colab"
$repoDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$destDirs = @()
if ($Dest) {
    $destDirs += $Dest
} else {
    $userHome = $env:USERPROFILE
    $geminiSkills = Join-Path $userHome ".gemini\config\skills"
    $codexSkills = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME "skills" } else { Join-Path $userHome ".codex\skills" }

    $destDirs += $geminiSkills
    $destDirs += $codexSkills
}

foreach ($skillsRoot in $destDirs) {
    if (-not (Test-Path $skillsRoot)) {
        New-Item -ItemType Directory -Path $skillsRoot -Force | Out-Null
    }

    $target = Join-Path $skillsRoot $skillName
    if (Test-Path $target) {
        if (-not $Force) {
            Write-Host "Skill already exists at: $target (Use -Force to overwrite)" -ForegroundColor Yellow
            continue
        }
        $timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
        $backup = "$target.backup.$timestamp"
        Move-Item -Path $target -Destination $backup -Force
        Write-Host "Backed up previous installation to: $backup" -ForegroundColor Cyan
    }

    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Copy-Item (Join-Path $repoDir "SKILL.md") -Destination $target -Force
    Copy-Item (Join-Path $repoDir "README.md") -Destination $target -Force
    Copy-Item (Join-Path $repoDir "README.zh-TW.md") -Destination $target -Force
    Copy-Item (Join-Path $repoDir "run_colab_inference.sh") -Destination $target -Force
    Copy-Item (Join-Path $repoDir "run_colab_inference.ps1") -Destination $target -Force
    Copy-Item (Join-Path $repoDir "scripts") -Destination $target -Recurse -Force
    Copy-Item (Join-Path $repoDir "assets") -Destination $target -Recurse -Force

    # Clean __pycache__ in target
    Get-ChildItem -Path $target -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

    Write-Host "Successfully installed $skillName to $target" -ForegroundColor Green
}
