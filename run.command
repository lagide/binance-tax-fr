#!/bin/bash
cd "$(dirname "$0")"
source venv/bin/activate
python3 binance_tax.py
open rapport_fiscal_2025.html
