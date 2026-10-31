# Video Combiner

Run `pip install -r requirements.txt`, then `streamlit run streamlit_app.py`.
FFmpeg and ffprobe must be installed and available on PATH. Existing 360 mode also uses ExifTool.

## Multiple Video Comparison Contact Sheets

Select **Comparison Contact Sheets** in Processing Mode. Choose an input folder and output directory. The mode scans the immediate folder for MP4, MOV, MKV, AVI, WebM and M4V files, sorted alphabetically. Set the number of frames (default 16), thumbnail bounds, optional sheet dimensions, image format, position labels and whether to retain extracted thumbnails.

Each sample position produces one sheet containing every video. Samples span 0% to 100% of each video's own duration; the endpoint seeks to the last frame, with a small backwards retry if that frame cannot be decoded. A single sample uses 50%. FFmpeg preserves display aspect ratio and the grid letterboxes thumbnails. Filename labels always appear. Custom sheet dimensions must have enough space for the grid; thumbnail dimensions control extraction resolution.

Each run creates a separate `Comparison_…` directory with `Comparison_001.png` (or `.jpg`) and subsequent sheets, plus a manifest containing ordering, sample positions, extraction count and warnings. Optional thumbnails are grouped under `frames` by source video. Unreadable videos and failed frames retain labelled placeholder cells in the grid. The UI provides progress, a sheet preview/download selector and an output-folder button. Processing happens in a worker; memory holds one sheet and thumbnail at a time.

Run checks with `python -m unittest discover -s tests -v`.
