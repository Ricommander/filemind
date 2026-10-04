[CmdletBinding()]
param(
    [string]$Server = "fritzleserver",
    [string]$RemoteInstallDir = "/opt/filemind",
    [string]$RemoteConfigPath = "/etc/filemind/config.yaml",
    [string]$ServiceName = "filemind",
    [string]$ServiceUser = "filemind",
    [string]$RepositoryPath,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"

if ($RemoteInstallDir -ne "/opt/filemind" -or
    $RemoteConfigPath -ne "/etc/filemind/config.yaml" -or
    $ServiceName -ne "filemind" -or
    $ServiceUser -ne "filemind") {
    throw "The protected deploy wrapper uses fixed filemind service paths and identity."
}

foreach ($commandName in @("ssh.exe", "scp.exe", "tar.exe")) {
    if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $commandName"
    }
}

$repoRoot = if ($RepositoryPath) {
    (Resolve-Path $RepositoryPath).Path
} else {
    (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
$checkCommand = "sudo -n /usr/local/sbin/filemind-deploy --check"
& ssh.exe -o BatchMode=yes -T $Server $checkCommand
if ($LASTEXITCODE -ne 0) {
    throw "The passwordless filemind deploy wrapper is not installed or is unhealthy."
}
if ($CheckOnly) {
    Write-Output "Remote filemind deploy wrapper is ready on $Server."
    return
}

$stageDir = Join-Path $env:TEMP ("filemind-deploy-" + [guid]::NewGuid().ToString("N"))
$archivePath = "$stageDir.tar.gz"
$releaseId = [guid]::NewGuid().ToString("N")
$remoteArchive = "/tmp/filemind-deploy-$releaseId.tar.gz"
$uploaded = $false

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

    $allowedFiles = @("main.py", "requirements.txt", "config.yaml")
    $stagedFiles = Get-ChildItem -Path $stageDir -File -Recurse | ForEach-Object {
        [System.IO.Path]::GetRelativePath($stageDir, $_.FullName).Replace("\", "/")
    }
    foreach ($file in $stagedFiles) {
        if ($file -notin $allowedFiles -and $file -notmatch '^filemind/.+\.py$') {
            throw "Refusing to package unexpected runtime file: $file"
        }
    }

    & tar.exe -czf $archivePath -C $stageDir main.py requirements.txt config.yaml filemind
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create deployment archive (exit code $LASTEXITCODE)"
    }

    $uploaded = $true
    & scp.exe -o BatchMode=yes $archivePath "${Server}:$remoteArchive"
    if ($LASTEXITCODE -ne 0) {
        throw "SCP upload failed with exit code $LASTEXITCODE"
    }

    & ssh.exe -o BatchMode=yes -T $Server "chmod 0600 -- '$remoteArchive' && printf '%s\n' '$releaseId' | sudo -n /usr/local/sbin/filemind-deploy --deploy"
    if ($LASTEXITCODE -ne 0) {
        throw "Remote deployment failed with exit code $LASTEXITCODE"
    }
    $uploaded = $false
    Write-Output "Deployed tested release $releaseId to $Server."
}
finally {
    Remove-Item -Path $stageDir, $archivePath -Recurse -Force -ErrorAction SilentlyContinue
    if ($uploaded) {
        & ssh.exe -o BatchMode=yes -T $Server "rm -f -- '$remoteArchive'" 2>$null | Out-Null
    }
}
