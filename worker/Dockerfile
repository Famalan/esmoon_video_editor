FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir poetry==1.8.4 && poetry config virtualenvs.create false
COPY worker/pyproject.toml /app/
COPY shared /shared
RUN poetry install --no-root --no-interaction
COPY worker /app
ENV PYTHONPATH=/app:/shared
