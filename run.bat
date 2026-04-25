@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
python binance_tax.py
if exist rapport_fiscal_2025.html start "" rapport_fiscal_2025.html
pause
