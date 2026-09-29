# FormuSense runs on the Python standard library alone. The optional extras are
# installed here because a container can carry them cheaply: PostgreSQL support
# (psycopg) and the report's figure/code-listing renderers (Pillow, Pygments).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FORMUSENSE_HOST=0.0.0.0

WORKDIR /app

COPY requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements-optional.txt

COPY . .

# The acceptance model is trained here, in the image, by the project's own offline
# pipeline: no API key, no network at runtime, and no trained artefact checked into
# the repository. These are the settings the figures in README.md were measured
# with, so a fresh deployment reports that model rather than an empty Model tab.
RUN python run.py --build-dataset --variants 20 --dataset-seed 7 \
 && python run.py --train --model-version v1

# FORMUSENSE_PORT is deliberately left unset: a hosting platform injects PORT and
# the application follows it (see app/config.py), while a plain `docker run` falls
# back to 8770. Nothing is declared as a VOLUME either - a volume mounted over
# app/data would hide the model built above. docker-compose.yml mounts that
# directory when the record and the report are meant to outlive the container.
EXPOSE 8770

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request,sys; port=os.environ.get('FORMUSENSE_PORT') or os.environ.get('PORT') or '8770'; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/api/ready' % port).status == 200 else 1)"

CMD ["python", "run.py"]
