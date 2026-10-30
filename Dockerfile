FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends --only-upgrade liblzma5 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN useradd --uid 10001 --user-group --shell /usr/sbin/nologin --no-create-home appuser

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && pip uninstall -y pip setuptools wheel

COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser registry.json ./registry.json

USER appuser
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health')"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
