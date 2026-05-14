@echo off
title ApplyJob Bot - Iniciando...
color 0A
cls

echo.
echo  ==========================================
echo   ApplyJob Bot - Iniciando sistema...
echo  ==========================================
echo.

echo  [1/3] Cerrando Chrome existente...
taskkill /F /IM chrome.exe /T >nul 2>&1
timeout /t 3 /nobreak >nul

echo  [2/3] Abriendo Chrome bot (perfil sessions\indeed)...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
    --remote-debugging-port=9222 ^
    --user-data-dir="%~dp0sessions\indeed" ^
    --no-first-run ^
    --no-default-browser-check ^
    http://127.0.0.1:5000

timeout /t 4 /nobreak >nul

echo  [3/3] Iniciando servidor...
echo.
echo  Dashboard: http://127.0.0.1:5000
echo  Puerto CDP: 9222
echo.
cd /d "%~dp0"
.venv\Scripts\python -X utf8 gui_server.py

pause
