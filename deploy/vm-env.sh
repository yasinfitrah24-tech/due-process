#!/usr/bin/env bash
# Run on the VM from the app folder. Creates .env if missing and sets everything except the key, which a person adds.
# Never reads or prints the key, and leaves an existing key alone.
set -euo pipefail

PUBLIC_IP=${1:?usage: vm-env.sh PUBLIC_IP}
PUBLIC_HOST="${PUBLIC_IP//./-}.sslip.io"

[ -f .env ] || cp .env.example .env
chmod 600 .env

set_value() {
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    echo "$1=$2" >> .env
  fi
}

set_value VULTR_MODEL deepseek-v4.1-flash-1017
set_value VULTR_VISION_MODEL nemotron-3-nano-omni-30b-a3b-reasoning
set_value PUBLIC_HOST "$PUBLIC_HOST"
set_value EGRESS_DENY_IPS "$PUBLIC_IP"
set_value COMPOSE_FILE docker-compose.yml:deploy/compose.vm.yml

if grep -q '^VULTR_API_KEY=.' .env; then echo "key: set"; else echo "key: EMPTY"; fi
echo "public host: $PUBLIC_HOST"
