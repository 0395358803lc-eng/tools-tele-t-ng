@echo off
title Stop Multi TG Manager
echo Stopping server on port 8000...
for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
  echo Killing PID %%p
  taskkill /PID %%p /F >nul 2>nul
)
echo Done.
timeout /t 2 >nul
