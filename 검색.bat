@echo off
chcp 65001 >nul
cd /d "%~dp0"
python local_search.py
echo.
pause
