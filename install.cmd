@echo off
rem Videocassete Web - instalador para Windows. Duplo clique neste arquivo.
rem Garante um Python 3.9+ (instala pelo winget se faltar) e roda install.py.
setlocal
cd /d "%~dp0"
set "CHECK=import sys; sys.exit(0 if sys.version_info >= (3, 9) and 'WindowsApps' not in sys.executable else 1)"
set "PY="

where py >nul 2>nul && py -3 -c "%CHECK%" >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && python -c "%CHECK%" >nul 2>nul && set "PY=python"
if not defined PY call :known

if not defined PY (
  echo Python 3.9+ nao encontrado / not found. Instalando / installing: winget install Python.Python.3.13
  where winget >nul 2>nul || goto :nowinget
  winget install -e --id Python.Python.3.13 --scope user --accept-source-agreements --accept-package-agreements --disable-interactivity
  call :known
)
if not defined PY goto :nopython

%PY% "%~dp0install.py" %*
set "RC=%ERRORLEVEL%"
if not defined VIDEOCASSETE_NO_PAUSE pause
exit /b %RC%

:known
for %%V in (314 313 312 311 310 39) do (
  if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
  if not defined PY if exist "%ProgramFiles%\Python%%V\python.exe" set PY="%ProgramFiles%\Python%%V\python.exe"
)
exit /b 0

:nowinget
echo winget nao encontrado / not found. Instale o Python 3.9+ de / install Python 3.9+ from https://www.python.org/downloads/
echo e rode este arquivo de novo / and run this file again.
:nopython
if not defined VIDEOCASSETE_NO_PAUSE pause
exit /b 1
