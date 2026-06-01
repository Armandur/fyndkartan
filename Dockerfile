FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Installera beroenden via uv från låsfilen (reproducerbart).
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Applikationen: api/-paketet + statisk frontend i web/.
COPY api ./api
COPY web ./web

ENV PATH="/app/.venv/bin:$PATH" \
    DB_PATH=/data/stores.db
RUN mkdir -p /data

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
