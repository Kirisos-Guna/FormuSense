# FormuSense runs on the Python standard library alone. The optional extras are
# installed here because a container can carry them cheaply: PostgreSQL support
# (psycopg) and the report's figure/code-listing renderers (Pillow, Pygments).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FORMUSENSE_HOST=0.0.0.0 \
    FORMUSENSE_PORT=8770

WORKDIR /app

COPY requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements-optional.txt

COPY . .

# The record and the generated artefacts live under app/data and report/; declare
# them as volumes so they can persist across container restarts.
VOLUME ["/app/app/data", "/app/report"]

EXPOSE 8770

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8770/api/ready').status==200 else 1)"

CMD ["python", "run.py"]
