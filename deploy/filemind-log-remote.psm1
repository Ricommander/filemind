function New-FilemindLogArchiveScript {
    [CmdletBinding()]
    param(
        [switch]$UseSudo
    )

    $sudoPrefix = if ($UseSudo) { "sudo " } else { "" }
    $sudoValidate = if ($UseSudo) { "sudo -v" } else { ":" }

    $script = @'
set -euo pipefail
log_dir="$1"
archive="$2"
@@SUDO_VALIDATE@@
if ! @@SUDO@@test -d "$log_dir"; then
    printf 'Log directory not found or unreadable: %s\n' "$log_dir" >&2
    exit 2
fi
mapfile -d '' log_files < <(
    @@SUDO@@find "$log_dir" -maxdepth 1 -type f \
        \( -name 'performance.jsonl*' -o -name 'filemind.log*' \) -printf '%f\0'
)
if [[ "${#log_files[@]}" -eq 0 ]]; then
    printf 'No filemind or performance logs found in %s\n' "$log_dir" >&2
    exit 3
fi
@@SUDO@@tar -czf - -C "$log_dir" -- "${log_files[@]}" > "$archive"
chmod 0644 "$archive"
printf 'Packed application and performance logs from %s.\n' "$log_dir"
'@

    return $script.Replace("@@SUDO_VALIDATE@@", $sudoValidate).Replace("@@SUDO@@", $sudoPrefix)
}

Export-ModuleMember -Function New-FilemindLogArchiveScript