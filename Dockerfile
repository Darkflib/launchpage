# Minimal, fast, non-root
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on

# system deps for timezonefinder (pure-Python) and . (bundled), plus tzdata
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential tzdata \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Universal Venv) and upgrade pip to latest
RUN pip install --upgrade pip uv

WORKDIR /app
# Install project dependencies into the system
# We use uv (Universal Venv) to compile and install dependencies
# into the system Python environment
COPY pyproject.toml README.md ./

# Using uv (Universal Venv) to compile and install dependencies
RUN uv pip compile pyproject.toml > requirements.txt && \
    uv pip install --system -r requirements.txt

# Copy project after dependencies are installed to leverage Docker cache
COPY app ./app
COPY web ./web

EXPOSE 8000
USER 65532:65532
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
