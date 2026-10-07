@echo off
rem Tra loi bai hat (LRCLIB) cho cac bai Spotify da crawl trong data\spotify.db.
rem Chay lai bao nhieu lan cung duoc: bai nao da tra roi thi bo qua.
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo Neu dang mo data\spotify.db trong DB Browser for SQLite thi hay dong lai truoc khi chay.
echo.

where python >nul 2>nul
if %errorlevel%==0 (set "PY=python") else (set "PY=py -3")

%PY% -m pip install -q --disable-pip-version-check -r requirements.txt
%PY% lyrics.py
%PY% check_db.py
pause
