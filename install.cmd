@echo off
chcp 65001 >nul
rem Запускает setup.ps1 в обход политики выполнения скриптов.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
echo.
pause
