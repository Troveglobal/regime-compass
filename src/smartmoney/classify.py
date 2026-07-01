"""
Entity + sector classification for NSE bulk/block deals.

The core job: turn messy NSE client names into canonical entities, tag each as
smart money (FII / DII / insurer / AIF / sovereign) vs. prop/HFT noise, and map
symbols to sectors so we can read the "flavour" of accumulation.

All rule tables are data-driven dicts — extend them as you see new names.
"""

import re

# ---------------------------------------------------------------------------
# 1. Entity name canonicalisation
# ---------------------------------------------------------------------------
# NSE prints the same fund many ways: trailing account IDs, "PTE." vs "PTE",
# "EUROPE SE" vs "EUROPESE". We strip noise then map known variant groups to one
# canonical display name so their flow aggregates instead of fragmenting.

_TRAILING_ID = re.compile(r"\b[A-Z]{2,5}\d{6,}\b")          # e.g. MTBJ400045828
_PUNCT = re.compile(r"[.\-,/()]+")
_WS = re.compile(r"\s+")


def _norm(name: str) -> str:
    n = name.upper().strip()
    n = _TRAILING_ID.sub("", n)
    n = _PUNCT.sub(" ", n)
    n = _WS.sub(" ", n).strip()
    return n


# Variant-group → canonical. Matched as substring on the normalised name.
_CANON_GROUPS = [
    ("GOLDMAN SACHS", "Goldman Sachs (ODI)"),
    ("THE MTBJ LTD AS TRST FOR GOV", "GPIF — Japan (Govt Pension Investment Fund)"),
    ("GOVERNMENT PENSION INVESTMENT FUND", "GPIF — Japan (Govt Pension Investment Fund)"),
    ("MORGAN STANLEY", "Morgan Stanley"),
    ("SOCIETE GENERALE", "Societe Generale (ODI)"),
    ("BOFA SECURITIES", "BofA Securities"),
    ("MERRILL LYNCH", "BofA Securities"),
    ("GQG PARTNERS", "GQG Partners"),
    ("WHITEOAK CAPITAL", "WhiteOak Capital MF"),
    ("SBI MUTUAL FUND", "SBI Mutual Fund"),
    ("SBI LIFE INSURANCE", "SBI Life Insurance"),
    ("ICICI PRUDENTIAL", "ICICI Prudential MF"),
    ("ICICI LOMBARD", "ICICI Lombard GIC"),
    ("KOTAK MAHINDRA LIFE", "Kotak Life Insurance"),
    ("KOTAK MAHINDRA MUTUAL", "Kotak MF"),
    ("HDFC MUTUAL", "HDFC MF"),
    ("HDFC LIFE", "HDFC Life Insurance"),
    ("NIPPON LIFE INDIA", "Nippon India MF"),
    ("MIRAE ASSET", "Mirae Asset MF"),
    ("ADITYA BIRLA SUN LIFE", "Aditya Birla Sun Life MF"),
    ("TATA MUTUAL", "Tata MF"),
    ("DSP MUTUAL", "DSP MF"),
    ("AXIS MUTUAL", "Axis MF"),
    ("MOTILAL OSWAL MUTUAL", "Motilal Oswal MF"),
    ("PI OPPORTUNITIES", "Premji Invest (PI Opportunities AIF)"),
    ("PIONEER INVESTMENT FUND", "Pioneer Investment Fund"),
    ("GOVERNMENT OF SINGAPORE", "GIC — Govt of Singapore"),
    ("MONETARY AUTHORITY OF SINGAPORE", "MAS — Singapore"),
    ("ABU DHABI INVESTMENT AUTHORITY", "ADIA — Abu Dhabi"),
    ("NORGES BANK", "Norges Bank (Norway SWF)"),
    ("VANGUARD", "Vanguard"),
    ("BLACKROCK", "BlackRock"),
]


def canonical_entity(name: str) -> str:
    n = _norm(name)
    for needle, canon in _CANON_GROUPS:
        if needle in n:
            return canon
    # Title-case a cleaned version for display of the long tail.
    return " ".join(w.capitalize() for w in n.split())


# ---------------------------------------------------------------------------
# 2. Entity type / smart-money tagging
# ---------------------------------------------------------------------------
# Returned type drives whether flow counts as "smart money" and whether it is
# treated as churn/noise.

