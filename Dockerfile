FROM python:3.13.1-alpine
WORKDIR /app
COPY app/ /app/
RUN adduser -D -H -u 10001 pages && mkdir -p /data && chown pages /data
USER 10001
ENV DATA_DIR=/data PORT=8000 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8000
VOLUME /data
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"
CMD ["python", "server.py"]
