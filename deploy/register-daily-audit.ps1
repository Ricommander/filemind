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
$git = Get-Command git.exe -ErrorAction Stop
$gitStatus = & $git.Source -C $repoRoot status --porcelain 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Could not verify the repository status before task registration: $gitStatus"
}
if ($gitStatus) {
    throw "Commit the reviewed repository changes before registering the daily task."
}
$automationRoot = Join-Path $env:LOCALAPPDATA "filemind\daily-audit"
$runnerPath = Join-Path $PSScriptRoot "run-daily-audit.ps1"
$pwshPath = Join-Path $PSHOME "pwsh.exe"
if (-not (Test-Path $pwshPath -PathType Leaf)) {
    throw "Current PowerShell executable not found: $pwshPath"
}
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
    "-AutomationRoot"
    ('"{0}"' -f $automationRoot)
    "-CopilotCommand"
    ('"{0}"' -f $CopilotCommand)
)
if ($EnableDeploy) {
    $arguments += "-EnableDeploy"
}

$action = New-ScheduledTaskAction `
    -Execute $pwshPath `
    -Argument ($arguments -join " ") `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal `
    -UserId $identity `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Daily filemind log audit and tested repair run" `
    -Force | Out-Null

Write-Output "Registered '$TaskName' daily at $($DailyAt.ToString('HH:mm')) for the interactive account $identity. Keep this account signed in; the screen may be locked. No account password is stored by this installer."