FII = "FII/FPI"
DII = "DII (MF)"
INSURANCE = "Insurance"
AIF = "AIF/PMS"
SOVEREIGN = "Sovereign/Pension"
ODI = "ODI/Swap desk"
BROKER = "Broker/Prop"
CORPORATE = "Corporate/Holding"
INDIVIDUAL = "Individual"
UNKNOWN = "Unknown"

# Substring rules, checked in order. First hit wins.
_TYPE_RULES = [
    (SOVEREIGN, ["PENSION INVESTMENT FUND", "GOVERNMENT OF SINGAPORE", "ABU DHABI INVESTMENT",
                 "NORGES BANK", "MONETARY AUTHORITY OF SINGAPORE", "GPIF", "PENSION FUND",
                 "PROVIDENT FUND", "EMPLOYEES PROVIDENT"]),
    (INSURANCE, ["LIFE INSURANCE", "GENERAL INSURANCE", "LOMBARD", "GIC ", "ASSURANCE",
                 "LIFE INS", "HEALTH INSURANCE"]),
    (DII, ["MUTUAL FUND", " MF", "ASSET MANAGEMENT", " AMC", "INVESTMENT TRUST OF INDIA"]),
    (AIF, ["AIF", "ALTERNATIVE INVESTMENT", "PMS", "PORTFOLIO MANAGEMENT",
           "PREMJI INVEST", "PI OPPORTUNITIES", "ABAKKUS", "MARSHALL WACE",
           "MALABAR", "STEADVIEW", "NALANDA", "ICONIQ"]),
    (ODI, ["ODI", "P-NOTE", "PARTICIPATORY"]),
    (FII, ["MAURITIUS", "SINGAPORE", "LUXEMBOURG", "IRELAND", "CAYMAN", "OFFSHORE",
           "EMERGING MARKET", "INTERNATIONAL FUND", "CAPITAL GROUP",
           "GQG", "VANGUARD", "BLACKROCK", "FIDELITY", "FRANKLIN", "SCHRODER",
           "GOVERNMENT PENSION", "MORGAN STANLEY", "GOLDMAN SACHS", "SOCIETE GENERALE",
           "BOFA", "MERRILL", "JPMORGAN", "NOMURA", "CITIGROUP", "UBS ", "HSBC",
           "DEUTSCHE", "BARCLAYS", "PIONEER INVESTMENT", "FPI", "FII"]),
    (BROKER, ["SECURITIES", "BROKING", "BROKERS", "STOCK BROK", "COMMODITIES",
              "CAPITAL MARKETS", "PROP ", "PROPRIETARY", "GRAVITON", "ALPHAGREP",
              "TOWER RESEARCH", "QE SECURITIES", "DOLAT", "JANE STREET"]),
    (CORPORATE, ["LIMITED", "PRIVATE LIMITED", " LLP", "HOLDING", "HOLDINGS",
                 "INVESTMENT AND INDUSTRIES", "ENTERPRISES", "TRADING", "CONSULTANCY",
                 "INFRA", "VENTURES", "FINSERV", "FINANCE", "CAPITAL"]),
]


def entity_type(canonical_name: str) -> str:
    n = canonical_name.upper()
    for typ, needles in _TYPE_RULES:
        if any(k in n for k in needles):
            return typ
    # No corporate/fund marker and short → likely an individual.
    if len(n.split()) <= 4:
        return INDIVIDUAL
    return UNKNOWN


# Which types count as tracked "smart money" by default.
SMART_TYPES = {FII, DII, INSURANCE, AIF, SOVEREIGN, ODI}
# Types we treat as churn/noise candidates (still stored, just flagged).
NOISE_TYPES = {BROKER}


def is_smart(typ: str) -> bool:
    return typ in SMART_TYPES


# ---------------------------------------------------------------------------
# 3. Sector mapping
# ---------------------------------------------------------------------------
# Explicit overrides for the high-value names that drive the flavour read,
# plus a keyword fallback on the security name for the long tail.

