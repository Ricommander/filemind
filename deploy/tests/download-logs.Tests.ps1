$modulePath = Join-Path $PSScriptRoot "..\filemind-log-remote.psm1"
Import-Module $modulePath -Force

Describe "New-FilemindLogArchiveScript" {
    It "does not require sudo when the SSH account can read logs" {
        $script = New-FilemindLogArchiveScript

        $script | Should Not Match "sudo"
        $script | Should Match "find .*log_dir"
        $script | Should Match "tar -czf -"
    }

    It "supports sudo-based log access when explicitly requested" {
        $script = New-FilemindLogArchiveScript -UseSudo

        $script | Should Match "sudo -v"
        $script | Should Match "sudo find"
        $script | Should Match "sudo tar"
    }
}