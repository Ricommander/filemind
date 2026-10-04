Describe "Passwordless deploy command boundary" {
    It "allows only fixed check and deploy operations in sudoers" {
        $installerPath = Join-Path $PSScriptRoot "..\install-auto-deploy.sh"
        $rules = Get-Content $installerPath | Where-Object { $_ -match "NOPASSWD" }
        $uniqueRules = @($rules | ForEach-Object { $_.Trim() } | Sort-Object -Unique)
        $ruleText = $uniqueRules -join "`n"

        $uniqueRules.Count | Should Be 2
        ($ruleText.Contains("NOPASSWD: %s --check\n")) | Should Be $true
        ($ruleText.Contains("NOPASSWD: %s --deploy\n")) | Should Be $true
        ($ruleText -match '[?*]') | Should Be $false
    }

    It "passes the release ID on stdin to the fixed wrapper operation" {
        $wrapper = Get-Content (Join-Path $PSScriptRoot "..\filemind-deploy-wrapper.sh") -Raw
        $updater = Get-Content (Join-Path $PSScriptRoot "..\update-server.ps1") -Raw
        $expectedCommand = "printf '%s\n' '`$releaseId' | sudo -n /usr/local/sbin/filemind-deploy --deploy"

        ($wrapper.Contains("read -r release_id")) | Should Be $true
        ($updater.Contains($expectedCommand)) | Should Be $true
    }
}