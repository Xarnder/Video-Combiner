#!/bin/bash
cd "$(dirname "$0")"
echo "Starting Video Combiner..."
# Check if streamlit is installed
if ! command -v streamlit &> /dev/null
then
    echo "Streamlit not found. Installing dependencies..."
    pip install streamlit tqdm
fi
streamlit run streamlit_app.py
