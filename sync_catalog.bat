@echo off
title RYAL — Sync Catálogo
cd /d "%~dp0config"

echo.
echo  RYAL — Actualizando catálogo
echo  ==============================
echo.

call ..\venv\Scripts\activate.bat

echo [1/3] Scraping proveedores...
echo.
python manage.py sync_catalog %*

echo.
echo  Listo. Presiona cualquier tecla para cerrar.
pause > nul
