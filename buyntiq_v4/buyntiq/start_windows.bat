@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto dependencies
py -3.12 -m venv .venv
if errorlevel 1 py -3.11 -m venv .venv
if errorlevel 1 goto no_python
:dependencies
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto install_failed
".venv\Scripts\python.exe" -m streamlit run app.py --server.headless=false
pause
exit /b
:no_python
echo Install Python 3.12 from python.org, then double-click this file again.
pause
exit /b 1
:install_failed
echo Dependencies could not be installed. Check your internet connection and the error above.
pause
exit /b 1
