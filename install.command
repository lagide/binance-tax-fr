#!/bin/bash
cd "$(dirname "$0")"
echo "Installation des dépendances Python..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo "✅ Installation terminée. Tu peux maintenant lancer run.command"
