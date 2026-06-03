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
echo  Se abriran las pestanas de login automaticamente.
echo  Inicia sesion en cada una y deja esta ventana abierta.
echo.

start "ApplyJobBot-Chrome" %CHROME% ^
  --remote-debugging-port=9222 ^
  --remote-debugging-address=127.0.0.1 ^
  --user-data-dir="%BOT_PROFILE%" ^
  --no-first-run ^
  --no-default-browser-check ^
  "https://www.linkedin.com/login" ^
  "https://cl.computrabajo.com" ^
  "https://www.laborum.cl" ^
  "https://www.trabajando.cl" ^
  "https://www.infojobs.net" ^
  "https://www.getonbrd.com/auth/sign_in"

echo  Chrome iniciado. Cuando termines los logins, vuelve al dashboard
echo  y hace click en "Guardar sesiones del bot".
echo.
timeout /t 5 /nobreak >nul
