import streamlit as st
import os
import shutil
import tempfile
import time
from concat_videos import (
    MIN_TIKTOK_SLOW_FACTOR,
    TIKTOK_CUT_AFTER_SECONDS,
    TIKTOK_NORMAL_SECONDS,
    TIKTOK_SLOW_FACTOR,
    VIDEO_EXTENSIONS,
    concatenate_videos_logic,
    validate_video_consistency
)

# Constants
OUTPUT_CACHE_DIR = "output_cache"

# Ensure cache directory exists
if not os.path.exists(OUTPUT_CACHE_DIR):
    os.makedirs(OUTPUT_CACHE_DIR)

def get_dir_size(path):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    return total_size

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

def clear_cache():
    for filename in os.listdir(OUTPUT_CACHE_DIR):
        file_path = os.path.join(OUTPUT_CACHE_DIR, filename)
        try:
            if os.path.isfile(file_path) or os.path.islink(file_path):
                os.unlink(file_path)
            elif os.path.isdir(file_path):
                shutil.rmtree(file_path)
        except Exception as e:
            st.error(f"Failed to delete {file_path}. Reason: {e}")

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
    /* Video container styling */
    .video-container {
        margin-top: 20px;
        margin-bottom: 20px;
        border-radius: 10px;
        overflow: hidden;
        border: 1px solid #333;
    }
    </style>
    """, unsafe_allow_html=True)

st.title("🎬 Video Combiner")
st.markdown("Concatenate multiple MP4/MOV videos, preserve 360/VR metadata, or create a TikTok-style slow-motion edit.")

with st.sidebar:
    st.header("Settings")
    mode = st.radio(
        "Processing Mode",
        ["360", "normal", "tiktok"],
        index=1,
        help="TikTok mode plays the first 0.5s of every clip normally, then slows the rest to 0.4x."
    )
    debug = st.checkbox("Enable Debug Logging", value=False)
    tiktok_normal_seconds = TIKTOK_NORMAL_SECONDS
    tiktok_slow_factor = TIKTOK_SLOW_FACTOR
    tiktok_cut_after_seconds = TIKTOK_CUT_AFTER_SECONDS
    if mode == "tiktok":
        tiktok_normal_seconds = st.slider(
            "Normal Play Duration",
            min_value=0.0,
            max_value=5.0,
            value=float(TIKTOK_NORMAL_SECONDS),
            step=0.1,
            format="%.1f s"
        )
        tiktok_slow_factor = st.slider(
            "Slow Speed",
            min_value=float(MIN_TIKTOK_SLOW_FACTOR),
            max_value=1.0,
            value=float(TIKTOK_SLOW_FACTOR),
            step=0.05,
            format="%.2f x"
        )
        tiktok_cut_after_seconds = st.slider(
            "Cut Each Clip After",
            min_value=0.0,
            max_value=15.0,
            value=float(TIKTOK_CUT_AFTER_SECONDS),
            step=0.1,
            format="%.1f s",
            help="Use 0 to keep the full processed clip. Any other value cuts to the next video after that many output seconds."
        )
    output_filename = st.text_input("Output Filename", value="concatenated_video.mp4")
    list_name = st.text_input("Temp List Filename", value="mylist.txt", help="Name of the temporary text file used by FFmpeg.")
    force_concat = st.checkbox("Force Concatenate", value=False, help="Bypass metadata mismatch warnings. Use with caution as it may cause glitches.")

    st.markdown("---")
    st.header("🧹 Cache Management")
    cache_size = get_dir_size(OUTPUT_CACHE_DIR)
    st.write(f"**Current Cache Size:** {format_size(cache_size)}")
    if st.button("🗑️ Clear Output Cache"):
        clear_cache()
        st.success("Cache cleared!")
        st.rerun()

    st.markdown("---")
    if st.button("🔄 Reset Application"):
        st.rerun()


input_method = st.radio("Input Method", ["Upload Files", "Local Directory Path"], horizontal=True)

if input_method == "Upload Files":
    uploaded_files = st.file_uploader("Upload MP4/MOV Videos", type=["mp4", "mov"], accept_multiple_files=True)
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
                
                # 1. Validate Consistency
                files = sorted([f for f in os.listdir(tmp_dir) if f.lower().endswith(VIDEO_EXTENSIONS)])
                if mode == "tiktok":
                    st.success("TikTok mode will normalize and re-encode the clips before combining.")
                else:
                    mismatches, base_props = validate_video_consistency(tmp_dir, files)
                    
                    if mismatches:
                        st.warning("⚠️ **Metadata Mismatches Found!**")
                        for m in mismatches:
                            st.write(f"- {m}")
                        if not force_concat:
                            st.error("Concatenation aborted due to mismatches. Enable 'Force Concatenate' in sidebar to bypass.")
                            st.stop()
                    else:
                        st.success(f"✅ All videos match: {base_props.get('width')}x{base_props.get('height')} @ {base_props.get('r_frame_rate')} FPS ({base_props.get('video_codec')})")

                # 2. Concatenate
                # Use cache directory for output to ensure it persists for playback
                timestamp = int(time.time())
                cache_output_path = os.path.join(OUTPUT_CACHE_DIR, f"{timestamp}_{output_filename}")
                
                def streamlit_progress(current, total):
                    progress = min(current / total, 1.0) if total > 0 else 0
                    progress_bar.progress(progress)
                    status_text.text(f"Processing: {current:.1f}s / {total:.1f}s")

                success = concatenate_videos_logic(
                    tmp_dir,
                    cache_output_path,
                    mode=mode,
                    list_name=list_name,
                    debug=debug,
                    progress_callback=streamlit_progress,
                    tiktok_normal_seconds=tiktok_normal_seconds,
                    tiktok_slow_factor=tiktok_slow_factor,
                    tiktok_cut_after_seconds=tiktok_cut_after_seconds
                )
                if success and os.path.exists(cache_output_path):
                    st.balloons()
                    st.success("Concatenation complete!")
                    
                    st.markdown("### 📽️ Preview Result")
                    st.video(cache_output_path)
                    
                    with open(cache_output_path, "rb") as f:
                        st.download_button("💾 Download Result", f, file_name=output_filename)
                else:
                    st.error("Concatenation failed.")
    else:
        st.warning("Please upload some MP4 files.")
else:
    local_dir = st.text_input("Local Directory Path", value=os.getcwd(), help="Absolute path to the folder containing videos.")
    output_dir = st.text_input("Output Directory", value=os.getcwd(), help="Where to save the result.")
    
    if st.button("Start Local Concatenation"):
        # 1. Validate Consistency
        files = sorted([f for f in os.listdir(local_dir) if f.lower().endswith(VIDEO_EXTENSIONS) and os.path.abspath(os.path.join(local_dir, f)) != os.path.abspath(os.path.join(output_dir, output_filename))])
        if mode == "tiktok":
            st.success("TikTok mode will normalize and re-encode the clips before combining.")
        else:
            mismatches, base_props = validate_video_consistency(local_dir, files)
            
            if mismatches:
                st.warning("⚠️ **Metadata Mismatches Found!**")
                for m in mismatches:
                    st.write(f"- {m}")
                if not force_concat:
                    st.error("Concatenation aborted due to mismatches. Enable 'Force Concatenate' in sidebar to bypass.")
                    st.stop()
            elif base_props:
                st.success(f"✅ All videos match: {base_props.get('width')}x{base_props.get('height')} @ {base_props.get('r_frame_rate')} FPS ({base_props.get('video_codec')})")

        # 2. Concatenate
        output_path = os.path.join(output_dir, output_filename)
        status_text = st.empty()
        progress_bar = st.progress(0)
        
        def streamlit_progress(current, total):
            progress = min(current / total, 1.0) if total > 0 else 0
            progress_bar.progress(progress)
            status_text.text(f"Processing: {current:.1f}s / {total:.1f}s")

        success = concatenate_videos_logic(
            local_dir,
            output_path,
            mode=mode,
            list_name=list_name,
            debug=debug,
            progress_callback=streamlit_progress,
            tiktok_normal_seconds=tiktok_normal_seconds,
            tiktok_slow_factor=tiktok_slow_factor,
            tiktok_cut_after_seconds=tiktok_cut_after_seconds
        )
        if success:
            st.success(f"Done! Saved to: {output_path}")
            st.balloons()
            
            st.markdown("### 📽️ Preview Result")
            st.video(output_path)
        else:
            st.error("Failed. Check directory paths and video formats.")


st.markdown("---")
st.markdown("### How it works")
st.markdown("""
1. **Upload:** Select all the MP4/MOV clips you want to join.
2. **Sort:** Files are automatically sorted by name.
3. **Concat:** Normal and 360 modes join matching videos without re-encoding.
4. **TikTok:** TikTok mode uses the sidebar timing and cut controls, then combines everything.
5. **Metadata:** If '360' mode is selected, ExifTool is used to inject spatial metadata from the first clip.
""")
