"""End-to-end audit script. Run from project root: ./venv/bin/python notebooks/audit.py"""
from __future__ import annotations

import pickle
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DB_PATH, INDICES, STATE_LABELS, features_path, model_path, raw_path
from src.features import build_one, feature_cols_for
from src.inference import _filtered_probs, _load_bundle


SEP = "=" * 78
SUB = "-" * 78
PASS = "[PASS]"
WARN = "[WARN]"
FAIL = "[FAIL]"


def banner(title: str):
    print(f"\n{SEP}\n  {title}\n{SEP}")


def sub(title: str):
    print(f"\n{SUB}\n  {title}\n{SUB}")


fails = []
warns = []


def check(condition: bool, msg: str, fail_msg: str = ""):
    if condition:
        print(f"  {PASS}  {msg}")
    else:
        full = f"{msg}{(' — ' + fail_msg) if fail_msg else ''}"
        print(f"  {FAIL}  {full}")
        fails.append(full)


def warn(condition: bool, msg: str):
    if not condition:
        print(f"  {WARN}  {msg}")
        warns.append(msg)
