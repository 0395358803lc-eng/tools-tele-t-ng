@echo off
title Multi TG Manager
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
if "!ROOT:~-1!"=="\" set "ROOT=!ROOT:~0,-1!"
cd /d "!ROOT!"

echo.
echo ============================================
echo   Multi TG Manager
echo   !ROOT!
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found on PATH.
  echo Install Python 3.10+ from https://python.org
  pause
  exit /b 1
)

set "VENV_PY=!ROOT!\backend\.venv\Scripts\python.exe"
if not exist "!VENV_PY!" (
  echo [setup] Creating Python venv...
  python -m venv "!ROOT!\backend\.venv"
)

echo [check] Verifying Python packages...
"!VENV_PY!" -c "import fastapi, telethon, bcrypt, itsdangerous, asyncpg, cryptography, alembic" >nul 2>nul
set "DEPS_OK=!errorlevel!"
echo [check] DEPS_OK=!DEPS_OK!

if not "!DEPS_OK!"=="0" (
  echo [setup] Installing Python packages... please wait 1-3 minutes
  "!VENV_PY!" -m pip install --upgrade pip
  "!VENV_PY!" -m pip install -r "!ROOT!\backend\requirements.txt"
  if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
  )
)

set "ENVFILE=!ROOT!\backend\.env"
set "ENVEXAMPLE=!ROOT!\backend\.env.example"
echo [check] Looking for env at: !ENVFILE!

if not exist "!ENVFILE!" (
  echo [setup] Creating backend\.env from .env.example...
  if not exist "!ENVEXAMPLE!" (
    echo [ERROR] backend\.env.example is missing.
    pause
    exit /b 1
  )
  copy /Y "!ENVEXAMPLE!" "!ENVFILE!" >nul
  "!VENV_PY!" -c "import secrets,re,pathlib,os; p=pathlib.Path(os.environ['ENVFILE']); t=p.read_text(encoding='utf-8'); t=re.sub(r'SESSION_SECRET=.*', 'SESSION_SECRET='+secrets.token_urlsafe(48), t, count=1); t=re.sub(r'TWOFA_ENCRYPTION_KEY=.*', 'TWOFA_ENCRYPTION_KEY='+secrets.token_urlsafe(48), t, count=1); t=t.replace('COOKIE_SECURE=true','COOKIE_SECURE=false'); p.write_text(t, encoding='utf-8')"
  echo.
  echo ============================================
  echo   FIRST RUN - please fill backend\.env
  echo ============================================
  echo   - APP_PASSWORD ^(your login password^)
  echo   - DATABASE_URL ^(PostgreSQL connection string^)
  echo.
  echo   Telegram API ID/Hash are configured after app login in Settings.
  echo.
  echo   Save Notepad, close it, then re-run start.bat
  echo ============================================
  start "" notepad "!ENVFILE!"
  pause
  exit /b 0
) else (
  echo [check] backend\.env found.
)

where node >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Node.js not found on PATH. Install Node 18+ from https://nodejs.org
  pause
  exit /b 1
)
if not exist "!ROOT!\frontend\node_modules" (
  echo [setup] Installing frontend packages...
  pushd "!ROOT!\frontend"
  call npm ci
  if errorlevel 1 (
    popd
    echo [ERROR] npm install failed.
    pause
    exit /b 1
  )
  popd
)
echo [build] Building frontend...
pushd "!ROOT!\frontend"
call npm run build
if errorlevel 1 (
  popd
  echo [ERROR] Frontend build failed.
  pause
  exit /b 1
)
popd

echo [db] Applying database migrations...
pushd "!ROOT!\backend"
"!VENV_PY!" migrate.py
if errorlevel 1 (
  popd
  echo [ERROR] Database migration failed. Check DATABASE_URL.
  pause
  exit /b 1
)
popd

echo.
echo ============================================
echo   Server: http://localhost:8000
echo   Close this window to stop.
echo ============================================
echo.

start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:8000"

cd /d "!ROOT!\backend"
"!VENV_PY!" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo Server stopped.
pause
endlocal
