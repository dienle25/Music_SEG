@echo off
title hopamchuan_phuquy - Zing MP3 Crawler

echo ================================================
echo hopamchuan_phuquy - Zing MP3 Crawler
echo ================================================
echo.

python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Cannot install requirements.
    pause
    exit /b 1
)

python main.py
echo.
echo ================================================
echo Checking SQLite database...
echo ================================================
python check_db.py

echo.
pause
