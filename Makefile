SANDBOX_IMAGE = due-process-sandbox:v1.64.1

# On Windows, `docker` without .exe is a wrapper script that Cygwin's sh cannot run.
ifeq ($(OS),Windows_NT)
DOCKER ?= docker.exe
PY ?= .venv/Scripts/python.exe
else
DOCKER ?= docker
PY ?= .venv/bin/python
endif

.PHONY: up down sandbox-image test test-docker

up: sandbox-image
	$(DOCKER) compose up -d --build --wait

down:
	$(DOCKER) compose down

sandbox-image:
	$(DOCKER) build -q -t $(SANDBOX_IMAGE) sandbox

test:
	$(PY) -m pytest -q -m "not docker"

# Runs real sandbox containers; needs `make up` first. Takes about two minutes (one visit hangs on purpose).
test-docker:
	$(PY) -m pytest -q -m docker
