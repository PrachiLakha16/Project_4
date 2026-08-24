# Multi-stage build: compile deps in a full image, ship a slim non-root runtime.

FROM python:3.11-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir --upgrade pip
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --target=/install .

FROM python:3.11-slim AS runtime
RUN groupadd --gid 10001 sreagent && \
    useradd --uid 10001 --gid sreagent --shell /usr/sbin/nologin --no-create-home sreagent
WORKDIR /app
COPY --from=builder /install /usr/local/lib/python3.11/site-packages
COPY src ./src
COPY --chown=sreagent:sreagent src/sre_agent/guardrails/allowlist.yaml ./allowlist.yaml

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    ALLOWLIST_CONFIG_PATH=/app/allowlist.yaml

USER sreagent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

CMD ["python", "-m", "uvicorn", "sre_agent.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
