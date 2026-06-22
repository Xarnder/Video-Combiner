import streamlit as st
import os
import shutil
import subprocess
import tempfile
import time
from streamlit_sortables import sort_items
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
ORDER_LIST_STYLE = """
.sortable-component {
    width: 100%;
}
.sortable-component.vertical {
    gap: 0.35rem;
}
.sortable-item {
    box-sizing: border-box;
    width: 100%;
    min-height: 3rem;
    margin: 0 !important;
    border: 1px solid rgba(255, 255, 255, 0.14);
    border-radius: 6px;
    padding: 0.7rem 0.85rem;
    background: rgba(255, 255, 255, 0.04);
    cursor: grab;
    transform: none !important;
    transition: background-color 0.15s ease, border-color 0.15s ease;
}
.sortable-item:hover {
    border-color: rgba(255, 75, 75, 0.65);
    background: rgba(255, 75, 75, 0.14);
    transform: none !important;
}
.sortable-item:active {
    cursor: grabbing;
    transform: none !important;
}
"""

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

def escape_applescript_string(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')

def choose_folder_with_finder(start_path):
    if os.name != "posix" or not os.path.exists("/usr/bin/osascript"):
        return None, "Finder folder selection is only available on macOS."

    default_path = start_path if os.path.isdir(start_path) else os.path.expanduser("~")
    result = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "Finder" to activate',
            "-e",
            f'POSIX path of (choose folder default location POSIX file "{escape_applescript_string(default_path)}")'
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if result.returncode != 0:
        if "User canceled" in result.stderr:
            return None, None
        return None, result.stderr.strip() or "Could not open Finder folder picker."
    return result.stdout.strip(), None

def get_ordered_items(state_key, items):
    existing_order = st.session_state.get(state_key, [])
    ordered_items = [item for item in existing_order if item in items]
    ordered_items.extend([item for item in items if item not in ordered_items])
    st.session_state[state_key] = ordered_items
    return ordered_items

def render_order_controls(state_key, items):
    if not items:
        return []

    ordered_items = get_ordered_items(state_key, items)
    control_key = f"{state_key}_{len(ordered_items)}"

    st.markdown("### Video Order")
    if len(ordered_items) > 1:
        dragged_items = sort_items(
            ordered_items,
            direction="vertical",
            custom_style=ORDER_LIST_STYLE,
            key=f"{control_key}_drag"
        )
        if dragged_items != ordered_items:
            ordered_items = dragged_items
            st.session_state[state_key] = ordered_items
    else:
        st.markdown(f"1. `{ordered_items[0]}`")

    if len(ordered_items) > 1:
        col_a, col_b, col_c, col_d = st.columns([1, 1, 1, 1])
        first_position = col_a.number_input(
            "First position",
            min_value=1,
            max_value=len(ordered_items),
            value=1,
            step=1,
            key=f"{control_key}_first_position"
        )
        second_position = col_b.number_input(
            "Second position",
            min_value=1,
            max_value=len(ordered_items),
            value=2,
            step=1,
            key=f"{control_key}_second_position"
        )
        if col_c.button("Swap Selected", key=f"{control_key}_swap"):
            first_index = int(first_position) - 1
            second_index = int(second_position) - 1
            if first_index != second_index:
                ordered_items[first_index], ordered_items[second_index] = ordered_items[second_index], ordered_items[first_index]
                st.session_state[state_key] = ordered_items
                st.rerun()
        if col_d.button("Reverse Order", key=f"{control_key}_reverse"):
            st.session_state[state_key] = list(reversed(ordered_items))
            st.rerun()

    return ordered_items

def render_video_selection_controls(order_state_key, removed_state_key, items):
    removed_items = set(st.session_state.get(removed_state_key, []))
    available_items = [item for item in items if item not in removed_items]
    ordered_items = render_order_controls(order_state_key, available_items)

    if ordered_items:
        st.markdown("### Remove Videos")
        for index, item in enumerate(ordered_items):
            col_name, col_remove = st.columns([5, 1])
            col_name.write(f"{index + 1}. `{item}`")
            if col_remove.button("Remove", key=f"{removed_state_key}_remove_{item}"):
                removed_items.add(item)
                st.session_state[removed_state_key] = sorted(removed_items)
                st.session_state[order_state_key] = [
                    ordered_item for ordered_item in ordered_items
                    if ordered_item != item
                ]
                st.rerun()

    removed_existing_items = [item for item in items if item in removed_items]
    if removed_existing_items:
        with st.expander(f"Removed videos ({len(removed_existing_items)})"):
            for item in removed_existing_items:
                col_name, col_restore = st.columns([5, 1])
                col_name.write(f"`{item}`")
                if col_restore.button("Restore", key=f"{removed_state_key}_restore_{item}"):
                    removed_items.discard(item)
                    st.session_state[removed_state_key] = sorted(removed_items)
                    st.rerun()
            if st.button("Restore All", key=f"{removed_state_key}_restore_all"):
                st.session_state[removed_state_key] = []
                st.rerun()

    return ordered_items

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


input_method = st.radio("Input Method", ["Local Directory Path", "Upload Files"], horizontal=True)

if input_method == "Upload Files":
    uploaded_files = st.file_uploader(
        "Upload MP4/MOV Videos",
        type=["mp4", "mov"],
        accept_multiple_files=True,
        help="Uploads are configured up to 100 GB per file. For very large local footage, use Local Directory Path to avoid browser upload overhead."
    )
    if uploaded_files:
        upload_file_map = {uploaded_file.name: uploaded_file for uploaded_file in uploaded_files}
        ordered_upload_names = render_video_selection_controls(
            "uploaded_video_order",
            "uploaded_video_removed",
            list(upload_file_map.keys())
        )
        total_upload_size = sum(upload_file_map[name].size for name in ordered_upload_names)
        st.success(f"Selected {len(ordered_upload_names)} of {len(uploaded_files)} files ({format_size(total_upload_size)} total).")
        if st.button("Start Concatenation"):
            if not ordered_upload_names:
                st.error("No videos selected.")
                st.stop()
            with tempfile.TemporaryDirectory() as tmp_dir:
                status_text = st.empty()
                upload_progress_bar = st.progress(0, text="Preparing uploads...")
                processing_progress_bar = st.progress(0, text="Waiting to process...")
                saved_size = 0
                for uploaded_file_name in ordered_upload_names:
                    uploaded_file = upload_file_map[uploaded_file_name]
                    status_text.text(f"Saving upload: {uploaded_file.name}")
                    with open(os.path.join(tmp_dir, uploaded_file.name), "wb") as f:
                        f.write(uploaded_file.getbuffer())
                    saved_size += uploaded_file.size
                    upload_progress = min(saved_size / total_upload_size, 1.0) if total_upload_size > 0 else 1.0
                    upload_progress_bar.progress(
                        upload_progress,
                        text=f"Saved uploads: {format_size(saved_size)} / {format_size(total_upload_size)}"
                    )
                upload_progress_bar.progress(1.0, text="Uploads ready")
                
                # 1. Validate Consistency
                files = [name for name in ordered_upload_names if name.lower().endswith(VIDEO_EXTENSIONS)]
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
                    processing_progress_bar.progress(progress, text=f"Processing: {current:.1f}s / {total:.1f}s")
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
                    tiktok_cut_after_seconds=tiktok_cut_after_seconds,
                    ordered_files=files
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
    st.session_state.setdefault("local_dir_input", os.getcwd())
    st.session_state.setdefault("output_dir_input", os.getcwd())

    local_col, local_picker_col = st.columns([4, 1])
    if local_picker_col.button("Choose Folder", key="choose_local_dir"):
        selected_path, picker_error = choose_folder_with_finder(st.session_state.local_dir_input)
        if picker_error:
            st.error(picker_error)
        elif selected_path:
            st.session_state.local_dir_input = selected_path
    local_dir = local_col.text_input(
        "Local Directory Path",
        key="local_dir_input",
        help="Absolute path to the folder containing videos."
    )

    custom_output_dir = st.checkbox(
        "Use Custom Output Directory",
        value=False,
        help="Leave off to save the finished video in the same folder as the input videos."
    )
    if custom_output_dir:
        if st.session_state.output_dir_input == os.getcwd() and os.path.isdir(local_dir):
            st.session_state.output_dir_input = local_dir
        output_col, output_picker_col = st.columns([4, 1])
        if output_picker_col.button("Choose Folder", key="choose_output_dir"):
            selected_path, picker_error = choose_folder_with_finder(st.session_state.output_dir_input)
            if picker_error:
                st.error(picker_error)
            elif selected_path:
                st.session_state.output_dir_input = selected_path
        output_dir = output_col.text_input(
            "Output Directory",
            key="output_dir_input",
            help="Where to save the result."
        )
    else:
        output_dir = local_dir
        st.caption(f"Output directory: `{output_dir}`")

    local_files = []
    if os.path.isdir(local_dir):
        local_files = sorted([
            f for f in os.listdir(local_dir)
            if f.lower().endswith(VIDEO_EXTENSIONS)
            and os.path.abspath(os.path.join(local_dir, f)) != os.path.abspath(os.path.join(output_dir, output_filename))
        ])
        local_selection_key = f"local_video_selection_{local_dir}_{output_dir}_{output_filename}"
        ordered_local_files = render_video_selection_controls(
            f"{local_selection_key}_order",
            f"{local_selection_key}_removed",
            local_files
        )
        st.success(f"Selected {len(ordered_local_files)} of {len(local_files)} videos.")
    else:
        ordered_local_files = []
        st.warning("Enter a valid local directory to preview and order videos.")
    
    if st.button("Start Local Concatenation"):
        if not os.path.isdir(local_dir):
            st.error("Local directory does not exist.")
            st.stop()
        # 1. Validate Consistency
        files = ordered_local_files
        if not files:
            st.error("No MP4/MOV videos found in the selected local directory.")
            st.stop()
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
        progress_bar = st.progress(0, text="Waiting to process...")
        
        def streamlit_progress(current, total):
            progress = min(current / total, 1.0) if total > 0 else 0
            progress_bar.progress(progress, text=f"Processing: {current:.1f}s / {total:.1f}s")
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
            tiktok_cut_after_seconds=tiktok_cut_after_seconds,
            ordered_files=files
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
2. **Order:** Review the clip order and swap positions before processing.
3. **Concat:** Normal and 360 modes join matching videos without re-encoding.
4. **TikTok:** TikTok mode uses the sidebar timing and cut controls, then combines everything.
5. **Metadata:** If '360' mode is selected, ExifTool is used to inject spatial metadata from the first clip.
""")
