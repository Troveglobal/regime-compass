"""
Member metadata — party, chamber, state, district, committees — from the
community-maintained unitedstates/congress-legislators dataset (the de-facto
free replacement for the sunset ProPublica Congress API).

Joined downstream by (last name + state) with nickname normalisation, since
the disclosure feeds don't carry bioguide IDs. Cached ~weekly.
"""

import json
import os
import time

import yaml

try:
    from ..markets import common as X
except ImportError:
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "markets"))
    import common as X

MKT = "congress"
BASE = "https://unitedstates.github.io/congress-legislators/"  # official distribution mirror
FILES = {
    "legislators": "legislators-current.yaml",
    "memberships": "committee-membership-current.yaml",
    "committees": "committees-current.yaml",
}
HISTORICAL = "legislators-historical.yaml"
HISTORICAL_SINCE = "2025-01-01"  # ex-members can still have filings in our window
MAX_AGE = 7 * 86400


def _fresh(path):
    return os.path.exists(path) and time.time() - os.path.getmtime(path) < MAX_AGE


def load():
    """Returns {bioguide: member} with party/chamber/state/district/committees."""
    cache = X.cache_path(MKT, "members.json")
    if _fresh(cache):
        with open(cache) as f:
            return json.load(f)

    raw = {}
    for key, fname in FILES.items():
        raw[key] = yaml.safe_load(X.get(BASE + fname, timeout=60,
                                        headers={"Accept": "text/plain"}))

    comm_names = {}
    for c in raw["committees"]:
        comm_names[c["thomas_id"]] = c["name"]
        for sub in c.get("subcommittees", []):
            comm_names[c["thomas_id"] + sub["thomas_id"]] = c["name"] + " — " + sub["name"]

    def _entry(m, former=False):
        term = m["terms"][-1]
        return {
            "bioguide": m["id"]["bioguide"],
            "name": (m["name"].get("official_full")
                     or (m["name"]["first"] + " " + m["name"]["last"])) + (" (former)" if former else ""),
            "first": m["name"]["first"],
            "last": m["name"]["last"],
            "nickname": m["name"].get("nickname", ""),
            "chamber": "Senate" if term["type"] == "sen" else "House",
            "party": term.get("party", ""),
            "state": term.get("state", ""),
            "district": term.get("district"),
            "committees": [],
        }

    by_bioguide = {}
    try:  # recently departed members first, so current data wins any collision
        for m in yaml.safe_load(X.get(BASE + HISTORICAL, timeout=120)):
            if m["terms"][-1].get("end", "") >= HISTORICAL_SINCE:
                by_bioguide[m["id"]["bioguide"]] = _entry(m, former=True)
    except Exception:
        pass  # former-member metadata is a nice-to-have
    for m in raw["legislators"]:
        by_bioguide[m["id"]["bioguide"]] = _entry(m)

    for code, members in raw["memberships"].items():
        cname = comm_names.get(code)
        if not cname or "—" in cname:  # top-level committees only
            continue
        for mm in members:
            bid = mm.get("bioguide")
            if bid in by_bioguide:
                title = mm.get("title")
                by_bioguide[bid]["committees"].append(cname + (f" ({title})" if title else ""))

    X.cache_save(MKT, "members.json", by_bioguide)
    return by_bioguide


def _fold(s):
    """Lowercase + strip accents (Sánchez -> sanchez)."""
    import unicodedata
    return unicodedata.normalize("NFKD", (s or "").lower()).encode("ascii", "ignore").decode()


def matcher(members):
    """Build a lookup: (normalised last, state) and (normalised last, chamber) -> bioguide."""
    idx = {}
    for bid, m in members.items():
        lasts = {_fold(m["last"])}
        if " " in m["last"]:  # "McClain Delaney" also matchable as "Delaney"
            lasts.add(_fold(m["last"].split()[-1]))
        keys = set()
        for ln in lasts:
            keys.add((ln, m["state"], m["chamber"]))
            keys.add((ln, m["state"], None))
        for k in keys:
            idx.setdefault(k, []).append(bid)
    return idx


def match(idx, members, last, first="", state=None, chamber=None):
    """Resolve a disclosure name to a bioguide ID; None if ambiguous/unknown."""
    last = _fold(last).strip()
    last = __import__("re").sub(r",?\s*(jr|sr|ii|iii|iv|v)\.?$", "", last).replace(",", "").strip()
    for key in ((last, state, chamber), (last, state, None)):
        if state is None and key[1] is None and key[2] is None:
            continue
        cands = idx.get(key, [])
        if len(cands) == 1:
            return cands[0]
        if len(cands) > 1 and first:
            f = first.lower().strip().rstrip(".")
            for bid in cands:
                m = members[bid]
                if (m["first"].lower().startswith(f) or f.startswith(m["first"].lower())
                        or m["nickname"].lower() == f):
                    return bid
    if chamber and not state:  # senate gives no state — try last+chamber across all
        cands = [bid for bid, m in members.items()
                 if _fold(m["last"]) == last and m["chamber"] == chamber]
        if len(cands) == 1:
            return cands[0]
        f = (first or "").lower().strip().rstrip(".")
        for bid in cands:
            m = members[bid]
            if f and (m["first"].lower().startswith(f) or f.startswith(m["first"].lower())
                      or m["nickname"].lower() == f):
                return bid
    return None


if __name__ == "__main__":
    ms = load()
    sens = [m for m in ms.values() if m["chamber"] == "Senate"]
    print(f"{len(ms)} members, {len(sens)} senators")
    ex = next(m for m in ms.values() if m["last"] == "McConnell")
    print(ex["name"], "|", ex["party"], ex["state"], "|", ex["committees"][:2])
