# ─── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim

# System deps required by Playwright Chromium
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libxrandr2 libgbm1 libpango-1.0-0 libgtk-3-0 \
    libasound2 libxss1 libxcomposite1 libxdamage1 \
    fonts-liberation libappindicator3-1 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy pip packages from builder
COPY --from=builder /install /usr/local

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    PORT=8000

WORKDIR /app

# Install Playwright browser binary (Python package already in /usr/local from builder)
RUN playwright install chromium --with-deps

# Copy source code
COPY . .

# Run as non-root user for security
RUN useradd -m -u 1001 scraper \
    && chown -R scraper:scraper /app \
    && mkdir -p /opt/pw-browsers \
    && chown -R scraper:scraper /opt/pw-browsers

USER scraper

EXPOSE 8000

# Railway health check — tells Railway when the app is truly ready
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "asyncio", \
     "--access-log"]
