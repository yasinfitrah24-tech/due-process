#!/usr/bin/env bash
# Idempotent VM setup: Docker and compose from Ubuntu's own repository, and the app folder.
# Changes no firewall or kernel setting; deploy/vm-harden.sh does that, run by a person.
set -euo pipefail

APP_DIR=/opt/due-process

export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  apt-get update -q
  apt-get install -y -q docker.io docker-compose-v2
fi
systemctl enable --now docker
mkdir -p "$APP_DIR"
docker --version
docker compose version
