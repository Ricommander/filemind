[CmdletBinding()]
param(
    [string]$Server = "fritzleserver",
    [string]$RemoteInstallDir = "/opt/filemind",
    [string]$RemoteConfigPath = "/etc/filemind/config.yaml",
    [string]$ServiceName = "filemind",
    [string]$ServiceUser = "filemind"
)

$ErrorActionPreference = "Stop"

foreach ($commandName in @("ssh.exe", "scp.exe", "tar.exe")) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $commandName"
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$stageDir = Join-Path $env:TEMP ("filemind-deploy-" + [guid]::NewGuid().ToString("N"))
$archivePath = "$stageDir.tar.gz"
$remoteArchive = "/tmp/filemind-update-$([guid]::NewGuid().ToString('N')).tar.gz"

function ConvertTo-ShellArgument([string]$Value) {
    return "'" + $Value.Replace("'", "'\''") + "'"
}

try {
    New-Item -ItemType Directory -Path $stageDir | Out-Null
    Copy-Item (Join-Path $repoRoot "main.py") $stageDir
    Copy-Item (Join-Path $repoRoot "requirements.txt") $stageDir
    Copy-Item (Join-Path $repoRoot "config.yaml") $stageDir
    Copy-Item -Recurse (Join-Path $repoRoot "filemind") $stageDir

    Get-ChildItem -Path (Join-Path $stageDir "filemind") -Directory -Filter "__pycache__" -Recurse |
        Sort-Object { $_.FullName.Length } -Descending |
        Remove-Item -Recurse -Force
    Get-ChildItem -Path (Join-Path $stageDir "filemind") -File -Filter "*.pyc" -Recurse |
        Remove-Item -Force

    & tar.exe -czf $archivePath -C $stageDir .
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create deployment archive (exit code $LASTEXITCODE)"
    }

    & scp.exe $archivePath "${Server}:$remoteArchive"
    if ($LASTEXITCODE -ne 0) {
        throw "SCP upload failed with exit code $LASTEXITCODE"
    }

    $remoteScript = @'
set -euo pipefail
archive="$1"
install_dir="$2"
service="$3"
service_user="$4"
config_path="$5"
config_tmp="${config_path}.new.$$"
stopped=0
cleanup() {
    status=$?
    trap - EXIT
    if [[ "$stopped" -eq 1 ]]; then
        sudo systemctl start "$service" || true
    fi
    sudo rm -f -- "$config_tmp" || true
    rm -f -- "$archive" || true
    exit "$status"
}
trap cleanup EXIT

if ! command -v exiftool >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y libimage-exiftool-perl
fi

sudo systemctl stop "$service"
stopped=1
sudo mkdir -p "$install_dir"
sudo tar -xzf "$archive" -C "$install_dir"
sudo chown -R "$service_user:$service_user" \
    "$install_dir/main.py" "$install_dir/filemind" "$install_dir/requirements.txt"
sudo mkdir -p "$(dirname "$config_path")"
sudo install -o "$service_user" -g "$service_user" -m 0640 \
    "$install_dir/config.yaml" "$config_tmp"
sudo mv -f -- "$config_tmp" "$config_path"
sudo rm -f -- "$install_dir/config.yaml"
sudo -u "$service_user" "$install_dir/.venv/bin/python" -m pip install \
    -r "$install_dir/requirements.txt"
sudo systemctl start "$service"
sudo systemctl is-active --quiet "$service"
stopped=0
trap - EXIT
rm -f -- "$archive"
printf 'Updated %s and confirmed service %s is active.\n' "$install_dir" "$service"
'@

    $remoteScript = $remoteScript -replace "`r`n?", "`n"
    $encodedScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteScript))
    $remoteCommand = @(
        "sudo -v || { rm -f -- $(ConvertTo-ShellArgument $remoteArchive); exit 1; };"
        "printf '%s' $(ConvertTo-ShellArgument $encodedScript) | base64 -d | bash -s --"
        (ConvertTo-ShellArgument $remoteArchive)
        (ConvertTo-ShellArgument $RemoteInstallDir)
        (ConvertTo-ShellArgument $ServiceName)
        (ConvertTo-ShellArgument $ServiceUser)
        (ConvertTo-ShellArgument $RemoteConfigPath)
    ) -join " "
    & ssh.exe -tt $Server $remoteCommand
    if ($LASTEXITCODE -ne 0) {
        throw "Remote deployment failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -Path $stageDir, $archivePath -Recurse -Force -ErrorAction SilentlyContinue
}
