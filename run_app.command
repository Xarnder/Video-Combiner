#!/bin/bash
cd "$(dirname "$0")"
echo "Starting Video Combiner..."
# Check if streamlit is installed
if ! command -v streamlit &> /dev/null
then
    echo "Streamlit not found. Installing dependencies from requirements.txt..."
    pip install -r requirements.txt
fi
streamlit run streamlit_app.py --server.maxUploadSize 102400
