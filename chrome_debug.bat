@echo off
REM ============================================================
REM  ApplyJob Bot — Chrome con CDP
REM  Perfil DEDICADO — no interfiere con Chrome personal ni MCP.
REM ============================================================

set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
set BOT_PROFILE=%LOCALAPPDATA%\ApplyJobBot\ChromeProfile

if not exist "%BOT_PROFILE%" mkdir "%BOT_PROFILE%"

REM Matar instancia previa del bot Chrome (solo la del bot)
taskkill /F /FI "WINDOWTITLE eq ApplyJobBot-Chrome" >nul 2>&1
timeout /t 1 /nobreak >nul

echo.
echo  Iniciando Chrome del bot (perfil dedicado)...
echo  CDP: 127.0.0.1:9222
echo.
echo  Se abrirá solo el dashboard en Chrome.
 echo  Usa el botón de login del dashboard para abrir los portales activos.
 echo.

start "ApplyJobBot-Chrome" %CHROME% ^
  --remote-debugging-port=9222 ^
  --remote-debugging-address=127.0.0.1 ^
  --user-data-dir="%BOT_PROFILE%" ^
  --no-first-run ^
  --no-default-browser-check ^
  "http://127.0.0.1:5000/"

echo  Chrome iniciado. Cuando termines los logins, vuelve al dashboard
 echo  y haz click en "Guardar sesiones del bot".
echo.
timeout /t 5 /nobreak >nul
