"""Streamlit controls for the comparison sheet workflow."""
import os
import queue
import time
from concurrent.futures import ThreadPoolExecutor

import streamlit as st

from comparison_sheets import find_comparison_videos, generate_comparison_sheets


def render_comparison_mode(folder_picker, indicator_class, open_folder_button):
    st.subheader('Multiple Video Comparison Contact Sheets')
    st.caption('One sheet per relative playback position, with every video in a consistent alphabetical grid. Sampling spans 0–100%; 100% uses the last available frame.')
    folder = folder_picker('Video folder', 'comparison_input', 'comparison_choose_input')
    output = folder_picker('Output directory', 'comparison_output', 'comparison_choose_output')
    a, b, c = st.columns(3)
    count = int(a.number_input('Frames per video / sheets', 1, 1000, 16))
    tw = int(b.number_input('Thumbnail width (px)', 32, 4096, 320))
    th = int(c.number_input('Thumbnail height (px)', 32, 4096, 180))
    a, b, c = st.columns(3)
    image_format = a.selectbox('Image format', ['PNG', 'JPEG'])
    show_position = b.checkbox('Show timestamp and percentage', True)
    keep_frames = c.checkbox('Keep extracted frames', False)
    sheet_size = None
    if st.checkbox('Custom contact sheet dimensions'):
        a, b = st.columns(2)
        sheet_size = (int(a.number_input('Sheet width (px)', 128, 20000, 1920)),
                      int(b.number_input('Sheet height (px)', 128, 20000, 1080)))
    videos = []
    try:
        videos = find_comparison_videos(folder)
        st.info(f'{len(videos)} videos · {count} sheets · {len(videos) * count} frame extractions')
        with st.expander('Video order and supported formats'):
            st.caption('MP4, MOV, MKV, AVI, WebM, M4V. Scans files directly inside the selected folder.')
            for video in videos:
                st.text(video.name)
    except OSError as exc:
        st.warning(f'Choose a readable video folder: {exc}')
    st.caption('Each run is saved in a new Comparison_ folder inside your output directory.')
    if st.button('Generate Comparison Contact Sheets', type='primary', disabled=not videos or not output):
        indicator = indicator_class()
        events = queue.Queue()
        def progress(current, total):
            events.put(('progress', (current, total)))
        def status(message):
            events.put(('status', message))
        def drain():
            latest_progress = latest_message = None
            while True:
                try:
                    kind, value = events.get_nowait()
                    if kind == 'progress':
                        latest_progress = value
                    else:
                        latest_message = value
                except queue.Empty:
                    break
            if latest_message:
                indicator.add_message(latest_message)
            if latest_progress:
                current, total = latest_progress
                elapsed = indicator._elapsed()
                indicator._render('Generating comparison sheets', latest_message or 'Extracting thumbnails…',
                                  current / total, f'{current}/{total} steps',
                                  f'Elapsed: {elapsed:.0f}s', 'Processing…')
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                job = executor.submit(generate_comparison_sheets, folder, output, count, (tw, th),
                                      sheet_size, image_format, show_position, keep_frames,
                                      progress, status)
                while not job.done():
                    drain()
                    time.sleep(0.15)
                drain()
                result = job.result()
            st.session_state['comparison_result'] = result
            indicator.complete('Comparison sheets complete', f"Saved {len(result['paths'])} sheets and extracted {result['extracted_frames']} frames")
        except Exception as exc:
            st.error(f'Could not generate comparison sheets: {exc}')
    result = st.session_state.get('comparison_result')
    if result:
        st.success(f"{len(result['paths'])} sheets saved to {result['output_dir']}")
        if result['warnings']:
            with st.expander(f"{len(result['warnings'])} extraction warnings"):
                for warning in result['warnings']:
                    st.text(warning)
        existing = [p for p in result['paths'] if os.path.isfile(p)]
        if existing:
            selected = st.selectbox('Preview / download sheet', existing, format_func=os.path.basename)
            st.image(selected)
            with open(selected, 'rb') as image:
                st.download_button('Download sheet', image, file_name=os.path.basename(selected),
                                   mime='image/png' if selected.endswith('.png') else 'image/jpeg')
        open_folder_button(result['output_dir'], 'comparison_open_output')
