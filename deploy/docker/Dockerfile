# syntax=docker/dockerfile:1
# Snapshot-mode live server (no torch / Loki / LLM). See deploy/README.md.
#
#   docker build -f deploy/docker/Dockerfile -t nt2102-demo .          (repo root)
#   docker run --rm -p 8000:8000 --read-only --tmpfs /data --tmpfs /tmp nt2102-demo

FROM python:3.12.16-slim-trixie AS deps
COPY --from=ghcr.io/astral-sh/uv:0.9.3 /uv /bin/uv
COPY deploy/docker/requirements.txt /tmp/requirements.txt
RUN uv pip install --no-cache --require-hashes --target /opt/deps -r /tmp/requirements.txt

FROM python:3.12.16-slim-trixie
ARG NT2102_CODE_SHA=unknown
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/deps \
    NT2102_CODE_SHA=${NT2102_CODE_SHA} \
    TZ=Asia/Ho_Chi_Minh
LABEL org.opencontainers.image.title="nt2102-demo" \
      org.opencontainers.image.description="OCR-APT live detector, recorded snapshot demo (read-only)" \
      org.opencontainers.image.source="https://github.com/UITProjects/NT2102" \
      org.opencontainers.image.revision="${NT2102_CODE_SHA}"
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin demo
COPY --from=deps /opt/deps /opt/deps
WORKDIR /app
# only the modules snapshot mode imports
COPY src/live/live_server.py src/live/repository.py src/live/history.py src/live/file_store.py \
     src/live/snapshot.py src/live/playback.py src/live/
COPY src/live/static/index.html src/live/static/
COPY src/pipeline/alg1.py src/pipeline/alg2.py src/pipeline/cmdlines.py src/pipeline/
COPY deploy/snapshot/nt2102-snapshot.tar.gz /app/snapshot/
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
# loops 2026-11-15 15:10Z -> 20:15Z
ENTRYPOINT ["python", "src/live/live_server.py", "--snapshot", "/app/snapshot/nt2102-snapshot.tar.gz", \
            "--data-dir", "/data", "--bind", "0.0.0.0", "--port", "8000", \
            "--playback-from", "2026-11-15T15:10:00Z", "--playback-interval-s", "3", "--playback-pause-s", "30"]
