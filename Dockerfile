# ─── Stage 1: Builder ─────────────────────────────────────────────────────────
# Compiles wheels for packages that need C extensions (e.g. pydantic-core)
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ─── Stage 2: Runtime ─────────────────────────────────────────────────────────
# python:3.11-slim is Debian Trixie — package names use t64 suffix
FROM python:3.11-slim

# Playwright + Chromium system dependencies (Debian Trixie / bookworm compatible)
# Note: Many packages were renamed with t64 suffix in Debian Trixie.
#       libappindicator3-1 is removed from Trixie — use libayatana-appindicator3-1.
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    libnss3 \
    libatk1.0-0t64 \
    libatk-bridge2.0-0t64 \
    libcups2t64 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libgtk-3-0t64 \
    libasound2t64 \
    libxss1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    fonts-liberation \
    xdg-utils \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy pip-installed packages from builder
COPY --from=builder /install /usr/local

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=0 \
    CHROMIUM_FLAGS="--no-sandbox --disable-dev-shm-usage --disable-gpu" \
    PORT=8000

WORKDIR /app

# Install Playwright and download Chromium browser binaries
# Version must match requirements.txt playwright pin
RUN playwright install chromium

# Copy application source
COPY . .

# Non-root user for security
RUN useradd -m -u 1001 scraper \
    && mkdir -p /opt/pw-browsers \
    && chown -R scraper:scraper /app /opt/pw-browsers

USER scraper

EXPOSE 8000

# Healthcheck so Railway knows when the app is ready
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--loop", "asyncio", "--access-log"]
