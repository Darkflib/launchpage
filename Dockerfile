# Minimal, fast, non-root
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on

# system deps for timezonefinder (pure-Python) and . (bundled), plus tzdata
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
COPY web ./web

RUN pip install --no-cache-dir uv && \
    uv pip compile pyproject.toml > requirements.txt && \
    uv pip install --system -r requirements.txt

EXPOSE 8000
USER 65532:65532
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
