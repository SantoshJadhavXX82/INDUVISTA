FROM python:3.12-slim

# Avoid Python writing .pyc and force stdout/stderr to be unbuffered so
# uvicorn and alembic logs surface in real time under `docker compose logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python dependencies first so this layer is cached when only
# application code changes.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Copy Alembic configuration and the application package. Compose mounts
# ./backend:/app (migrate service) or ./backend/app:/app/app (backend, workers)
# in dev, so these copies are what production images use without mounts.
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY app/ ./app/

EXPOSE 8000

# Default command — overridden by docker-compose per service
# (uvicorn for backend, alembic for migrate, python -m for workers).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
