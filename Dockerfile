# Production Dockerfile for LeadPulse (Playwright + FastAPI + Deduplication Pipeline)
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

# Set environment flags
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8000 \
    HOST=0.0.0.0

WORKDIR /app

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency definition
COPY requirements.txt .

# Install Python requirements
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Pre-install Chromium browser for Playwright
RUN playwright install chromium

# Copy application source code
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY tests/ ./tests/

# Create persistent storage folder
RUN mkdir -p /app/data

# Expose Webhook API port
EXPOSE 8000

# Healthcheck to ensure API and Playwright runtime are responsive
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Launch FastAPI ASGI server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
