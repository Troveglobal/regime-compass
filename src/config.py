import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODELS_DIR = ROOT / "models"
LOGS_DIR = ROOT / "logs"

DB_PATH = DATA_DIR / "regime.db"

START_DATE = "2010-01-01"

N_STATES = 3
VOL_WINDOW = 10
RANDOM_STATE = 42

STATE_LABELS = ["bear", "neutral", "bull"]

DATA_SOURCE = os.getenv("HMM_DATA_SOURCE", "yfinance")

API_HOST = os.getenv("HMM_API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("HMM_API_PORT", "8001"))
API_CORS_ORIGINS = os.getenv("HMM_CORS_ORIGINS", "*").split(",")


# Registry of supported indices. To add a new one, append here.
# - price: the index ticker (required)
# - fx:    a USD pair to capture currency stress (optional; None to skip)
# - vix:   implied-vol index (optional; None means use realized vol only)
INDICES = {
    "nifty": {
        "name": "Nifty 50",
        "country": "India",
        "currency": "INR",
        "tickers": {"price": "^NSEI", "fx": "INR=X", "vix": "^INDIAVIX"},
        # cash_rate = approximate annualised yield earned on cash during bear periods
        # India: liquid fund / overnight ~ 6.5% historical average over backtest window
        "cash_rate": 0.065,
        "cash_label": "Indian liquid fund",
    },
    "spx": {
        "name": "S&P 500",
        "country": "USA",
        "currency": "USD",
        "tickers": {"price": "^GSPC", "fx": "DX-Y.NYB", "vix": "^VIX"},
        # US 3-month T-bill avg 2010-2024 was ~ 1.5-2%
        "cash_rate": 0.020,
        "cash_label": "US T-bill",
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
    "btc": {
        "name": "Bitcoin",
        "country": "Crypto",
        "currency": "USD",
        # DXY as the "FX stress" proxy for crypto. No reliable BVIV on yfinance.
        "tickers": {"price": "BTC-USD", "fx": "DX-Y.NYB", "vix": None},
        # Cash equivalent for crypto: US T-bill / stablecoin yield ~2%
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
}

DEFAULT_INDEX = "nifty"


def index_dir(key: str) -> Path:
    return DATA_DIR / key


def raw_path(key: str) -> Path:
    return index_dir(key) / "raw.parquet"


def features_path(key: str) -> Path:
    return index_dir(key) / "features.parquet"


def model_path(key: str) -> Path:
    return MODELS_DIR / f"hmm_{key}.pkl"
