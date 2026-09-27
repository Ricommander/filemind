[CmdletBinding()]
param(
    [string]$Server = "fritzleserver",
    [string]$RemoteLogDirectory = "/opt/filemind/.filemind/logs",
    [string]$OutputDirectory
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

$remoteScript = @'
set -euo pipefail
log_dir="$1"
archive="$2"
if ! sudo test -d "$log_dir"; then
    printf 'Log directory not found: %s\n' "$log_dir" >&2
    exit 2
fi
mapfile -d '' log_files < <(
    sudo find "$log_dir" -maxdepth 1 -type f \
        \( -name 'performance.jsonl*' -o -name 'filemind.log*' \) -printf '%f\0'
)
if [[ "${#log_files[@]}" -eq 0 ]]; then
    printf 'No filemind or performance logs found in %s\n' "$log_dir" >&2
    exit 3
fi
sudo tar -czf "$archive" -C "$log_dir" -- "${log_files[@]}"
sudo chmod 0644 "$archive"
printf 'Packed application and performance logs from %s.\n' "$log_dir"
'@

try {
    $remoteScript = $remoteScript -replace "`r`n?", "`n"
    $encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteScript))
    $remoteCommand = @(
        "sudo -v && printf '%s' $(ConvertTo-ShellArgument $encodedScript) | base64 -d | bash -s --"
        (ConvertTo-ShellArgument $RemoteLogDirectory)
        (ConvertTo-ShellArgument $remoteArchive)
    ) -join " "
    & ssh.exe -tt $Server $remoteCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Could not prepare remote logs (exit code $LASTEXITCODE)"
    }

    & scp.exe "${Server}:$remoteArchive" $localArchive
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
    & ssh.exe $Server "rm -f -- '$remoteArchive'" 2>$null | Out-Null
}
