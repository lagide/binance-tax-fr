@echo off
setlocal
cd /d "%~dp0"

echo Installation des dependances de build...
if not exist venv (
  python -m venv venv
)
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install "pyinstaller>=6.0.0"

echo.
echo Creation de l'executable Windows...
pyinstaller --noconfirm --clean --onefile --windowed ^
  --name BinanceTaxFR ^
  --add-data ".env.example;." ^
  gui.py

echo.
echo Termine. Executable disponible dans dist\BinanceTaxFR.exe
pause
