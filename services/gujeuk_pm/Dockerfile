# Build only after production-host capacity preflight. Never contains auth/state.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
WORKDIR /package
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[pm-server]' \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/browsers
COPY --chmod=0444 services/gujeuk_pm/collector.py /collector/collector.py
USER 1000:1000
ENTRYPOINT ["python", "-B"]
CMD ["-m", "oijwef098234.gujeuk_ai.runtime", "worker", "--state", "/state"]
