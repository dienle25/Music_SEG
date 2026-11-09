@echo off
rem Chay web demo ViSongSearch (SEG301 + TMG301). Dung: run_demo.bat [--db duong\dan\file.db] [--port 8000]
chcp 65001 >nul
cd /d "%~dp0"
python -m webdemo %*
if errorlevel 1 pause
