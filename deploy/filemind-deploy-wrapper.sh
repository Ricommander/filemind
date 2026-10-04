#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly INSTALL_ROOT="/opt/filemind"
readonly RELEASES_DIR="$INSTALL_ROOT/releases"
readonly CURRENT_LINK="$INSTALL_ROOT/current"
readonly SHARED_STATE="$INSTALL_ROOT/.filemind"
readonly CONFIG_PATH="/etc/filemind/config.yaml"
readonly ARCHIVE_VALIDATOR="/usr/local/libexec/filemind-deploy-archive.py"
readonly SERVICE_NAME="filemind"
readonly SERVICE_USER="filemind"
readonly SERVICE_GROUP="filemind"
readonly DEPLOY_USER="filemind-deploy"

fail() {
    printf 'filemind deploy: %s\n' "$1" >&2
    exit 1
}

if [[ "${1:-}" == "--check" ]]; then
    [[ $# -eq 1 ]] || fail "--check accepts no additional arguments"
    [[ $EUID -eq 0 ]] || fail "must run as root"
    [[ -L "$CURRENT_LINK" ]] || fail "current release symlink is missing"
    current_target="$(readlink -f -- "$CURRENT_LINK")"
    [[ "$current_target" == "$RELEASES_DIR/"* ]] || fail "current release points outside the releases directory"
    [[ -d "$SHARED_STATE" && ! -L "$SHARED_STATE" ]] || fail "shared .filemind state directory is missing"
    [[ -f "$CONFIG_PATH" ]] || fail "active config is missing"
    [[ -f "$ARCHIVE_VALIDATOR" ]] || fail "archive validator is missing"
    id "$DEPLOY_USER" >/dev/null 2>&1 || fail "isolated build user is missing"
    [[ -x "$CURRENT_LINK/.venv/bin/python" ]] || fail "current Python environment is missing"
    [[ -f "$CURRENT_LINK/main.py" ]] || fail "current application entry point is missing"
    grep -Fq "WorkingDirectory=$CURRENT_LINK" "/etc/systemd/system/$SERVICE_NAME.service" ||
        fail "systemd unit is not configured for the current release"
    systemctl is-active --quiet "$SERVICE_NAME" || fail "service is not active"
    printf 'filemind deploy wrapper is ready; current release: %s\n' "${current_target##*/}"
    exit 0
fi

[[ $EUID -eq 0 ]] || fail "must run as root"
[[ $# -eq 1 && "${1:-}" == "--deploy" ]] || fail "expected the fixed --deploy operation"
[[ -n "${SUDO_USER:-}" ]] || fail "SUDO_USER is required"
id -u "$SUDO_USER" >/dev/null 2>&1 || fail "unknown invoking user"
IFS= read -r release_id || fail "release ID must be provided on standard input"
if IFS= read -r extra_input; then
    fail "expected exactly one release ID on standard input"
fi
[[ "$release_id" =~ ^[0-9a-f]{32}$ ]] || fail "release ID must be 32 lowercase hexadecimal characters"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v runuser >/dev/null 2>&1 || fail "runuser is required"
command -v systemctl >/dev/null 2>&1 || fail "systemctl is required"
id "$DEPLOY_USER" >/dev/null 2>&1 || fail "isolated build user is missing"

archive="/tmp/filemind-deploy-$release_id.tar.gz"
staging="$RELEASES_DIR/.staging-$release_id"
release="$RELEASES_DIR/$release_id"
config_backup_dir="/var/backups/filemind-deploy/config"
config_backup="$config_backup_dir/$release_id.yaml"
config_tmp="/etc/filemind/.config.yaml.new-$release_id"
link_tmp="$INSTALL_ROOT/.current-$release_id"
previous_link=""
service_stopped=0
config_swapped=0
link_swapped=0
archive_owned=0
committed=0

rollback() {
    status=$?
    trap - EXIT
    if [[ "$status" -ne 0 && "$committed" -ne 1 ]]; then
        if [[ "$service_stopped" -eq 1 ]]; then
            systemctl stop "$SERVICE_NAME" || true
        fi
        if [[ "$config_swapped" -eq 1 && -f "$config_backup" ]]; then
            restore_tmp="/etc/filemind/.config.yaml.restore-$release_id"
            install -o root -g "$SERVICE_GROUP" -m 0640 "$config_backup" "$restore_tmp" || true
            mv -f -- "$restore_tmp" "$CONFIG_PATH" || true
        fi
        if [[ "$link_swapped" -eq 1 && -n "$previous_link" ]]; then
            ln -s "$previous_link" "$link_tmp" || true
            mv -Tf -- "$link_tmp" "$CURRENT_LINK" || true
        fi
        if [[ "$service_stopped" -eq 1 ]]; then
            systemctl start "$SERVICE_NAME" || true
        fi
    fi
    rm -f -- "$config_tmp" "$link_tmp"
    if [[ -n "$staging" && -d "$staging" ]]; then
        rm -rf -- "$staging"
    fi
    if [[ "$archive_owned" -eq 1 ]]; then
        rm -f -- "$archive"
    fi
    exit "$status"
}
trap rollback EXIT

[[ -f "$archive" && ! -L "$archive" ]] || fail "upload archive is missing or is not a regular file"
[[ "$(stat -c '%u' -- "$archive")" == "$(id -u "$SUDO_USER")" ]] ||
    fail "upload archive is not owned by the invoking SSH user"
archive_mode="$(stat -c '%a' -- "$archive")"
(( (8#$archive_mode & 077) == 0 )) || fail "upload archive must not be accessible to other users"
archive_owned=1
archive_size="$(stat -c '%s' -- "$archive")"
(( archive_size <= 104857600 )) || fail "upload archive exceeds 100 MiB"
[[ -L "$CURRENT_LINK" ]] || fail "current release symlink is missing"
[[ ! -e "$staging" && ! -e "$release" && ! -e "$link_tmp" ]] || fail "release ID already exists"
[[ -d "$SHARED_STATE" && ! -L "$SHARED_STATE" ]] || fail "shared state directory is missing"
[[ -f "$CONFIG_PATH" ]] || fail "active config is missing"

mkdir -p -- "$RELEASES_DIR"
install -d -o root -g "$SERVICE_GROUP" -m 0750 -- "$config_backup_dir"
install -d -o "$DEPLOY_USER" -g "$SERVICE_GROUP" -m 0700 -- "$staging"

python3 "$ARCHIVE_VALIDATOR" "$archive" "$staging"

ln -s "$SHARED_STATE" "$staging/.filemind"
chown -R "$DEPLOY_USER:$DEPLOY_USER" "$staging"
find "$staging" -type d -exec chmod 0700 {} +
find "$staging" -type f -exec chmod 0600 {} +
runuser -u "$DEPLOY_USER" -- python3 -m venv "$staging/.venv"
runuser -u "$DEPLOY_USER" -- env PIP_NO_CACHE_DIR=1 \
    "$staging/.venv/bin/python" -m pip install -r "$staging/requirements.txt"

chown -R "root:$SERVICE_GROUP" "$staging"
find "$staging" -type d -exec chmod 0750 {} +
find "$staging" -type f -exec chmod 0640 {} +
find "$staging/.venv/bin" -maxdepth 1 -type f -exec chmod 0750 {} +
mv -- "$staging" "$release"
staging=""

previous_link="$(readlink -- "$CURRENT_LINK")"
[[ "$previous_link" == releases/* ]] || fail "previous release link is not relative to releases"
cp -p -- "$CONFIG_PATH" "$config_backup"
install -o root -g "$SERVICE_GROUP" -m 0640 \
    "$release/config.yaml" "$config_tmp"

service_stopped=1
systemctl stop "$SERVICE_NAME"
config_swapped=1
mv -f -- "$config_tmp" "$CONFIG_PATH"
ln -s "releases/$release_id" "$link_tmp"
link_swapped=1
mv -Tf -- "$link_tmp" "$CURRENT_LINK"
systemctl start "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME"
committed=1
service_stopped=0

printf 'Deployed release %s; previous release %s remains available for rollback.\n' \
    "$release_id" "${previous_link#releases/}"