SECTOR_OVERRIDE = {
    "LENSKART": "New-Age / Consumer Tech", "ETERNAL": "New-Age / Consumer Tech",
    "POLICYBZR": "New-Age / Consumer Tech", "PAYTM": "New-Age / Consumer Tech",
    "SWIGGY": "New-Age / Consumer Tech", "PINELABS": "New-Age / Consumer Tech",
    "URBANCO": "New-Age / Consumer Tech", "BLACKBUCK": "New-Age / Consumer Tech",
    "DELHIVERY": "Logistics", "NAUKRI": "New-Age / Consumer Tech",
    "INDIAMART": "New-Age / Consumer Tech", "NAZARA": "New-Age / Consumer Tech",
    "CCAVENUE": "New-Age / Consumer Tech", "ZAGGLE": "New-Age / Consumer Tech",
    "INFIBEAM": "New-Age / Consumer Tech", "VMM": "Retail", "TRENT": "Retail",
    "ADANIENT": "Conglomerate", "ADANIGREEN": "Power / Renewables",
    "ADANIENSOL": "Power / Utilities", "ADANIPOWER": "Power / Utilities",
    "ADANIPORTS": "Infrastructure", "RELIANCE": "Conglomerate",
    "KOTAKBANK": "Financials — Banks", "ICICIBANK": "Financials — Banks",
    "HDFCBANK": "Financials — Banks", "SBIN": "Financials — Banks",
    "AXISBANK": "Financials — Banks", "RBLBANK": "Financials — Banks",
    "BANKBARODA": "Financials — Banks", "PNB": "Financials — Banks",
    "CANBK": "Financials — Banks", "IDFCFIRSTB": "Financials — Banks",
    "YESBANK": "Financials — Banks", "AUBANK": "Financials — Banks",
    "UJJIVANSFB": "Financials — Banks", "CAPITALSFB": "Financials — Banks",
    "JIOFIN": "Financials — NBFC", "BAJFINANCE": "Financials — NBFC",
    "BAJAJFINSV": "Financials — NBFC", "SHRIRAMFIN": "Financials — NBFC",
    "CHOLAFIN": "Financials — NBFC", "TATACAP": "Financials — NBFC",
    "ABCAPITAL": "Financials — NBFC", "PFC": "Financials — NBFC",
    "RECLTD": "Financials — NBFC", "MANAPPURAM": "Financials — NBFC",
    "SAMMAANCAP": "Financials — NBFC", "360ONE": "Financials — Capital Markets",
    "NUVAMA": "Financials — Capital Markets", "MOTILALOFS": "Financials — Capital Markets",
    "HDFCAMC": "Financials — Capital Markets", "BSE": "Financials — Capital Markets",
    "SBILIFE": "Financials — Insurance", "HDFCLIFE": "Financials — Insurance",
    "ICICIGI": "Financials — Insurance", "GODIGIT": "Financials — Insurance",
    "MFSL": "Financials — Insurance", "JSWSTEEL": "Metals & Mining",
    "JSL": "Metals & Mining", "HINDALCO": "Metals & Mining", "VEDL": "Metals & Mining",
    "COALINDIA": "Metals & Mining", "USHAMART": "Metals & Mining",
    "BHARTIARTL": "Telecom", "TATACOMM": "Telecom", "INDIGO": "Aviation",
    "GMRAIRPORT": "Infrastructure", "ITC": "FMCG", "ITCHOTELS": "Hotels",
    "VBL": "FMCG", "PATANJALI": "FMCG", "HONASA": "FMCG", "GRMOVER": "FMCG",
    "INFY": "IT Services", "TCS": "IT Services", "WIPRO": "IT Services",
    "HCLTECH": "IT Services", "TECHM": "IT Services", "LTIM": "IT Services",
    "COFORGE": "IT Services", "PERSISTENT": "IT Services", "KPITTECH": "IT Services",
    "MARUTI": "Auto & Components", "M&M": "Auto & Components", "TATAMOTORS": "Auto & Components",
    "TMCV": "Auto & Components", "EICHERMOT": "Auto & Components", "BAJAJ-AUTO": "Auto & Components",
    "HEROMOTOCO": "Auto & Components", "TVSMOTOR": "Auto & Components", "ASHOKLEY": "Auto & Components",
    "ATHERENERG": "Auto & Components", "HYUNDAI": "Auto & Components", "MOTHERSON": "Auto & Components",
    "UNOMINDA": "Auto & Components", "LODHA": "Realty", "DLF": "Realty",
    "GODREJPROP": "Realty", "OBEROIRLTY": "Realty", "PREMIERENE": "Power / Renewables",
    "WAAREEENER": "Power / Renewables", "JSWENERGY": "Power / Utilities",
    "POWERGRID": "Power / Utilities", "NHIT": "InvIT / REIT", "VERTIS": "InvIT / REIT",
    "EMBASSY": "InvIT / REIT", "INDIGRID": "InvIT / REIT", "CUBEINVIT": "InvIT / REIT",
    "NDRINVIT": "InvIT / REIT", "MINDSPACE": "InvIT / REIT", "NXST": "InvIT / REIT",
    "BIRET": "InvIT / REIT", "CAPINVIT": "InvIT / REIT", "ANZEN": "InvIT / REIT",
    "BPCL": "Oil & Gas", "ONGC": "Oil & Gas", "PETRONET": "Oil & Gas",
    "APOLLOHOSP": "Healthcare", "MAXHEALTH": "Healthcare", "FORTIS": "Healthcare",
    "MEDANTA": "Healthcare", "HCG": "Healthcare", "KIMS": "Healthcare",
    "ASTERDM": "Healthcare", "METROPOLIS": "Healthcare", "LALPATHLAB": "Healthcare",
    "AGARWALEYE": "Healthcare", "BEL": "Defence", "HAL": "Defence", "BHEL": "Capital Goods",
    "LT": "Capital Goods", "CGPOWER": "Capital Goods", "KAYNES": "Capital Goods",
    "DIXON": "Capital Goods", "POLYCAB": "Capital Goods", "SOLARINDS": "Chemicals",
    "DEEPAKNTR": "Chemicals", "PIIND": "Chemicals", "COROMANDEL": "Chemicals",
}

