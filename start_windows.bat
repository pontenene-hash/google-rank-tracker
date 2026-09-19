@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo Google順位チェッカーを起動します...
where py >nul 2>nul
if %errorlevel%==0 (
  py -m pip install -r requirements.txt
  py -m streamlit run app.py
) else (
  python -m pip install -r requirements.txt
  python -m streamlit run app.py
)
pause
