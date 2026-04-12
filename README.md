# 🛒 Grab Gully — Scraper Service

**"Har Deal Ka Baap."**

Production-grade Python scraper + FastAPI backend for the Grab Gully Android app.
Scrapes Myntra, Meesho, Ajio, Snapdeal, **Flipkart** via Scrapling + Playwright.
Fetches live Amazon product data via the **Amazon Creator API** (Content Creator Program).
All non-Amazon affiliate links generated via **CueLink**.
Deployed on Railway. Zero paid infrastructure at launch.

---

## Architecture

```
Android App  ──HTTP──>  FastAPI (Railway)  ──reads──>  Supabase PostgreSQL
                              │
                    APScheduler (cron, IST)
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
    Scrapling             Amazon               CueLink
  (Myntra/Meesho/        Creator API          Affiliate API
   Ajio/Snapdeal/        (OAuth2 LWA)         (All platforms
   Flipkart)              product data)        except Amazon)
```

### Affiliate Strategy

| Platform | Method | Notes |
|---|---|---| 
| Amazon | Direct Associates deep link | `amazon.in/dp/{ASIN}?tag=grabgully-21` |
| Flipkart | CueLink short-link | Auto-tracked via CueLink |
| Myntra | CueLink short-link | Auto-tracked via CueLink |
| Meesho | CueLink short-link | Auto-tracked via CueLink |
| Ajio | CueLink short-link | Auto-tracked via CueLink |
| Snapdeal | CueLink short-link | Auto-tracked via CueLink |

---

## Local Development

### 1. Prerequisites
- Python 3.11+
- Docker (optional, for parity with Railway)

### 2. Setup
```bash
git clone https://github.com/jatinchaurasiya/grabgully.scraper
cd grab-gully-scraper

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium --with-deps

cp .env.example .env
# Edit .env and fill in all values
```

### 3. Run Supabase Schema
```
Supabase Dashboard → SQL Editor → New Query
→ Paste contents of supabase/schema.sql
→ Run All
```

### 4. Start Dev Server
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Visit: http://localhost:8000/docs (Swagger UI, dev mode only)
Visit: http://localhost:8000/health

---

## Railway Deployment

### Step-by-step

1. Push this repo to GitHub (private repo)
2. railway.app → New Project → Deploy from GitHub repo
3. Select this repo → Railway detects Dockerfile → Deploy
4. Settings → Variables → Add all vars from `.env.example`
5. Settings → Networking → Generate Domain
6. Test: `curl https://your-app.railway.app/health`

### Required Railway Environment Variables

| Variable | Where to find |
|---|---|
| `SCRAPER_SECRET` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `SUPABASE_URL` | Supabase → Project Settings → API |
| `SUPABASE_SERVICE_KEY` | Supabase → Settings → Service Role Key |
| `UPSTASH_REDIS_URL` | Upstash Console → REST URL |
| `UPSTASH_REDIS_TOKEN` | Upstash Console → REST Token |
| `AMAZON_CLIENT_ID` | Amazon Creator Program → LWA Security Profile |
| `AMAZON_CLIENT_SECRET` | Amazon Creator Program → LWA Security Profile |
| `AMAZON_PARTNER_TAG` | Your Amazon Associates tracking ID (e.g. `grabgully-21`) |
| `CUELINK_API_KEY` | CueLink Dashboard → API Section → Generate Key |
| `FIREBASE_PROJECT_ID` | Firebase Console → Project Settings |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Firebase → Service Accounts → Generate Key (minify JSON) |
| `ALLOWED_ORIGINS` | `https://grabgully.com,https://admin.grabgully.com` |

---

## Third-Party Integrations

### Amazon Creator API (Content Creator Program)
Replaces the old PA-API 5.0. Uses **OAuth2 LWA (Login with Amazon)** — no complex
AWS Signature V4 signing required.

1. Join: https://affiliate.amazon.in → Tools → Creator API
2. Create an LWA Security Profile → get `AMAZON_CLIENT_ID` + `AMAZON_CLIENT_SECRET`
3. Tokens are auto-refreshed by the service before expiry.

