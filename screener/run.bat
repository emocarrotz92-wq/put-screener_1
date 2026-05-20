@echo off
cd /d "%~dp0"
echo Installing dependencies...
pip install -r requirements.txt -q
echo.
echo Starting Put Screener...
echo Open your browser to: http://localhost:5050
echo Press Ctrl+C to stop.
echo.
python app.py
pause
