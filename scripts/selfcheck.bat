@echo off
REM MT5 Bridge Startup Self-Check Wrapper
setlocal
cd /d "%~dp0\.."
python scripts\selfcheck.py %*
exit /b %ERRORLEVEL%
