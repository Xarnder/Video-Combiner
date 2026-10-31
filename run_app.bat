@echo off
setlocal
cd /d "%~dp0"

rem Prefer the project's virtual environment, then Python on PATH.
set "APP_PYTHON=python"
if exist "%~dp0venv\Scripts\python.exe" set "APP_PYTHON=%~dp0venv\Scripts\python.exe"
if exist "%~dp0.venv\Scripts\python.exe" set "APP_PYTHON=%~dp0.venv\Scripts\python.exe"

"%APP_PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo Python was not found. Install Python and enable "Add Python to PATH".
    pause
    exit /b 1
)

"%APP_PYTHON%" -c "import streamlit, streamlit_sortables, tqdm, PIL" >nul 2>&1
if errorlevel 1 (
    echo Installing required Python packages...
    "%APP_PYTHON%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo Could not install dependencies. Check the errors above.
        pause
        exit /b 1
    )
)

echo Starting Video Combiner...
echo Keep this window open while using the app. Press Ctrl+C to stop.
"%APP_PYTHON%" -m streamlit run "%~dp0streamlit_app.py" --server.maxUploadSize 102400
if errorlevel 1 (
    echo Video Combiner stopped with an error. Check the messages above.
    pause
    exit /b 1
)
endlocal
