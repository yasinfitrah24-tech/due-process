# Blockers for a public deploy

Fix these before the app is reachable from outside the VM. Today its port is bound to `127.0.0.1` only.

## Docker socket in the app container

`docker-compose.yml` mounts `/var/run/docker.sock` into the app, because the app starts one sandbox container per
case. Access to that socket is equal to root on the host. A remote code execution bug in the app would give an
attacker the whole VM, including `.env` and every case.

The app also has no login yet, so anyone who reaches it could submit reports and approve actions.

Before a public deploy:

- Put something narrower between the app and Docker, so the app can only start the sandbox image with the contract
  flags and cannot reach the rest of the Docker API.
- Separate analyst access (approving actions) from the public report form that judges can use.
- Then bind the port publicly.
