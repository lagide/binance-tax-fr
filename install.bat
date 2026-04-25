@echo off
cd /d "%~dp0"
echo Installation des dependances Python...
python -m venv venv
call venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo Installation terminee. Tu peux maintenant lancer run.bat
pause
