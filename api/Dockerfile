FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir poetry==1.8.4 && poetry config virtualenvs.create false
COPY api/pyproject.toml /app/
COPY shared /shared
RUN poetry install --no-root --no-interaction
COPY api /app
ENV PYTHONPATH=/app:/shared
EXPOSE 8000
