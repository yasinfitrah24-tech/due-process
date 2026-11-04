FROM python:3.12-alpine
ENV PYTHONDONTWRITEBYTECODE=1
COPY server.py collector.py /app/
CMD ["python3", "-u", "/app/server.py"]