### CueLink Affiliate API
Single integration to generate tracked affiliate links for Flipkart, Myntra, Meesho,
Ajio, and Snapdeal. CueLink auto-detects the merchant from the URL.

1. Register: https://cuelinks.com
2. Join merchant programs for each platform inside the CueLink dashboard
3. Go to: Dashboard → API Section → Generate API Key → set `CUELINK_API_KEY`

---

## Scrapers

| Scraper | File | Method |
|---|---|---|
| Flipkart | `scrapers/flipkart.py` | Scrapling + Playwright (headless) |
| Myntra | `scrapers/myntra.py` | Scrapling + Playwright (headless) |
| Meesho | `scrapers/meesho.py` | Scrapling + Playwright (headless) |
| Ajio | `scrapers/ajio.py` | Scrapling + Playwright (headless) |
| Snapdeal | `scrapers/snapdeal.py` | Scrapling lite (SSR-friendly) |

---

## API Reference

### Public Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Health check (Railway ping) |
| GET | `/deals` | Deal feed (paginated, filterable) |
| GET | `/deals/top` | Top deals by discount % |
| GET | `/deals/{id}` | Single deal |
| GET | `/search?q=` | Universal search |
| GET | `/search/url?url=` | URL paste search |
| GET | `/compare/{listing_id}` | Cross-platform price comparison |
| GET | `/compare/{listing_id}/history` | Price history (30/60/90 days) |
| GET | `/go/{platform}/{listing_id}` | Affiliate redirect |

### Authenticated Endpoints (Supabase JWT)

| Method | Path | Description |
|---|---|---|
| GET | `/watchlist` | Get user's watchlist |
| POST | `/watchlist` | Add to watchlist |
| DELETE | `/watchlist/{id}` | Remove from watchlist |
| PATCH | `/watchlist/{id}/alert` | Set price alert |

### Admin Endpoints (SCRAPER_SECRET)

| Method | Path | Description |
|---|---|---|
| POST | `/admin/trigger-scrape` | Manually trigger full scrape |
| POST | `/admin/trigger-price-check` | Manually trigger alert check |
| GET | `/admin/jobs` | List scheduler jobs |

---

## Monitoring

- **Logs**: Railway Dashboard → your service → Logs (structured JSON)
- **Health**: `/health` endpoint — checks DB + scheduler
- **Metrics**: `/metrics` — Prometheus format (request count, latency)
- **Scraper runs**: `scraper_runs` table in Supabase — full audit trail

---

## Scraping Schedule (IST)

| Job | Schedule | Purpose |
|---|---|---|
| Platform scrapers | Every 30 min, 6AM–11PM | Flipkart, Myntra, Meesho, Ajio, Snapdeal |
| Amazon Creator API | :15 and :45 past hour, 6AM–11PM | Search + deals refresh |
| Price alerts | Every 15 min | Watchlist target price checks + FCM push |
| Data cleanup | Daily 2:00 AM | Delete price_history > 1 year old |

> **Railway credit tip**: Scraper only runs 6AM–11PM IST (17 hours/day).
> Estimated Railway credit usage: ~$3/month on free $5 plan.

---

## Security Notes

- All API keys in env vars — never in code
- Service role key (Supabase) only on Railway — never in Android app
- Affiliate URLs never exposed in app — all via `/go/` redirect
- RLS enabled on all Supabase tables
- Rate limiting on affiliate clicks (Upstash Redis)
- Non-root Docker user

---

## Quality Control & CI/CD

This repository includes automated Quality Control checks via GitHub Actions (`.github/workflows/ci.yml`).

- **Formatting & Linting**: Auto-checked via `ruff` and `black`. Ensure you run `ruff check .` and `black .` locally before pushing.
- **Type Checking** (Optional): Pipeline runs basic `mypy` type hints checks.
- **Testing**: Pre-configured to run `pytest` if a `tests/` directory is present.

---

*Grab Gully — Har Deal Ka Baap.* 🏆
