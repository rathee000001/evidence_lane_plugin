FROM python:3.14.2-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EVIDENCE_LANE_DATA_ROOT=/var/lib/evidence-lane \
    EVIDENCE_LANE_MCP_PORT=8080

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        git \
        libgomp1 \
        tesseract-ocr \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY plugins/evidence-lane-plugin/requirements.lock.txt /tmp/requirements.lock.txt
RUN python -m pip install \
        --disable-pip-version-check \
        --require-hashes \
        --no-cache-dir \
        -r /tmp/requirements.lock.txt

COPY . /app
RUN python -m pip install \
        --disable-pip-version-check \
        --force-reinstall \
        --no-build-isolation \
        --no-deps \
        /app \
    && useradd --create-home --uid 10001 evidence-lane \
    && mkdir -p /var/lib/evidence-lane \
    && chown -R evidence-lane:evidence-lane /var/lib/evidence-lane

USER evidence-lane
VOLUME ["/var/lib/evidence-lane"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["evidence-lane-plugin", "serve", "--transport", "streamable-http", "--host", "0.0.0.0"]
