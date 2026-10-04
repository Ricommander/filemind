Describe "Daily audit task logon mode" {
    It "uses an interactive token and does not request or save a password" {
        $script = Get-Content (Join-Path $PSScriptRoot "..\register-daily-audit.ps1") -Raw
        $principal = New-ScheduledTaskPrincipal `
            -UserId $env:USERNAME `
            -LogonType Interactive `
            -RunLevel Limited
        $script = Get-Content (Join-Path $PSScriptRoot "..\register-daily-audit.ps1") -Raw

        $principal.LogonType | Should Be "Interactive"
        $script | Should Match "-LogonType Interactive"
        $script | Should Not Match "Get-Credential|GetNetworkCredential|-Password"
        $script | Should Match "AutomationRoot"
        $script | Should Match "Commit the reviewed repository changes"
    }
}