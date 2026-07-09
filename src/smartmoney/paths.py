"""Data paths + one-time volume seeding for the smart-money module.

The accumulating data is the raw NSE deal CSVs (data/raw/), from which the
pipeline rebuilds the derived store.db each run; the price cache (data/prices/)
is a fetch cache. On Railway the container filesystem is ephemeral, so without a
persistent volume the daily-fetched raw CSVs (and thus history) are lost on every
deploy.

If SMARTMONEY_DATA_DIR is set (a mounted volume), raw/, prices/ and store.db live
there and survive deploys. On first boot with an empty volume we seed it ONCE from
the committed in-repo snapshot so the existing year of history isn't lost; after
that the volume is the source of truth and the committed copies are only a
bootstrap seed. Without the env var (local dev) everything stays in-repo, unchanged.
"""
import os
import shutil

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_DB = os.path.join(_HERE, "store.db")
_REPO_RAW = os.path.join(_HERE, "data", "raw")
_REPO_PRICES = os.path.join(_HERE, "data", "prices")

_VOL = (os.environ.get("SMARTMONEY_DATA_DIR") or "").strip() or None


def _seed():
    """Copy the committed snapshot into an empty volume, once (idempotent)."""
    if not _VOL:
        return
    try:
        os.makedirs(_VOL, exist_ok=True)
        dst_db = os.path.join(_VOL, "store.db")
        if not os.path.exists(dst_db) and os.path.exists(_REPO_DB):
            shutil.copy2(_REPO_DB, dst_db)
        for name, src in (("raw", _REPO_RAW), ("prices", _REPO_PRICES)):
            dst = os.path.join(_VOL, name)
            if not os.path.isdir(dst) and os.path.isdir(src):
                shutil.copytree(src, dst)
    except Exception:  # never let seeding crash startup — fall back to in-repo below
        pass


_seed()

if _VOL and os.path.isdir(_VOL):
    DB_PATH = os.path.join(_VOL, "store.db")
    RAW_DIR = os.path.join(_VOL, "raw")
    PRICES_DIR = os.path.join(_VOL, "prices")
else:
    DB_PATH = _REPO_DB
    RAW_DIR = _REPO_RAW
    PRICES_DIR = _REPO_PRICES

DAILY_DIR = os.path.join(RAW_DIR, "daily")
