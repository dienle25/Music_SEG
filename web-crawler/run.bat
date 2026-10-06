@echo off
rem Chay nhanh tren Windows: cai thu vien -> crawl -> xem du lieu trong database
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if %errorlevel%==0 (set "PY=python") else (set "PY=py -3")

%PY% -m pip install -q --disable-pip-version-check -r requirements.txt
%PY% main.py
%PY% check_db.py
pause
