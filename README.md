# Regime Compass

Multi-market regime detection website. Hidden Markov Models, Simple Moving Average, and Exponential Moving Average models applied to six global markets (Nifty 50, S&P 500, KOSPI, Shanghai Composite, Bitcoin, Ethereum). Free, updated daily.

Live at: `regime.iquantlabs.com` *(once deployed)*

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
| `RESEND_FROM_EMAIL` | If RESEND_API_KEY set | e.g. `Regime Compass <alerts@regime.iquantlabs.com>`. The domain must be verified in Resend. |
| `HMM_CORS_ORIGINS` | Optional | Comma-separated list of allowed origins (default `*`). |

### Custom domain (regime.iquantlabs.com)

1. In Railway, go to your service → Settings → Networking → Custom Domain.
2. Add `regime.iquantlabs.com`. Railway gives you a CNAME target.
3. In your DNS provider (where you bought iquantlabs.com — Cloudflare, Namecheap, etc.):
   - Add a CNAME record: `regime` → `<your-railway-app>.up.railway.app`
4. Wait 5-30 minutes for DNS to propagate.
5. Railway auto-issues an HTTPS certificate via Let's Encrypt.

### Scheduled jobs

The web server runs an in-process scheduler (APScheduler):
- **Daily 11:00 UTC** (Mon–Fri): refetch latest day's data, dispatch alert emails.
- **Sunday 03:30 UTC**: full retrain of all 6 HMMs on the now-week-bigger dataset.

No separate cron service needed. To disable the scheduler (e.g. for local dev), set `DISABLE_SCHEDULER=1`.

## First push to GitHub

If you haven't created the repo yet:

```bash
# 1. Create a new repo on github.com (do NOT initialise with README — we have one already)
# Name suggestion: "regime-compass"

# 2. Locally:
cd ~/agents/hmm_nifty
git init
git add .
git commit -m "Initial commit — Regime Compass v0.4"
git branch -M main
git remote add origin git@github.com:<YOUR-USERNAME>/regime-compass.git
git push -u origin main
```

(Or use HTTPS instead of SSH if you prefer — Railway works with either.)

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