# keyword → sector, applied to the upper-cased security name when no override hits.
_SECTOR_KEYWORDS = [
    ("Hotels", ["HOTEL", "HOSPITALITY", "LEELA", "LEMON TREE"]),
    ("InvIT / REIT", ["REIT", "INVIT", "INFRA TRUS", "HIGHWAY", "TRUST"]),
    ("Financials — Banks", ["BANK"]),
    ("Financials — Insurance", ["LIFE INS", "INSURANCE", "ASSURANCE", "LOMBARD", "GIC"]),
    ("Financials — NBFC", ["FINANCE", "FIN CO", "HOUSING FIN", "CAPITAL", "FINSERV", "NBFC"]),
    ("Healthcare", ["PHARMA", "LAB", "LIFESCIENCE", "LIFE SCIENCE", "HEALTHCARE", "HOSPITAL",
                    "REMEDIES", "BIOSCIENCE", "DRUG", "MEDICURE", "HEALTH", "MEDI"]),
    ("Metals & Mining", ["STEEL", "METAL", "ALUM", "ZINC", "IRON", "ISPAT", "FORGING",
                         "FORGE", "MINING"]),
    ("Cement", ["CEMENT"]),
    ("Chemicals", ["CHEM", "NITRITE", "PHOSPHATE", "FERTIL", "PHOSPHATES"]),
    ("Auto & Components", ["MOTOR", "AUTO", "TYRE", "FORGINGS"]),
    ("Power / Utilities", ["POWER", "ENERGY", "ELECTRIC", "GRID", "UTILIT"]),
    ("Oil & Gas", ["OIL", "GAS", "PETRO", "LNG"]),
    ("IT Services", ["TECHNOLOG", "SOFTWARE", "SYSTEMS", "INFOTECH", "DIGITAL", "INFO EDGE"]),
    ("Realty", ["PROPERT", "REALT", "DEVELOPERS", "ESTATE", "INFRASTRUCT"]),
    ("FMCG", ["FOODS", "BEVERAGE", "CONSUMER", "PAINT", "BREW", "DAIRY"]),
    ("Capital Goods", ["ENGINEER", "ELECTRIC", "INDUSTR", "CABLE"]),
]


def sector_for(symbol: str, security_name: str) -> str:
    if symbol in SECTOR_OVERRIDE:
        return SECTOR_OVERRIDE[symbol]
    nm = (security_name or "").upper()
    for sector, kws in _SECTOR_KEYWORDS:
        if any(k in nm for k in kws):
            return sector
    return "Other / Diversified"
