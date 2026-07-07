"""
Shared plumbing for the global smart-money market fetchers.

Each market module caches one small JSON file per trading day under
data/<mkt>/ and rebuilds its feed from those caches, so re-runs are
cheap/offline and a failed fetch never loses history.
"""

import json
import os
import time
import urllib.request
from datetime import date, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # src/smartmoney
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "out")

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def get(url, headers=None, retries=3, timeout=25, pause=1.0, encoding="utf-8"):
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    last = None
    for i in range(retries):
        try:
            if i:
                time.sleep(pause * i)
            req = urllib.request.Request(url, headers=h)
            return urllib.request.urlopen(req, timeout=timeout).read().decode(encoding, "replace")
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"fetch failed for {url}: {last!r}")


def get_impersonated(url, retries=3, timeout=30):
    """Cloudflare-fronted sources (IDX) need TLS fingerprint impersonation."""
    from curl_cffi import requests as cr  # lazy: only markets that need it
    last = None
    for i in range(retries):
        try:
            if i:
                time.sleep(1.5 * i)
            r = cr.get(url, impersonate="chrome124", timeout=timeout)
            if r.status_code == 200:
                return r.text
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"fetch failed for {url}: {last!r}")


def cache_path(mkt, name):
    d = os.path.join(DATA, mkt)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def cache_load(mkt, name):
    p = cache_path(mkt, name)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return None


def cache_save(mkt, name, obj):
    with open(cache_path(mkt, name), "w") as f:
        json.dump(obj, f)


def write_feed(mkt, feed):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"feed_{mkt}.json")
    with open(path, "w") as f:
        json.dump(feed, f, indent=1)
    return path


def trading_days_back(days, end=None):
    """Calendar walk (newest last); weekends skipped, holidays handled by empty caches."""
    end = end or date.today()
    out = []
    for i in range(days, -1, -1):
        d = end - timedelta(days=i)
        if d.weekday() < 5:
            out.append(d)
    return out


def fnum(s):
    if isinstance(s, (int, float)):
        return float(s)
    s = (s or "").strip().replace(",", "")
    if not s or s in ("-", "--", "N/A"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0
