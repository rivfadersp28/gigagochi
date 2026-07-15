FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.lock ./

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install \
    --no-cache-dir \
    --require-hashes \
    --only-binary=:all: \
    --requirement requirements.lock \
    && python -m pip check

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
COPY data ./data
COPY static ./static

RUN groupadd --system --gid 10001 gigagochi \
    && useradd --system --uid 10001 --gid gigagochi --home-dir /app gigagochi \
    && mkdir -p /app/static/generated /app/data/push /app/logs \
    && chown -R gigagochi:gigagochi /app/static/generated /app/data/push /app/logs

USER gigagochi

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
