FROM python:3.12-slim
# The only image with a Docker client. The app image has none.
COPY --from=docker:29-cli /usr/local/bin/docker /usr/local/bin/docker
WORKDIR /srv
COPY sandbox sandbox
COPY launcher launcher
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python3", "-m", "launcher.server"]
