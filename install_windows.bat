@echo off
echo --- Installing Python Dependencies ---
pip install -r requirements.txt

echo.
echo --- Checking for System Dependencies (FFmpeg ^& ExifTool) ---

where ffmpeg >nul 2>nul
if %errorlevel% neq 0 (
    echo FFmpeg not found. Attempting to install via winget...
    winget install ffmpeg
) else (
    echo FFmpeg is already installed.
)

where exiftool >nul 2>nul
if %errorlevel% neq 0 (
    echo ExifTool not found. Attempting to install via winget...
    winget install PhilHarvey.ExifTool
) else (
    echo ExifTool is already installed.
)

echo.
echo Installation complete!
echo To run the app, use: streamlit run streamlit_app.py
pause
