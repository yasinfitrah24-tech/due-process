ARG PW_TAG
FROM mcr.microsoft.com/playwright/python:${PW_TAG}
ARG PW_TAG
# The image ships browsers + system deps only; the Python package is installed in a throwaway venv during
# its build and removed. Install the matching version at build time (host network, not the runner's).
RUN v=${PW_TAG#v}; pip install --no-cache-dir --break-system-packages "playwright==${v%-noble}" \
    && python3 -c "import playwright, importlib.metadata as m; print('playwright', m.version('playwright'))"
# HOME on the tmpfs: the root filesystem is read-only at run time. Set by the image, not taken from the host.
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
COPY visit.py /app/visit.py
USER pwuser
ENTRYPOINT ["python3", "/app/visit.py"]
