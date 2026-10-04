function Invoke-FilemindTestRepairLoop {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$TestRunner,
        [Parameter(Mandatory = $true)]
        [scriptblock]$RepairRunner
    )

    $repairAttempts = 0
    while ($true) {
        $testResult = & $TestRunner
        if ($null -eq $testResult -or $null -eq $testResult.ExitCode) {
            throw "The test runner returned no exit code."
        }

        if ([int]$testResult.ExitCode -eq 0) {
            return [pscustomobject]@{
                RepairAttempts = $repairAttempts
                TestOutput     = [string]$testResult.Output
            }
        }

        $repairAttempts++
        $repairResult = & $RepairRunner ([string]$testResult.Output) $repairAttempts
        if ($null -eq $repairResult -or $null -eq $repairResult.ExitCode) {
            throw "The repair agent returned no exit code after failed tests."
        }
        if ([int]$repairResult.ExitCode -ne 0) {
            throw "The repair agent failed with exit code $($repairResult.ExitCode): $($repairResult.Output)"
        }
    }
}

function Get-FilemindCopilotArguments {
    [CmdletBinding()]
    param()

    return @(
        "--allow-all-tools",
        "--available-tools=view,glob,grep,edit,create,apply_patch,skill",
        "--deny-tool=shell",
        "--disable-builtin-mcps",
        "--no-ask-user",
        "--no-auto-update"
    )
}

function Get-FilemindRuntimePayloadDigest {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryPath
    )

    $repository = (Resolve-Path $RepositoryPath).Path
    $relativeFiles = @("main.py", "requirements.txt", "config.yaml")
    $relativeFiles += Get-ChildItem -Path (Join-Path $repository "filemind") -Filter "*.py" -File -Recurse |
        ForEach-Object { [System.IO.Path]::GetRelativePath($repository, $_.FullName).Replace("\", "/") }
    $entries = foreach ($relative in $relativeFiles | Sort-Object -Unique) {
        $filePath = Join-Path $repository ($relative.Replace("/", "\"))
        if (-not (Test-Path $filePath -PathType Leaf)) {
            throw "Runtime payload file is missing: $relative"
        }
        "$relative $((Get-FileHash $filePath -Algorithm SHA256).Hash)"
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($entries -join "`n")
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [Convert]::ToHexString($hasher.ComputeHash($bytes)).ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
    }
}

function Invoke-FilemindPromptProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExecutablePath,
        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$Prompt,
        [ValidateRange(1, 7200)]
        [int]$TimeoutSeconds = 1800
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $ExecutablePath
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $Arguments | ForEach-Object { $startInfo.ArgumentList.Add($_) }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw "Could not start process '$ExecutablePath'."
        }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.StandardInput.WriteLine($Prompt)
        $process.StandardInput.Close()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            $process.Kill()
            $process.WaitForExit()
            throw "Process '$ExecutablePath' timed out after $TimeoutSeconds seconds."
        }
        $process.WaitForExit()
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Output = $stdout.GetAwaiter().GetResult() + $stderr.GetAwaiter().GetResult()
        }
    }
    finally {
        $process.Dispose()
    }
}

Export-ModuleMember -Function Invoke-FilemindTestRepairLoop, Get-FilemindCopilotArguments, Get-FilemindRuntimePayloadDigest, Invoke-FilemindPromptProcess