#!/bin/bash

# Ensure script is run from the project directory
cd "$(dirname "$0")"

echo "--- Installing Python Dependencies ---"
pip install -r requirements.txt

echo ""
echo "--- Checking for System Dependencies (FFmpeg & ExifTool) ---"

# Check for Homebrew
if ! command -v brew &> /dev/null
then
    echo "Homebrew not found. Please install Homebrew (https://brew.sh/) to automatically install FFmpeg and ExifTool."
    echo "Or install them manually: "
    echo "  FFmpeg: https://ffmpeg.org/"
    echo "  ExifTool: https://exiftool.org/"
else
    echo "Homebrew found. Installing FFmpeg and ExifTool..."
    brew install ffmpeg exiftool
fi

echo ""
echo "Installation complete!"
echo "You can now run the app using: ./run_app.command"
