@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

pyinstaller --noconfirm --clean --windowed --name MineModsUpdater --paths src src/main.py

echo.
echo Build termine.
echo Executable: dist\MineModsUpdater\MineModsUpdater.exe
