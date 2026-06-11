#!/usr/bin/env bash
# Installs filemind as a systemd service on a Linux server.
#
# Usage: sudo ./deploy/install.sh
#
# What it does:
#   1. Creates the system user "filemind".
#   2. Copies the project to /opt/filemind and creates a virtualenv there.
#   3. Installs the configuration to /etc/filemind/config.yaml (kept on re-runs).
#   4. Installs and enables the systemd unit "filemind".

set -euo pipefail

INSTALL_DIR="/opt/filemind"
CONFIG_DIR="/etc/filemind"
SERVICE_USER="filemind"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Bitte mit sudo/als root ausführen." >&2
    exit 1
fi

echo "==> Erstelle Service-User '${SERVICE_USER}'"
if ! id -u "${SERVICE_USER}" &>/dev/null; then
    useradd --system --home-dir "${INSTALL_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

echo "==> Kopiere Projekt nach ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '.filemind' \
    --exclude 'tests' \
    "${REPO_DIR}/" "${INSTALL_DIR}/"

echo "==> Erstelle virtuelles Environment und installiere Abhängigkeiten"
if [[ ! -d "${INSTALL_DIR}/.venv" ]]; then
    python3 -m venv "${INSTALL_DIR}/.venv"
fi
"${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
"${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "==> Installiere Konfiguration nach ${CONFIG_DIR}"
mkdir -p "${CONFIG_DIR}"
if [[ ! -f "${CONFIG_DIR}/config.yaml" ]]; then
    cp "${REPO_DIR}/config.yaml" "${CONFIG_DIR}/config.yaml"
    echo "    Neue Konfiguration angelegt - bitte ${CONFIG_DIR}/config.yaml prüfen!"
else
    echo "    Bestehende Konfiguration unverändert gelassen."
fi

echo "==> Setze Berechtigungen"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${CONFIG_DIR}"

echo "==> Installiere systemd-Unit"
cp "${REPO_DIR}/deploy/filemind.service" /etc/systemd/system/filemind.service
systemctl daemon-reload
systemctl enable filemind

echo "==> Starte Service"
systemctl restart filemind
systemctl --no-pager status filemind || true

echo
echo "Fertig. Nützliche Befehle:"
echo "  journalctl -u filemind -f          # Logs verfolgen"
echo "  systemctl restart filemind         # Service neu starten"
echo "  ${CONFIG_DIR}/config.yaml          # Konfiguration anpassen"
