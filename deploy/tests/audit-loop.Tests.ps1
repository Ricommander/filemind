$modulePath = Join-Path $PSScriptRoot "..\filemind-audit-loop.psm1"
Import-Module $modulePath -Force

Describe "Invoke-FilemindTestRepairLoop" {
    It "repairs repeatedly until the test runner succeeds" {
        $state = [pscustomobject]@{ TestIndex = 0; RepairCount = 0 }
        $testResults = @(
            [pscustomobject]@{ ExitCode = 1; Output = "failure one" },
            [pscustomobject]@{ ExitCode = 1; Output = "failure two" },
            [pscustomobject]@{ ExitCode = 0; Output = "all passed" }
        )
        $testRunner = {
            $result = $testResults[$state.TestIndex]
            $state.TestIndex++
            return $result
        }
        $repairRunner = {
            param($failureOutput, $attempt)
            $state.RepairCount++
            [pscustomobject]@{ ExitCode = 0; Output = "repair $attempt for $failureOutput" }
        }

        $result = Invoke-FilemindTestRepairLoop -TestRunner $testRunner -RepairRunner $repairRunner

        $state.TestIndex | Should Be 3
        $state.RepairCount | Should Be 2
        $result.RepairAttempts | Should Be 2
        $result.TestOutput | Should Be "all passed"
    }

    It "stops when the agent cannot run, rather than deploying with failing tests" {
        $testRunner = { [pscustomobject]@{ ExitCode = 1; Output = "test failed" } }
        $repairRunner = { [pscustomobject]@{ ExitCode = 1; Output = "credits exhausted" } }

        {
            Invoke-FilemindTestRepairLoop -TestRunner $testRunner -RepairRunner $repairRunner
        } | Should Throw "The repair agent failed with exit code 1: credits exhausted"
    }
}

Describe "Get-FilemindCopilotArguments" {
    It "uses the current non-interactive CLI with no shell, MCP, or user-question tools" {
        $arguments = Get-FilemindCopilotArguments -Prompt "audit prompt"
        $availableTools = $arguments | Where-Object { $_ -like "--available-tools=*" }

        (@($arguments) -contains "--allow-all-tools") | Should Be $true
        (@($arguments) -contains "--deny-tool=shell") | Should Be $true
        (@($arguments) -contains "--disable-builtin-mcps") | Should Be $true
        (@($arguments) -contains "--no-ask-user") | Should Be $true
        $availableTools | Should Be "--available-tools=view,glob,grep,edit,create,apply_patch,skill"
        (@($arguments) -contains "audit prompt") | Should Be $true
    }
}

Describe "Get-FilemindRuntimePayloadDigest" {
    It "ignores documentation changes and detects runtime code changes" {
        $root = Join-Path ([System.IO.Path]::GetTempPath()) ("filemind-digest-test-" + [guid]::NewGuid().ToString("N"))
        $package = Join-Path $root "filemind"
        New-Item -ItemType Directory -Path $package -Force | Out-Null
        Set-Content -Path (Join-Path $root "main.py") -Value "main = 1"
        Set-Content -Path (Join-Path $root "requirements.txt") -Value "requests"
        Set-Content -Path (Join-Path $root "config.yaml") -Value "language: en"
        Set-Content -Path (Join-Path $package "__init__.py") -Value "__version__ = '1'"
        try {
            $initial = Get-FilemindRuntimePayloadDigest -RepositoryPath $root
            Set-Content -Path (Join-Path $root "README.md") -Value "docs only"
            $documentationOnly = Get-FilemindRuntimePayloadDigest -RepositoryPath $root
            Set-Content -Path (Join-Path $package "__init__.py") -Value "__version__ = '2'"
            $runtimeChanged = Get-FilemindRuntimePayloadDigest -RepositoryPath $root

            $documentationOnly | Should Be $initial
            ($runtimeChanged -ne $initial) | Should Be $true
        }
        finally {
            Remove-Item -Path $root -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}