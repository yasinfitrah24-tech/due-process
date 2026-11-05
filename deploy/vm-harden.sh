#!/usr/bin/env bash
# Run by a person on the VM. Firewall: only 22, 80 and 443 from outside. Safe to run again.
# 22 is allowed before anything else, so this cannot lock SSH out.
set -euo pipefail

ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw default deny incoming
ufw --force enable
ufw status verbose
