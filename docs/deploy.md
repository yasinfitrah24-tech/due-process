# Deploy

One Vultr VM: Ubuntu 24.04, Docker from Ubuntu's repository, the stack from `docker-compose.yml` plus
`deploy/compose.vm.yml`. Public URL: `https://45-76-241-186.sslip.io`. Everything runs from `/opt/due-process`.

The VM has no access to the private repository, so `deploy/deploy.sh` sends the exact `origin/y/thin-slice` commit as an
archive. It refuses to run if local `HEAD` differs from origin.

## First deploy

1. Run the deploy from the laptop. It installs Docker, copies the code and creates `.env` without a key, then stops:

   ```bash
   SSH=/c/WINDOWS/System32/OpenSSH/ssh.exe deploy/deploy.sh
   ```

2. On the VM, add the key by hand. Never copy `.env` from a laptop:

   ```bash
   ssh dp-vm
   nano /opt/due-process/.env
   ```

3. On the VM, set the firewall. It allows 22 first, so SSH stays open:

   ```bash
   bash /opt/due-process/deploy/vm-harden.sh
   ```

4. Run the deploy again. It starts the stack.

Run the deploy again for every update. `.env` and all Docker volumes stay.

## What each file does

| File | Runs on | Purpose |
|---|---|---|
| `deploy/deploy.sh` | laptop | Copies the pushed code, starts the stack |
| `deploy/vm-setup.sh` | VM | Installs Docker and compose |
| `deploy/vm-env.sh` | VM | Creates `.env`, sets everything but the key |
| `deploy/vm-harden.sh` | VM, by hand | Firewall |
| `deploy/b3_check.py` | VM | Chromium's own sandbox under the contract flags |
| `deploy/vm_netcheck.py` | VM | Network test inside a real sandbox |

## Checks on the VM

```bash
cd /opt/due-process && python3 deploy/b3_check.py
```

```bash
cd /opt/due-process && python3 deploy/vm_netcheck.py 45.76.241.186 45-76-241-186.sslip.io
```

## Notes

- `docker compose` publishes ports past `ufw`. Only Caddy publishes any, 80 and 443.
- The app has no published port. It trusts `X-Forwarded-For` only from the address of the `caddy` container.
- `EGRESS_DENY_IPS` holds the VM's public address, so a page in the sandbox cannot reach the public app through the proxy.
- Do not run `do-release-upgrade`.
