[CmdletBinding()]
param(
    [string]$Server = "fritzleserver",
    [string]$RemoteLogDirectory = "/opt/filemind/.filemind/logs",
    [string]$OutputDirectory,
    [switch]$UseSudo
)

$ErrorActionPreference = "Stop"

foreach ($commandName in @("ssh.exe", "scp.exe", "tar.exe")) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $commandName"
    }
}

if (-not $OutputDirectory) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $OutputDirectory = Join-Path (Get-Location).Path "filemind-logs-$timestamp"
}

$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$remoteArchive = "/tmp/filemind-logs-$([guid]::NewGuid().ToString('N')).tar.gz"
$localArchive = Join-Path $env:TEMP ([guid]::NewGuid().ToString("N") + ".tar.gz")

function ConvertTo-ShellArgument([string]$Value) {
    return "'" + $Value.Replace("'", "'\''") + "'"
}

Import-Module (Join-Path $PSScriptRoot "filemind-log-remote.psm1") -Force
$remoteScript = New-FilemindLogArchiveScript -UseSudo:$UseSudo

try {
    $remoteScript = $remoteScript -replace "`r`n?", "`n"
    $encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteScript))
    $remoteCommand = @(
        "printf '%s' $(ConvertTo-ShellArgument $encodedScript) | base64 -d | bash -s --"
        (ConvertTo-ShellArgument $RemoteLogDirectory)
        (ConvertTo-ShellArgument $remoteArchive)
    ) -join " "
    if ($UseSudo) {
        & ssh.exe -o BatchMode=yes -tt $Server $remoteCommand
    }
    else {
        & ssh.exe -o BatchMode=yes -T $Server $remoteCommand
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Could not prepare remote logs (exit code $LASTEXITCODE)"
    }

    & scp.exe -o BatchMode=yes "${Server}:$remoteArchive" $localArchive
    if ($LASTEXITCODE -ne 0) {
        throw "SCP download failed with exit code $LASTEXITCODE"
    }

    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    & tar.exe -xzf $localArchive -C $OutputDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Could not extract downloaded logs (exit code $LASTEXITCODE)"
    }

    Get-ChildItem -Path $OutputDirectory -File |
        Where-Object { $_.Name -like "performance.jsonl*" -or $_.Name -like "filemind.log*" } |
        Select-Object -ExpandProperty FullName
}
finally {
    Remove-Item -Path $localArchive -Force -ErrorAction SilentlyContinue
    & ssh.exe -o BatchMode=yes $Server "rm -f -- '$remoteArchive'" 2>$null | Out-Null
}
