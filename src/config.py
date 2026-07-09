import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
LOGS_DIR = ROOT / "logs"

DB_PATH = DATA_DIR / "regime.db"

# User-generated data (email subscribers, feedback, waitlist) MUST survive
# redeploys. regime.db is git-tracked and gets reset to the committed copy on
# every deploy, and the Railway container filesystem is ephemeral — so user
# data written there is lost on the next deploy or restart. Route it to the
# mounted persistent volume when one is present (same volume smart-money uses).
# Local dev (no volume) keeps everything in regime.db, unchanged.
_USER_VOL = (
    os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    or os.environ.get("SMARTMONEY_DATA_DIR")
    or ""
).strip() or None
if _USER_VOL and os.path.isdir(_USER_VOL):
    USER_DB_PATH = Path(_USER_VOL) / "users.db"
else:
    USER_DB_PATH = DB_PATH  # local dev / no volume: same file as before

START_DATE = "2010-01-01"

N_STATES = 3
VOL_WINDOW = 10
RANDOM_STATE = 42

STATE_LABELS = ["bear", "neutral", "bull"]

DATA_SOURCE = os.getenv("HMM_DATA_SOURCE", "yfinance")

API_HOST = os.getenv("HMM_API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("HMM_API_PORT", "8001"))
API_CORS_ORIGINS = os.getenv(
    "HMM_CORS_ORIGINS",
    "https://www.regimecompass.com,https://regimecompass.com",
).split(",")


# Registry of supported indices — ordered for display (US/global first, Asia second).
INDICES = {
    "spx": {
        "name": "S&P 500",
        "country": "USA",
        "currency": "USD",
        "tickers": {"price": "^GSPC", "fx": "DX-Y.NYB", "vix": "^VIX"},
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
    },
    "nasdaq": {
        "name": "Nasdaq 100",
        "country": "USA",
        "currency": "USD",
        "tickers": {"price": "^NDX", "fx": "DX-Y.NYB", "vix": "^VIX"},
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
    },
    "btc": {
        "name": "Bitcoin",
        "country": "Crypto",
        "currency": "USD",
        "tickers": {"price": "BTC-USD", "fx": "DX-Y.NYB", "vix": None},
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
    },
    "eth": {
        "name": "Ethereum",
        "country": "Crypto",
        "currency": "USD",
        "tickers": {"price": "ETH-USD", "fx": "DX-Y.NYB", "vix": None},
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
    },
    "gold": {
        "name": "Gold",
        "country": "Commodity",
        "currency": "USD",
        "tickers": {"price": "GC=F", "fx": "DX-Y.NYB", "vix": None},
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
    },
    "silver": {
        "name": "Silver",
        "country": "Commodity",
        "currency": "USD",
        "tickers": {"price": "SI=F", "fx": "DX-Y.NYB", "vix": None},
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
    },
    "stoxx50": {
        "name": "Euro Stoxx 50",
        "country": "Eurozone",
        "currency": "EUR",
        "tickers": {"price": "^STOXX50E", "fx": "EURUSD=X", "vix": None},
        "cash_rate": 0.020,
        "cash_label": "Euro MMF",
    },
    "nifty": {
        "name": "Nifty 50",
        "country": "India",
        "currency": "INR",
        "tickers": {"price": "^NSEI", "fx": "INR=X", "vix": "^INDIAVIX"},
        "cash_rate": 0.065,
        "cash_label": "India overnight rate",
    },
    "nikkei": {
        "name": "Nikkei 225",
        "country": "Japan",
        "currency": "JPY",
        "tickers": {"price": "^N225", "fx": "JPY=X", "vix": None},
        "cash_rate": 0.005,
        "cash_label": "Japanese MMF",
    },
    "kospi": {
        "name": "KOSPI Composite",
        "country": "South Korea",
        "currency": "KRW",
        "tickers": {"price": "^KS11", "fx": "KRW=X", "vix": None},
        "cash_rate": 0.025,
        "cash_label": "Korean MMF",
    },
    "shcomp": {
        "name": "Shanghai Composite",
        "country": "China",
        "currency": "CNY",
        "tickers": {"price": "000001.SS", "fx": "CNY=X", "vix": None},
        "cash_rate": 0.025,
        "cash_label": "Chinese MMF",
    },
    "hangseng": {
        "name": "Hang Seng",
        "country": "Hong Kong",
        "currency": "HKD",
        "tickers": {"price": "^HSI", "fx": "HKD=X", "vix": None},
        "cash_rate": 0.030,
        "cash_label": "HK deposit rate",
    },
    "taiex": {
        "name": "TAIEX",
        "country": "Taiwan",
        "currency": "TWD",
        "tickers": {"price": "^TWII", "fx": "TWD=X", "vix": None},
        "cash_rate": 0.015,
        "cash_label": "Taiwan deposit rate",
    },
    "wti": {
        "name": "Crude Oil (WTI)",
        "country": "Commodity",
        "currency": "USD",
        "tickers": {"price": "CL=F", "fx": "DX-Y.NYB", "vix": "^OVX"},
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
    },
    "copper": {
        "name": "Copper",
        "country": "Commodity",
        "currency": "USD",
        "tickers": {"price": "HG=F", "fx": "DX-Y.NYB", "vix": None},
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
    },
    "us10y": {
        "name": "US 10Y Treasuries",
        "country": "Rates",
        "currency": "USD",
        "tickers": {"price": "ZN=F", "fx": "DX-Y.NYB", "vix": "^MOVE"},
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
        # bond bull = risk-off elsewhere: keep out of the breadth verdict
        "breadth": False,
    },
    "dxy": {
        "name": "US Dollar Index",
        "country": "FX",
        "currency": "USD",
        "tickers": {"price": "DX-Y.NYB", "fx": None, "vix": None},
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
        # dollar bull = risk-off elsewhere: keep out of the breadth verdict
        "breadth": False,
    },
    "ftse": {
        "name": "FTSE 100",
        "country": "United Kingdom",
        "currency": "GBP",
        "tickers": {"price": "^FTSE", "fx": "GBPUSD=X", "vix": None},
        "cash_rate": 0.030,
        "cash_label": "UK MMF",
    },
    "bovespa": {
        "name": "Bovespa",
        "country": "Brazil",
        "currency": "BRL",
        "tickers": {"price": "^BVSP", "fx": "BRL=X", "vix": None},
        "cash_rate": 0.100,
        "cash_label": "Brazil CDI",
    },
    "tadawul": {
        "name": "Tadawul All Share",
        "country": "Saudi Arabia",
        "currency": "SAR",
        # riyal is USD-pegged — DXY carries the effective currency risk
        "tickers": {"price": "^TASI.SR", "fx": "DX-Y.NYB", "vix": None},
        "cash_rate": 0.020,
        "cash_label": "SAMA bills",
    },
}

DEFAULT_INDEX = "spx"


# Country hub pages (/country/{slug}). Each maps to covered HMM indices plus
# country-level data sources. bond = FRED series for the 10y government yield
# (None where no clean free series exists — listed in page methodology).
# news_query/news_locale feed the shared news engine (src/news.py).
COUNTRIES = {
    "usa": {
        "name": "United States", "flag": "🇺🇸", "iso3": "USA",
        "indices": ["spx", "nasdaq"], "primary_index": "spx",
        "currency_label": "Dollar Index (DXY)",
        "bond": {"series": "DGS10", "label": "US 10y Treasury", "freq": "daily"},
        "smartmoney": "/smartmoney/us",
        "news_query": '"US economy" OR "Federal Reserve" OR "S&P 500"',
        "news_locale": ("en-US", "US", "US:en"),
    },
    "eurozone": {
        "name": "Eurozone", "flag": "🇪🇺", "iso3": "EURO", "group": True,
        "indices": ["stoxx50"], "primary_index": "stoxx50",
        "currency_label": "EUR/USD",
        "bond": {"series": "IRLTLT01DEM156N", "label": "German 10y Bund", "freq": "monthly"},
        "smartmoney": None,
        "news_query": 'Eurozone economy OR ECB OR "European stocks"',
        "news_locale": ("en-US", "US", "US:en"),
    },
    "india": {
        "name": "India", "flag": "🇮🇳", "iso3": "IND",
        "indices": ["nifty"], "primary_index": "nifty",
        "currency_label": "USD/INR",
        "bond": {"series": "INDIRLTLT01STM", "label": "India 10y G-Sec", "freq": "monthly"},
        "smartmoney": "/smartmoney",
        "news_query": "India economy OR Nifty OR RBI",
        "news_locale": ("en-IN", "IN", "IN:en"),
    },
    "japan": {
        "name": "Japan", "flag": "🇯🇵", "iso3": "JPN",
        "indices": ["nikkei"], "primary_index": "nikkei",
        "currency_label": "USD/JPY",
        "bond": {"series": "IRLTLT01JPM156N", "label": "Japan 10y JGB", "freq": "monthly"},
        "smartmoney": None,
        "news_query": 'Japan economy OR "Bank of Japan" OR Nikkei',
        "news_locale": ("en-US", "US", "US:en"),
    },
    "south-korea": {
        "name": "South Korea", "flag": "🇰🇷", "iso3": "KOR",
        "indices": ["kospi"], "primary_index": "kospi",
        "currency_label": "USD/KRW",
        "bond": {"series": "IRLTLT01KRM156N", "label": "Korea 10y KTB", "freq": "monthly"},
        "smartmoney": None,
        "news_query": 'South Korea economy OR "Bank of Korea" OR KOSPI',
        "news_locale": ("en-US", "US", "US:en"),
    },
    "china": {
        "name": "China", "flag": "🇨🇳", "iso3": "CHN",
        "indices": ["shcomp"], "primary_index": "shcomp",
        "currency_label": "USD/CNY",
        "bond": None,  # no clean free 10y CGB series (FRED's OECD China series is gone)
        "smartmoney": None,
        "news_query": 'China economy OR PBOC OR "Chinese stocks"',
        "news_locale": ("en-US", "US", "US:en"),
    },
    "hong-kong": {
        "name": "Hong Kong", "flag": "🇭🇰", "iso3": "HKG",
        "indices": ["hangseng"], "primary_index": "hangseng",
        "currency_label": "USD/HKD",
        "bond": None,  # no clean free 10y HKGB series
        "smartmoney": None,
        "news_query": '"Hong Kong" economy OR "Hang Seng" OR HKMA',
        "news_locale": ("en-US", "US", "US:en"),
    },
    "taiwan": {
        "name": "Taiwan", "flag": "🇹🇼", "iso3": "TWN",
        "indices": ["taiex"], "primary_index": "taiex",
        "currency_label": "USD/TWD",
        "bond": None,  # no clean free 10y TGB series
        "smartmoney": "/smartmoney/tw",
        "news_query": 'Taiwan economy OR TSMC OR TAIEX',
        "news_locale": ("en-US", "US", "US:en"),
    },
    "united-kingdom": {
        "name": "United Kingdom", "flag": "🇬🇧", "iso3": "GBR",
        "indices": ["ftse"], "primary_index": "ftse",
        "currency_label": "GBP/USD",
        "bond": {"series": "IRLTLT01GBM156N", "label": "UK 10y Gilt", "freq": "monthly"},
        "smartmoney": None,
        "news_query": '"UK economy" OR "Bank of England" OR "FTSE 100"',
        "news_locale": ("en-GB", "GB", "GB:en"),
    },
    "brazil": {
        "name": "Brazil", "flag": "🇧🇷", "iso3": "BRA",
        "indices": ["bovespa"], "primary_index": "bovespa",
        "currency_label": "USD/BRL",
        "bond": None,  # no clean free 10y BRL sovereign series
        "smartmoney": None,
        "news_query": 'Brazil economy OR Bovespa OR "central bank of Brazil"',
        "news_locale": ("en-US", "US", "US:en"),
    },
    "saudi-arabia": {
        "name": "Saudi Arabia", "flag": "🇸🇦", "iso3": "SAU",
        "indices": ["tadawul"], "primary_index": "tadawul",
        "currency_label": "USD/SAR (pegged)",
        "bond": None,  # no clean free SAR sovereign series
        "smartmoney": None,
        "news_query": '"Saudi Arabia" economy OR Tadawul OR Aramco OR "Vision 2030"',
        "news_locale": ("en-US", "US", "US:en"),
    },
}


# Asset hub pages (/asset/{slug}). All four are already classified HMM
# markets (config.INDICES) — hubs reuse their regimes, prices and news tags.
ASSETS = {
    "bitcoin": {"key": "btc", "name": "Bitcoin", "icon": "₿", "asset_class": "crypto",
                "headline_corr": "spx"},
    "ethereum": {"key": "eth", "name": "Ethereum", "icon": "Ξ", "asset_class": "crypto",
                 "headline_corr": "spx"},
    "gold": {"key": "gold", "name": "Gold", "icon": "🥇", "asset_class": "metal",
             "headline_corr": "real10y"},
    "silver": {"key": "silver", "name": "Silver", "icon": "🥈", "asset_class": "metal",
               "headline_corr": "real10y"},
    "oil": {"key": "wti", "name": "Crude Oil (WTI)", "icon": "🛢️", "asset_class": "energy",
            "headline_corr": "spx"},
    "copper": {"key": "copper", "name": "Copper", "icon": "🔶", "asset_class": "metal",
               "headline_corr": "spx"},
    "treasuries": {"key": "us10y", "name": "US 10Y Treasuries", "icon": "🏛️", "asset_class": "rates",
                   "headline_corr": "spx"},
    "dollar": {"key": "dxy", "name": "US Dollar Index", "icon": "💵", "asset_class": "fx",
               "headline_corr": "spx"},
}


def index_country_slug(index_key: str) -> str | None:
    """Country page slug an HMM market belongs to, if any (assets → None)."""
    for slug, cfg in COUNTRIES.items():
        if index_key in cfg["indices"]:
            return slug
    return None


def index_dir(key: str) -> Path:
    return DATA_DIR / key


def raw_path(key: str) -> Path:
    return index_dir(key) / "raw.parquet"


def features_path(key: str) -> Path:
    return index_dir(key) / "features.parquet"


def model_path(key: str) -> Path:
    return MODELS_DIR / f"hmm_{key}.pkl"
