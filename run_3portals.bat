@echo off
chcp 65001 >nul
echo ========================================================
echo  APPLYJOB BOT — Portales activos
echo  ChileTrabajos  /  Laborum  /  GetOnBoard
echo  Mode: multi-keyword (7 keywords x portal)
echo ========================================================
echo.

REM Verificar que el venv existe
if not exist ".\.venv\Scripts\python.exe" (
    echo [ERROR] No se encontro el virtualenv en .venv\
    echo Ejecuta: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo [1/3] Iniciando CHILETRABAJOS...
echo       (Si pide login: inicia sesion en el browser y el bot continua solo)
echo.
.\.venv\Scripts\python.exe main.py --portal chiletrabajos --multi-keyword
echo.
echo [ChileTrabajos finalizado] Esperando 10s antes del siguiente portal...
timeout /t 10 >nul

echo.
echo [2/3] Iniciando LABORUM...
echo       (Si pide login: inicia sesion en el browser y el bot continua solo)
echo.
.\.venv\Scripts\python.exe main.py --portal laborum --multi-keyword
echo.
echo [Laborum finalizado] Esperando 10s antes del siguiente portal...
timeout /t 10 >nul

echo.
echo [3/3] Iniciando GETONBOARD...
echo       (GetOnBoard registra ofertas externas — no requiere cuenta propia)
echo.
.\.venv\Scripts\python.exe main.py --portal getonyboard --multi-keyword

echo.
echo ========================================================
echo  TODAS LAS POSTULACIONES FINALIZADAS
echo  Revisa logs\applied_HOY.csv para ver el resumen
echo ========================================================
.\.venv\Scripts\python.exe main.py --stats
pause
