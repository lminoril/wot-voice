@echo off
rem Запуск приложения без чёрного окна консоли.
if exist "%~dp0app.log" ren "%~dp0app.log" "app.log.old" >nul 2>&1
where pythonw.exe >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw.exe "%~dp0app.py"
) else (
    start "" "C:\Users\о\AppData\Local\Programs\Python\Python310\pythonw.exe" "%~dp0app.py"
)
exit /b 0
