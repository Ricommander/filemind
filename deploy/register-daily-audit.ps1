[CmdletBinding()]
param(
    [string]$TaskName = "filemind daily audit",
    [datetime]$DailyAt = (Get-Date -Hour 2 -Minute 0 -Second 0),
    [string]$RepositoryPath,
    [string]$CopilotCommand,
    [switch]$EnableDeploy
)

$ErrorActionPreference = "Stop"
$repoRoot = if ($RepositoryPath) {
    (Resolve-Path $RepositoryPath).Path
} else {
    (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$runnerPath = Join-Path $PSScriptRoot "run-daily-audit.ps1"
$pwsh = Get-Command pwsh.exe -CommandType Application -ErrorAction Stop
if (-not $CopilotCommand) {
    $installedCopilot = Join-Path $env:LOCALAPPDATA "Programs\CopilotCLI\copilot.exe"
    $CopilotCommand = if (Test-Path $installedCopilot -PathType Leaf) { $installedCopilot } else { "copilot" }
}
if ($EnableDeploy) {
    $deployScript = Join-Path $PSScriptRoot "update-server.ps1"
    & $deployScript -RepositoryPath $repoRoot -CheckOnly
    if (-not $?) {
        throw "Remote deploy wrapper preflight failed; scheduled auto-deploy was not registered."
    }
}

$arguments = @(
    "-NoLogo"
    "-NoProfile"
    "-NonInteractive"
    "-File"
    ('"{0}"' -f $runnerPath)
    "-AllowLogUpload"
    "-CopilotCommand"
    ('"{0}"' -f $CopilotCommand)
)
if ($EnableDeploy) {
    $arguments += "-EnableDeploy"
}

$action = New-ScheduledTaskAction `
    -Execute $pwsh.Source `
    -Argument ($arguments -join " ") `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$credential = Get-Credential -Message "Windows account for the scheduled filemind audit task"
$password = $credential.GetNetworkCredential().Password

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -User $credential.UserName `
        -Password $password `
        -RunLevel Limited `
        -Description "Daily filemind log audit and tested repair run" `
        -Force | Out-Null
}
finally {
    $password = $null
    $credential = $null
}

Write-Output "Registered '$TaskName' daily at $($DailyAt.ToString('HH:mm')). Run it once manually from Task Scheduler to verify CLI sign-in and SSH access."