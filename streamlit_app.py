import streamlit as st
import os
import shutil
import tempfile
from concat_videos import concatenate_videos_logic

st.set_page_config(page_title="Video Combiner", page_icon="🎬", layout="wide")

# Custom CSS for premium look
st.markdown("""
    <style>
    .main {
        background-color: #0e1117;
    }
    .stButton>button {
        width: 100%;
        border-radius: 5px;
        height: 3em;
        background-color: #ff4b4b;
        color: white;
        font-weight: bold;
    }
    .stProgress > div > div > div > div {
        background-color: #ff4b4b;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🎬 Video Combiner")
st.markdown("Concatenate multiple MP4 videos losslessly while preserving 360/VR metadata.")

with st.sidebar:
    st.header("Settings")
    mode = st.radio("Processing Mode", ["360", "normal"], index=0, help="360 mode preserves spatial metadata for VR videos.")
    debug = st.checkbox("Enable Debug Logging", value=False)
    output_filename = st.text_input("Output Filename", value="concatenated_video.mp4")
    list_name = st.text_input("Temp List Filename", value="mylist.txt", help="Name of the temporary text file used by FFmpeg.")


input_method = st.radio("Input Method", ["Upload Files", "Local Directory Path"], horizontal=True)

if input_method == "Upload Files":
    uploaded_files = st.file_uploader("Upload MP4 Videos", type=["mp4"], accept_multiple_files=True)
    if uploaded_files:
        st.success(f"Selected {len(uploaded_files)} files.")
        if st.button("Start Concatenation"):
            with tempfile.TemporaryDirectory() as tmp_dir:
                status_text = st.empty()
                progress_bar = st.progress(0)
                status_text.text("Saving uploaded files...")
                for uploaded_file in uploaded_files:
                    with open(os.path.join(tmp_dir, uploaded_file.name), "wb") as f:
                        f.write(uploaded_file.getbuffer())
                
                output_path = os.path.join(tmp_dir, output_filename)
                def streamlit_progress(current, total):
                    progress = min(current / total, 1.0) if total > 0 else 0
                    progress_bar.progress(progress)
                    status_text.text(f"Processing: {current:.1f}s / {total:.1f}s")

                success = concatenate_videos_logic(tmp_dir, output_path, mode=mode, list_name=list_name, debug=debug, progress_callback=streamlit_progress)
                if success and os.path.exists(output_path):
                    st.balloons()
                    with open(output_path, "rb") as f:
                        st.download_button("Download Result", f, file_name=output_filename)
                else:
                    st.error("Concatenation failed.")
    else:
        st.warning("Please upload some MP4 files.")
else:
    local_dir = st.text_input("Local Directory Path", value=os.getcwd(), help="Absolute path to the folder containing videos.")
    output_dir = st.text_input("Output Directory", value=os.getcwd(), help="Where to save the result.")
    
    if st.button("Start Local Concatenation"):
        output_path = os.path.join(output_dir, output_filename)
        status_text = st.empty()
        progress_bar = st.progress(0)
        
        def streamlit_progress(current, total):
            progress = min(current / total, 1.0) if total > 0 else 0
            progress_bar.progress(progress)
            status_text.text(f"Processing: {current:.1f}s / {total:.1f}s")

        success = concatenate_videos_logic(local_dir, output_path, mode=mode, list_name=list_name, debug=debug, progress_callback=streamlit_progress)
        if success:
            st.success(f"Done! Saved to: {output_path}")
            st.balloons()
        else:
            st.error("Failed. Check directory paths and video formats.")


st.markdown("---")
st.markdown("### How it works")
st.markdown("""
1. **Upload:** Select all the MP4 clips you want to join.
2. **Sort:** Files are automatically sorted by name.
3. **Concat:** FFmpeg joins them without re-encoding (no quality loss).
4. **Metadata:** If '360' mode is selected, ExifTool is used to inject spatial metadata from the first clip.
""")
