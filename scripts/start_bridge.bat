@echo off
setlocal
cd /d "%~dp0\.."
echo Starting MT5 Python Bridge Server...
python scripts\run_bridge_server.py
endlocal
