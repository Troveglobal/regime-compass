# Regime Compass

Multi-market regime detection website. Hidden Markov Models, Simple Moving Average, and Exponential Moving Average models applied to six global markets (Nifty 50, S&P 500, KOSPI, Shanghai Composite, Bitcoin, Ethereum). Free, updated daily.

Live at: `regimecompass.com` *(once deployed)*

Built by [Aditya Sahasrabuddhe](https://www.linkedin.com/in/aditya-s1/) as part of [iQuant Labs](https://iquantlabs.com).

---

## Local development

```bash
# Python 3.12 required
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# First run — fetches data, trains models (~2 minutes)
uvicorn src.api:app --host 127.0.0.1 --port 8001
```

The server will bootstrap itself on first run: fetch market data via yfinance, build feature matrices, train HMMs, compute regime history. Subsequent runs use the cached data and only fetch updates.

Open `http://localhost:8001` in your browser.

## Deployment to Railway

### One-time setup

1. **Push this repo to GitHub** (instructions below if you haven't yet).
2. **Sign in to Railway** at https://railway.app — connect your GitHub account.
3. **Create a new project** → "Deploy from GitHub repo" → pick this repo.
4. Railway will auto-detect Python via Nixpacks and use the `Procfile` start command.
5. Wait for the first deploy. Bootstrap takes ~2 minutes (fetching 16 years of data for 6 markets, training 6 HMMs).

### Environment variables to set in Railway

| Variable | Required? | Purpose |
|---|---|---|
| `PORT` | Auto-set by Railway | Don't override |
| `RESEND_API_KEY` | Optional | Enables email alert sending. Sign up at [resend.com](https://resend.com) for free tier (3,000 emails/month). Without this, emails are logged but not sent. |
| `RESEND_FROM_EMAIL` | If RESEND_API_KEY set | e.g. `Regime Compass <alerts@regimecompass.com>`. The domain must be verified in Resend. |
| `HMM_CORS_ORIGINS` | Optional | Comma-separated list of allowed origins (default `*`). |

### Custom domain (regimecompass.com — apex)

Because we're using the apex domain (no subdomain prefix), DNS setup needs a CNAME-flattening / ALIAS record.

1. In Railway, go to your service → **Settings → Networking → Custom Domain**.
2. Add `regimecompass.com` AND `www.regimecompass.com`. Railway gives you a CNAME target.
3. In your DNS provider (Cloudflare, Namecheap, etc.):
   - For `regimecompass.com` (apex): use a **CNAME-flattening / ALIAS / ANAME** record pointing to `<your-railway-app>.up.railway.app`. Cloudflare supports this natively as a "CNAME" at the root with proxying enabled.
   - For `www.regimecompass.com`: add a regular **CNAME** pointing to the same Railway target.
   - If your DNS provider doesn't support CNAME flattening at apex, use an **A record** pointing to Railway's IP (Railway docs show the current IP).
4. Wait 5–30 minutes for DNS to propagate.
5. Railway auto-issues an HTTPS certificate via Let's Encrypt for both domains.

**Tip**: if you use **Cloudflare** as DNS (free tier is plenty), it auto-handles the apex CNAME via flattening and gives you a free CDN + DDoS protection on top.

### Scheduled jobs

The web server runs an in-process scheduler (APScheduler):
- **Daily 11:00 UTC** (Mon–Fri): refetch latest day's data, dispatch alert emails.
- **Sunday 03:30 UTC**: full retrain of all 6 HMMs on the now-week-bigger dataset.

No separate cron service needed. To disable the scheduler (e.g. for local dev), set `DISABLE_SCHEDULER=1`.

## Repo

Hosted at: `github.com/<YOUR-USERNAME>/regime-compass`

For local development on a new machine:
```bash
git clone git@github.com:<YOUR-USERNAME>/regime-compass.git
cd regime-compass
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn src.api:app --host 127.0.0.1 --port 8001
```

## Architecture

| Path | Purpose |
|---|---|
| `src/config.py` | Index registry, paths, constants, env vars |
| `src/fetch.py` | Data fetching via yfinance with retries |
| `src/features.py` | Feature matrices per index |
| `src/model.py` | HMM training + stable label rule |
| `src/ma_regime.py` | SMA + EMA regime detection (kind param) |
| `src/ma_backtest.py` | Walk-forward backtest with 2-day confirmation |
| `src/composite.py` | Per-market risk score |
| `src/inference.py` | Filtered probabilities (causal) |
| `src/subscriptions.py` | Email subscriber database |
| `src/email_sender.py` | Resend integration |
| `src/alerts.py` | Daily alert detection + dispatch |
| `src/api.py` | FastAPI app, routes, scheduler, bootstrap |
| `frontend/` | Static HTML/CSS/JS pages |
| `data/` | Parquet + SQLite (rebuilt on first run) |
| `models/` | Pickled trained HMMs (rebuilt on first run) |

## Disclaimer

This is a free statistical research tool for educational purposes only. Not investment advice in any jurisdiction. See `/disclaimer` page.

## License

All rights reserved. Source available for personal study; commercial use requires written consent.
