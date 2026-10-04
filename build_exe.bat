@echo off
setlocal
cd /d "%~dp0"
python -m venv .build-venv
if errorlevel 1 exit /b 1
".build-venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller==6.22.3
if errorlevel 1 exit /b 1
".build-venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed --name TelegramCleanup tg_cleanup.py
if errorlevel 1 exit /b 1
echo EXE: %~dp0dist\TelegramCleanup.exe
