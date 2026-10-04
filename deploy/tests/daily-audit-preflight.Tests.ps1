Describe "run-daily-audit preflight" {
    It "records the missing log-upload consent without contacting the server" {
        $pwshPath = Join-Path $PSHOME "pwsh.exe"
        $script = Join-Path $PSScriptRoot "..\run-daily-audit.ps1"
        $root = Join-Path ([System.IO.Path]::GetTempPath()) ("filemind-audit-test-" + [guid]::NewGuid().ToString("N"))
        $mutexName = "Local\filemind-audit-test-$([guid]::NewGuid().ToString('N'))"
        $copilotExecutable = Join-Path $env:SystemRoot "System32\where.exe"

        try {
            & $pwshPath -NoProfile -File $script -AutomationRoot $root `
            -CopilotCommand $copilotExecutable -MutexName $mutexName *> $null
            $exitCode = $LASTEXITCODE
            $report = Get-ChildItem (Join-Path $root "runs") -Filter "report.txt" -Recurse |
                Select-Object -First 1

            $exitCode | Should Be 1
            $report | Should Not BeNullOrEmpty
            (Get-Content $report.FullName -Raw) | Should Match "Pass -AllowLogUpload"
        }
        finally {
            Remove-Item -Path $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}