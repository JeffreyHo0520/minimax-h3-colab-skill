<#
.SYNOPSIS
    Run one MiniMax H3 Ref2VA inference on Colab from PowerShell.
.EXAMPLE
    .\run_colab_inference.ps1 -Image .\ref1.png,.\ref2.png -Prompt .\prompt.txt -Output .\result.mp4
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, ValueFromRemainingArguments = $false)]
    [string[]]$Image,

    [Parameter(Mandatory = $true)]
    [string]$Prompt,

    [Parameter(Mandatory = $false)]
    [string]$Output,

    [Parameter(Mandatory = $false)]
    [string]$Gpu = $(if ($env:COLAB_GPU) { $env:COLAB_GPU } else { "A100" }),

    [Parameter(Mandatory = $false)]
    [switch]$NoHighMem,

    [Parameter(Mandatory = $false)]
    [int]$Timeout = $(if ($env:COLAB_EXEC_TIMEOUT) { [int]$env:COLAB_EXEC_TIMEOUT } else { 3600 })
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $scriptDir "scripts\runner.py"

if (-not (Test-Path $runner)) {
    Write-Error "Bundled runner not found: $runner"
    exit 1
}

if ($Image.Count -lt 1 -or $Image.Count -gt 9) {
    Write-Error "Provide between 1 and 9 reference images with -Image."
    exit 2
}

foreach ($img in $Image) {
    if (-not (Test-Path $img)) {
        Write-Error "Reference image not found: $img"
        exit 2
    }
}

if (-not (Test-Path $Prompt)) {
    Write-Error "Prompt file not found: $Prompt"
    exit 2
}

$cmdArgs = @($runner, "single")
foreach ($img in $Image) {
    $cmdArgs += @("--image", (Resolve-Path $img).Path)
}
$cmdArgs += @("--prompt", (Resolve-Path $Prompt).Path)
if ($Output) {
    $cmdArgs += @("--output", $Output)
}
if ($Gpu) {
    $cmdArgs += @("--gpu", $Gpu)
}
if ($NoHighMem) {
    $cmdArgs += @("--no-high-mem")
}
if ($Timeout) {
    $cmdArgs += @("--timeout", "$Timeout")
}

& python $cmdArgs
exit $LASTEXITCODE
