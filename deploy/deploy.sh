#!/usr/bin/env bash
# Run on the laptop: deploys origin/main to the VM. Safe to run again.
# The VM has no access to the private repository, so the exact origin commit is sent as an archive.
# Windows: SSH=/c/WINDOWS/System32/OpenSSH/ssh.exe deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SSH=${SSH:-ssh}
HOST=${HOST:-dp-vm}
PUBLIC_IP=${PUBLIC_IP:-45.76.241.186}
BRANCH=main
APP_DIR=/opt/due-process

git fetch -q origin "$BRANCH"
if [ "$(git rev-parse HEAD)" != "$(git rev-parse "origin/$BRANCH")" ]; then
  echo "HEAD differs from origin/$BRANCH. Deploy only what is pushed; ask before pushing." >&2
  exit 1
fi

"$SSH" "$HOST" 'bash -s' < deploy/vm-setup.sh

# .env, outside the archive, stays. Everything else is replaced, so a deleted file does not linger.
"$SSH" "$HOST" "cd $APP_DIR && find . -mindepth 1 -maxdepth 1 ! -name .env -exec rm -rf {} +"
git archive "origin/$BRANCH" | "$SSH" "$HOST" "tar xf - -C $APP_DIR"
"$SSH" "$HOST" "cd $APP_DIR && bash deploy/vm-env.sh $PUBLIC_IP"

if ! "$SSH" "$HOST" "grep -q '^VULTR_API_KEY=.' $APP_DIR/.env"; then
  echo "VULTR_API_KEY is empty. Add it on the VM ($APP_DIR/.env), then run this again." >&2
  exit 2
fi

"$SSH" "$HOST" "cd $APP_DIR && docker build -q -t due-process-sandbox:v1.64.1 sandbox && docker compose up -d --build --wait"
"$SSH" "$HOST" "cd $APP_DIR && docker compose ps --format '{{.Service}} {{.Status}}'"
