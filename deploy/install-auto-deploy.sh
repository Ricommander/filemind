#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

readonly INSTALL_ROOT="/opt/filemind"
readonly CONFIG_PATH="/etc/filemind/config.yaml"
readonly SERVICE_NAME="filemind"
readonly SERVICE_USER="filemind"
readonly SERVICE_GROUP="filemind"
readonly DEPLOY_USER="filemind-deploy"
readonly DEPLOY_GROUP="filemind-deploy"
readonly WRAPPER_PATH="/usr/local/sbin/filemind-deploy"
readonly ARCHIVE_VALIDATOR_PATH="/usr/local/libexec/filemind-deploy-archive.py"
readonly SUDOERS_PATH="/etc/sudoers.d/filemind-deploy"

fail() {
    printf 'filemind auto-deploy install: %s\n' "$1" >&2
    exit 1
}

[[ $EUID -eq 0 ]] || fail "run with sudo from the SSH account that will deploy releases"
[[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]] || fail "SUDO_USER must be the non-root SSH deployment account"
[[ "$SUDO_USER" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || fail "unsupported SSH account name"
id "$SERVICE_USER" >/dev/null 2>&1 || fail "service user '$SERVICE_USER' does not exist"
getent group "$SERVICE_GROUP" >/dev/null || fail "service group '$SERVICE_GROUP' does not exist"
if ! getent passwd "$DEPLOY_USER" >/dev/null; then
    useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin "$DEPLOY_USER"
fi
getent group "$DEPLOY_GROUP" >/dev/null || fail "deployment build group is missing"
[[ -d "$INSTALL_ROOT" && -f "$INSTALL_ROOT/main.py" ]] || fail "existing installation was not found at $INSTALL_ROOT"
[[ -d "$INSTALL_ROOT/filemind" && -f "$INSTALL_ROOT/requirements.txt" ]] || fail "existing runtime source is incomplete"
[[ -x "$INSTALL_ROOT/.venv/bin/python" ]] || fail "existing virtual environment is missing"
[[ -d "$INSTALL_ROOT/.filemind" && ! -L "$INSTALL_ROOT/.filemind" ]] || fail "existing shared state directory is missing or is a symlink"
[[ -f "$CONFIG_PATH" ]] || fail "active configuration is missing at $CONFIG_PATH"
[[ ! -e "$INSTALL_ROOT/current" && ! -L "$INSTALL_ROOT/current" ]] || fail "a current-release link already exists; refusing to migrate twice"
[[ -f "$(dirname "$0")/filemind-deploy-wrapper.sh" ]] || fail "filemind-deploy-wrapper.sh must be beside this installer"
[[ -f "$(dirname "$0")/filemind.service" ]] || fail "filemind.service must be beside this installer"
[[ -f "$(dirname "$0")/deploy_archive.py" ]] || fail "deploy_archive.py must be beside this installer"

command -v systemctl >/dev/null 2>&1 || fail "systemd is required"
command -v visudo >/dev/null 2>&1 || fail "visudo is required"
command -v runuser >/dev/null 2>&1 || fail "runuser is required"
if ! command -v exiftool >/dev/null 2>&1; then
    command -v apt-get >/dev/null 2>&1 || fail "install ExifTool manually; apt-get is unavailable"
    apt-get update
    apt-get install -y libimage-exiftool-perl
fi

# The daemon may write only shared runtime state, never the release pointer.
chown root:root "$INSTALL_ROOT"
chmod 0755 "$INSTALL_ROOT"

install -d -o root -g root -m 0711 -- "$INSTALL_ROOT/releases"
shopt -s nullglob
bootstrap_candidates=("$INSTALL_ROOT"/releases/bootstrap-*)
shopt -u nullglob
if [[ ${#bootstrap_candidates[@]} -gt 1 ]]; then
    fail "multiple bootstrap releases exist; inspect them before continuing"
fi
if [[ ${#bootstrap_candidates[@]} -eq 1 ]]; then
    RELEASE_DIR="${bootstrap_candidates[0]}"
    [[ -d "$RELEASE_DIR" && ! -L "$RELEASE_DIR" ]] || fail "existing bootstrap release is not a real directory"
    [[ -f "$RELEASE_DIR/main.py" && -f "$RELEASE_DIR/filemind/__init__.py" ]] ||
        fail "existing bootstrap release is incomplete"
    [[ -L "$RELEASE_DIR/.venv" && "$(readlink -f "$RELEASE_DIR/.venv")" == "$INSTALL_ROOT/.venv" ]] ||
        fail "existing bootstrap release does not reference the preserved virtual environment"
    [[ -L "$RELEASE_DIR/.filemind" && "$(readlink -f "$RELEASE_DIR/.filemind")" == "$INSTALL_ROOT/.filemind" ]] ||
        fail "existing bootstrap release does not reference preserved runtime state"
    BOOTSTRAP_ID="${RELEASE_DIR##*/}"
    printf 'Reusing prepared bootstrap release %s.\n' "$BOOTSTRAP_ID"
else
    BOOTSTRAP_ID="bootstrap-$(date -u +%Y%m%d%H%M%S)"
    RELEASE_DIR="$INSTALL_ROOT/releases/$BOOTSTRAP_ID"
    install -d -o root -g "$SERVICE_GROUP" -m 0750 -- "$RELEASE_DIR"
    cp -- "$INSTALL_ROOT/main.py" "$INSTALL_ROOT/requirements.txt" "$RELEASE_DIR/"
    cp -a -- "$INSTALL_ROOT/filemind" "$RELEASE_DIR/filemind"
    find "$RELEASE_DIR/filemind" -type d -name __pycache__ -prune -exec rm -rf -- {} +
    find "$RELEASE_DIR/filemind" -type f -name '*.pyc' -delete
    ln -s "$INSTALL_ROOT/.venv" "$RELEASE_DIR/.venv"
    ln -s "$INSTALL_ROOT/.filemind" "$RELEASE_DIR/.filemind"
    chown -R "root:$SERVICE_GROUP" "$RELEASE_DIR"
    find "$RELEASE_DIR" -type d -exec chmod 0750 {} +
    find "$RELEASE_DIR" -type f -exec chmod 0640 {} +
fi

readonly BOOTSTRAP_ID
readonly RELEASE_DIR
readonly CURRENT_TMP="$INSTALL_ROOT/.current-$BOOTSTRAP_ID"
readonly UNIT_PATH="/etc/systemd/system/$SERVICE_NAME.service"
readonly UNIT_BACKUP="/etc/systemd/system/$SERVICE_NAME.service.before-auto-deploy-$BOOTSTRAP_ID"
readonly UNIT_TMP="/etc/systemd/system/$SERVICE_NAME.service.new-$BOOTSTRAP_ID"

install -o root -g root -m 0750 \
    "$(dirname "$0")/filemind-deploy-wrapper.sh" "$WRAPPER_PATH"
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0644 \
    "$(dirname "$0")/deploy_archive.py" "$ARCHIVE_VALIDATOR_PATH"
install -d -o root -g root -m 0700 /var/backups/filemind-deploy/config

sudoers_tmp="$(mktemp /etc/sudoers.d/.filemind-deploy.XXXXXX)"
trap 'rm -f -- "$sudoers_tmp" "$UNIT_TMP" "$CURRENT_TMP"' EXIT
{
    printf '%s ALL=(root) NOPASSWD: %s --check\n' "$SUDO_USER" "$WRAPPER_PATH"
    printf '%s ALL=(root) NOPASSWD: %s --deploy\n' "$SUDO_USER" "$WRAPPER_PATH"
} > "$sudoers_tmp"
chmod 0440 "$sudoers_tmp"
visudo -cf "$sudoers_tmp"
install -o root -g root -m 0440 "$sudoers_tmp" "$SUDOERS_PATH"

if [[ -f "$UNIT_PATH" ]]; then
    cp -a -- "$UNIT_PATH" "$UNIT_BACKUP"
fi
install -o root -g root -m 0644 \
    "$(dirname "$0")/filemind.service" "$UNIT_TMP"

service_stopped=0
unit_installed=0
link_installed=0
committed=0
rollback_install() {
    status=$?
    trap - EXIT
    if [[ "$status" -ne 0 && "$committed" -ne 1 ]]; then
        if [[ "$service_stopped" -eq 1 ]]; then
            systemctl stop "$SERVICE_NAME" || true
        fi
        if [[ "$link_installed" -eq 1 ]]; then
            rm -f -- "$INSTALL_ROOT/current"
        fi
        if [[ "$unit_installed" -eq 1 ]]; then
            if [[ -f "$UNIT_BACKUP" ]]; then
                cp -a -- "$UNIT_BACKUP" "$UNIT_PATH" || true
            else
                rm -f -- "$UNIT_PATH"
            fi
            systemctl daemon-reload || true
        fi
        if [[ "$service_stopped" -eq 1 ]]; then
            systemctl start "$SERVICE_NAME" || true
        fi
    fi
    rm -f -- "$UNIT_TMP" "$CURRENT_TMP" "$sudoers_tmp"
    exit "$status"
}
trap rollback_install EXIT

service_stopped=1
systemctl stop "$SERVICE_NAME"
unit_installed=1
mv -f -- "$UNIT_TMP" "$UNIT_PATH"
ln -s "releases/$BOOTSTRAP_ID" "$CURRENT_TMP"
link_installed=1
mv -Tf -- "$CURRENT_TMP" "$INSTALL_ROOT/current"
systemctl daemon-reload
systemctl start "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME"
systemctl enable "$SERVICE_NAME"
committed=1
service_stopped=0
trap - EXIT
rm -f -- "$sudoers_tmp" "$UNIT_TMP" "$CURRENT_TMP"

printf 'Installed restricted deploy wrapper for SSH user %s.\n' "$SUDO_USER"
printf 'Preserved active config at %s and runtime data under %s/.filemind.\n' \
    "$CONFIG_PATH" "$INSTALL_ROOT"
printf 'Bootstrapped release %s; service %s is active.\n' "$BOOTSTRAP_ID" "$SERVICE_NAME"