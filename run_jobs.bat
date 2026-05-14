@echo off
echo ========================================================
echo INICIANDO POSTULACIONES AUTOMATICAS (MODO DIRECTO)
echo Portales: Indeed, Computrabajo, Chiletrabajos, Laborum
echo Keywords: IT, Dev, Bodega...
echo ========================================================

echo.
echo [1/4] Iniciando INDEED...
.\.venv\Scripts\python.exe main.py --portal indeed
timeout /t 5 >nul

echo.
echo [2/4] Iniciando COMPUTRABAJO...
.\.venv\Scripts\python.exe main.py --portal computrabajo
timeout /t 5 >nul

echo.
echo [3/4] Iniciando CHILETRABAJOS...
.\.venv\Scripts\python.exe main.py --portal chiletrabajos
timeout /t 5 >nul

echo.
echo [4/4] Iniciando LABORUM...
.\.venv\Scripts\python.exe main.py --portal laborum

echo.
echo ========================================================
echo TODAS LAS POSTULACIONES FINALIZADAS
echo ========================================================
pause
