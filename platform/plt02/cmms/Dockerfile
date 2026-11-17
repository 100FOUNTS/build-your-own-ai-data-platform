# Mock legacy CMMS of plant PLT02 (a platform fixture). Build context: platform/plt02  (so it can copy notes.py and schema.py)
#   docker build -f platform/plt02/cmms/Dockerfile platform/plt02
FROM python:3.12.15-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
RUN useradd --system --uid 10001 cmms
WORKDIR /app
COPY . /app/plt02/
ENV PYTHONPATH=/app/plt02:/app/plt02/cmms PYTHONUNBUFFERED=1 CMMS_HOST=0.0.0.0 CMMS_PORT=8702
USER cmms
EXPOSE 8702
HEALTHCHECK --interval=5s --timeout=3s --retries=10 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8702/health', timeout=2).status == 200 else 1)"
ENTRYPOINT ["python", "-u", "/app/plt02/cmms/server.py"]
