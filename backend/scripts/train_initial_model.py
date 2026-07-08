#!/usr/bin/env python3
"""Bootstrap: build seed dataset and train the initial bot classifier.

Usage::

    cd backend
    python -m scripts.train_initial_model
"""
from __future__ import annotations

import sys
from pathlib import Path

# allow `python -m scripts.train_initial_model` from the backend dir
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.train_bot import train_bot_classifier  # noqa: E402


def main() -> None:
    print("Training initial bot classifier on seed dataset...")
    out = train_bot_classifier()
    print("Done:", json_dumps(out))


def json_dumps(o):
    import json
    return json.dumps(o, indent=2, default=str)


if __name__ == "__main__":
    main()