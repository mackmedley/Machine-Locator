# Machine Locator, packaged for a hosting service.
#
# The database is a SQLite file under /data, so mount a persistent volume
# there -- without one, every redeploy starts you back at an empty prospect
# list.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MACHINE_LOCATOR_HOME=/data

WORKDIR /app

# Dependencies first, so a code change doesn't reinstall the world.
COPY pyproject.toml README.md ./
COPY machine_locator ./machine_locator
RUN pip install --no-cache-dir -e ".[deploy]"

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000

# One worker, several threads -- see machine_locator/wsgi.py for why.
CMD ["sh", "-c", "gunicorn machine_locator.wsgi:app --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 4 --timeout 300"]
