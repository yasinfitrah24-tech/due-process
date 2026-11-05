SANDBOX_IMAGE = due-process-sandbox:v1.64.1

# On Windows, `docker` without .exe is a wrapper script that Cygwin's sh cannot run.
ifeq ($(OS),Windows_NT)
DOCKER ?= docker.exe
PY ?= .venv/Scripts/python.exe
else
DOCKER ?= docker
PY ?= .venv/bin/python
endif

VM ?= dp-vm
ifeq ($(OS),Windows_NT)
SSH ?= C:/WINDOWS/System32/OpenSSH/ssh.exe
else
SSH ?= ssh
endif

.PHONY: up down sandbox-image test test-docker docker-running eval reset-demo reset-demo-vm

up: sandbox-image
	$(DOCKER) compose up -d --build --wait

down:
	$(DOCKER) compose down

sandbox-image:
	$(DOCKER) build -q -t $(SANDBOX_IMAGE) sandbox

test:
	$(PY) -m pytest -q -m "not docker"

# Runs real sandbox containers; needs `make up` first. Takes about two minutes (one visit hangs on purpose).
test-docker: docker-running
	$(PY) -m pytest -q -m docker

# Stops here with a clear message and a non-zero exit, instead of dozens of failures that look like bugs.
docker-running:
	@$(DOCKER) info >/dev/null 2>&1 || { echo "Docker is not running: start it, run make up, then run this again." >&2; exit 1; }

# Needs `make up`. Real model calls, at most USD 0.50. Writes docs/eval.md and eval/results/.
eval:
	$(PY) -m eval.run_eval

# Deletes every case, then rebuilds one example per test page with real visits and model calls.
reset-demo:
	$(DOCKER) compose exec -T app python -u -m app.reset_demo

reset-demo-vm:
	$(SSH) -o ServerAliveInterval=15 $(VM) "cd /opt/due-process && docker compose exec -T app python -u -m app.reset_demo"
