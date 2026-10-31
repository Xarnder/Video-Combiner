import html
import sys
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
    find_side_by_side_pairs,
    normalize_processing_result,
    side_by_side_output_path,
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

def format_duration(seconds):
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "—"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"

def estimate_remaining_seconds(current, total, elapsed):
    if total <= 0 or current <= 0 or elapsed <= 0:
        return None
    if current >= total:
        return 0
    rate = current / elapsed
    if rate <= 0:
        return None
    return (total - current) / rate

class ProcessingIndicator:
    """Large animated progress panel with elapsed time, ETA, and activity messages."""

    MAX_MESSAGES = 10

    def __init__(self):
        self.message_container = st.empty()
        self.container = st.empty()
        self.start_time = time.time()
        self.messages = []
        self.secondary_active = False
        self.secondary_title = ""
        self.secondary_detail = ""
        self.secondary_progress = None
        self._primary_state = {
            "title": "Preparing…",
            "detail": "Getting ready to process your videos",
            "progress": 0.02,
            "progress_label": "0%",
            "elapsed_label": "Elapsed: 0s",
            "eta_label": "Est. remaining: calculating…",
            "show_spinner": True,
        }
        self.add_message("Initializing processing…")
        self._render_panels()

    def add_message(self, message):
        self.messages.append(message)
        if len(self.messages) > self.MAX_MESSAGES:
            self.messages = self.messages[-self.MAX_MESSAGES:]
        self._render_messages()

    def _render_messages(self):
        current_message = self.messages[-1] if self.messages else "Working…"
        history_messages = self.messages[:-1]
        history_html = "".join(
            f'<div class="vc-message-item">{html.escape(item)}</div>'
            for item in reversed(history_messages)
        )
        history_block = (
            f'<div class="vc-message-history">{history_html}</div>'
            if history_html
            else ""
        )
        message_html = f"""
        <div class="vc-message-panel">
            <div class="vc-message-label">Activity</div>
            <div class="vc-message-current">{html.escape(current_message)}</div>
            {history_block}
        </div>
        """
        self.message_container.markdown(message_html, unsafe_allow_html=True)

    def _render(
        self,
        title,
        detail,
        progress,
        progress_label,
        elapsed_label,
        eta_label,
        show_spinner=True,
    ):
        self._primary_state = {
            "title": title,
            "detail": detail,
            "progress": progress,
            "progress_label": progress_label,
            "elapsed_label": elapsed_label,
            "eta_label": eta_label,
            "show_spinner": show_spinner,
        }
        self._render_panels()

    def _secondary_html(self):
        progress = self.secondary_progress
        if progress is None:
            fill_class = "vc-progress-fill indeterminate"
            fill_style = ""
            progress_label = "Working…"
            eta_label = "This step has no time estimate"
        else:
            fill_class = "vc-progress-fill"
            fill_style = f'style="width: {max(0.02, min(progress, 1.0)) * 100:.1f}%;"'
            progress_label = f"{int(max(0.0, min(progress, 1.0)) * 100)}% complete"
            eta_label = "Step complete" if progress >= 1.0 else "Finishing up…"
        return f"""
        <div class="vc-secondary-panel">
            <div class="vc-secondary-header">
                <span class="vc-spinner vc-spinner-small" aria-hidden="true"></span>
                <span class="vc-secondary-title">{html.escape(self.secondary_title)}</span>
            </div>
            <p class="vc-secondary-detail">{html.escape(self.secondary_detail)}</p>
            <div class="vc-progress-track vc-progress-track-secondary" role="progressbar">
                <div class="{fill_class}" {fill_style}></div>
            </div>
            <div class="vc-progress-meta vc-secondary-meta">
                <span class="vc-progress-percent">{html.escape(progress_label)}</span>
                <span class="vc-progress-eta">{html.escape(eta_label)}</span>
            </div>
        </div>
        """

    def _render_panels(self):
        state = self._primary_state
        progress = max(0.02, min(state["progress"], 1.0))
        spinner_html = (
            '<span class="vc-spinner" aria-hidden="true"></span>'
            if state["show_spinner"]
            else ""
        )
        secondary_html = self._secondary_html() if self.secondary_active else ""
        panel_html = f"""
        <div class="vc-processing-panel active">
            <div class="vc-processing-header">
                {spinner_html}
                <span class="vc-processing-title">{html.escape(state["title"])}</span>
            </div>
            <p class="vc-processing-detail">{html.escape(state["detail"])}</p>
            <div class="vc-progress-track" role="progressbar" aria-valuenow="{int(progress * 100)}" aria-valuemin="0" aria-valuemax="100">
                <div class="vc-progress-fill" style="width: {progress * 100:.1f}%;"></div>
            </div>
            <div class="vc-progress-meta">
                <span class="vc-progress-percent">{html.escape(state["progress_label"])}</span>
                <span class="vc-progress-elapsed">{html.escape(state["elapsed_label"])}</span>
                <span class="vc-progress-eta">{html.escape(state["eta_label"])}</span>
            </div>
            {secondary_html}
        </div>
        """
        self.container.markdown(panel_html, unsafe_allow_html=True)

    def update_phase(self, phase_id, active, progress=None, message=""):
        phase_titles = {
            "metadata": "360° metadata injection",
            "merge": "Final clip merge",
            "sidebyside": "Side-by-side stacking",
        }
        if active:
            self.secondary_active = True
            self.secondary_title = phase_titles.get(phase_id, "Additional processing")
            if message:
                self.secondary_detail = message
            self.secondary_progress = progress
            if message:
                self.add_message(message)
            if self._primary_state.get("progress", 0) >= 0.999:
                self._primary_state["eta_label"] = "Video concat complete"
                self._primary_state["detail"] = "Concatenated all footage — finishing in the step below"
        else:
            self.secondary_active = False
            self.secondary_progress = None
        self._render_panels()

    def _elapsed(self):
        return time.time() - self.start_time

    def update_upload(self, saved_size, total_size, current_filename=None, message=None):
        progress = min(saved_size / total_size, 1.0) if total_size > 0 else 1.0
        elapsed = self._elapsed()
        remaining = estimate_remaining_seconds(saved_size, total_size, elapsed)
        detail = f"Saving {current_filename}" if current_filename else "Saving uploaded files to temporary storage"
        eta_label = (
            "Est. remaining: almost done"
            if progress >= 1.0
            else f"Est. remaining: {format_duration(remaining)}"
            if remaining is not None
            else "Est. remaining: calculating…"
        )
        self._render(
            title="Saving uploads",
            detail=detail,
            progress=progress,
            progress_label=f"{int(progress * 100)}% · {format_size(saved_size)} / {format_size(total_size)}",
            elapsed_label=f"Elapsed: {format_duration(elapsed)}",
            eta_label=eta_label,
        )
        if message:
            self.add_message(message)

    def update_processing(self, current, total, phase_label="Processing videos"):
        progress = min(current / total, 1.0) if total > 0 else 0
        elapsed = self._elapsed()
        remaining = estimate_remaining_seconds(current, total, elapsed)
        if progress >= 1.0:
            if self.secondary_active:
                eta_label = "Video concat complete"
                detail = f"Concatenated {total:.1f}s of footage — see step below"
            else:
                eta_label = "Est. remaining: complete"
                detail = f"Concatenated {total:.1f}s of footage"
        elif remaining is not None:
            eta_label = f"Est. remaining: ~{format_duration(remaining)}"
            detail = f"Encoded {current:.1f}s of {total:.1f}s total footage"
        else:
            eta_label = "Est. remaining: calculating…"
            detail = "Starting FFmpeg — this may take a moment on large files"
        self._render(
            title=phase_label,
            detail=detail,
            progress=progress,
            progress_label=f"{int(progress * 100)}% · {current:.1f}s / {total:.1f}s",
            elapsed_label=f"Elapsed: {format_duration(elapsed)}",
            eta_label=eta_label,
        )

    def complete(self, message="Processing complete", detail="Your video is ready"):
        self.secondary_active = False
        self.secondary_progress = None
        elapsed = self._elapsed()
        self.add_message(message)
        self._render(
            title=message,
            detail=detail,
            progress=1.0,
            progress_label="100% complete",
            elapsed_label=f"Total time: {format_duration(elapsed)}",
            eta_label="Done",
            show_spinner=False,
        )

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

def open_folder_in_file_manager(folder_path):
    folder_path = os.path.abspath(folder_path)
    if not os.path.isdir(folder_path):
        return False, f"Folder not found: {folder_path}"
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", folder_path], check=True)
        elif os.name == "nt":
            subprocess.run(["explorer", os.path.normpath(folder_path)], check=True)
        else:
            subprocess.run(["xdg-open", folder_path], check=True)
        return True, None
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return False, f"Could not open folder: {exc}"

def render_open_output_folder_button(folder_path, key):
    if st.button("📂 Open Output Folder", key=key, use_container_width=True):
        opened, error = open_folder_in_file_manager(folder_path)
        if opened:
            st.toast(f"Opened {folder_path}")
        else:
            st.error(error)

def choose_folder_with_finder(start_path):
    if os.name == "nt":
        picker_code = (
            "import sys, tkinter as tk; from tkinter import filedialog; "
            "root=tk.Tk(); root.withdraw(); root.attributes('-topmost', True); "
            "print(filedialog.askdirectory(initialdir=sys.argv[1])); root.destroy()"
        )
        result = subprocess.run([sys.executable, "-c", picker_code, start_path],
                                capture_output=True, text=True)
        if result.returncode:
            return None, "Could not open folder picker. Enter the directory path instead."
        return result.stdout.strip() or None, None
    if os.name != "posix" or not os.path.exists("/usr/bin/osascript"):
        return None, "Enter a local directory path on this platform."

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

def choose_video_file_with_finder(start_path):
    """Opens a macOS file picker limited to common video types."""
    if os.name != "posix" or not os.path.exists("/usr/bin/osascript"):
        return None, "Finder file selection is only available on macOS."

    if os.path.isfile(start_path):
        default_path = os.path.dirname(start_path) or os.path.expanduser("~")
    elif os.path.isdir(start_path):
        default_path = start_path
    else:
        default_path = os.path.expanduser("~")

    result = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "Finder" to activate',
            "-e",
            (
                'POSIX path of (choose file of type {"public.mpeg-4", "com.apple.quicktime-movie", "mp4", "mov"} '
                f'default location POSIX file "{escape_applescript_string(default_path)}")'
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    if result.returncode != 0:
        if "User canceled" in result.stderr:
            return None, None
        return None, result.stderr.strip() or "Could not open Finder file picker."
    return result.stdout.strip(), None

def is_supported_video_path(file_path):
    """Returns True when the path looks like a supported local video file."""
    return bool(file_path) and os.path.isfile(file_path) and file_path.lower().endswith(VIDEO_EXTENSIONS)

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

def list_videos_in_directory(directory, exclude_output_path=None):
    """Returns sorted video filenames in a directory, optionally excluding an output path."""
    if not os.path.isdir(directory):
        return []
    exclude_abs = os.path.abspath(exclude_output_path) if exclude_output_path else None
    return sorted([
        filename for filename in os.listdir(directory)
        if filename.lower().endswith(VIDEO_EXTENSIONS)
        and (exclude_abs is None or os.path.abspath(os.path.join(directory, filename)) != exclude_abs)
    ])

def render_folder_path_picker(label, state_key, button_key, help_text=None):
    """Renders a labeled local directory path field with a Finder picker button."""
    st.session_state.setdefault(state_key, os.getcwd())
    path_col, picker_col = st.columns([4, 1])
    if picker_col.button("Choose Folder", key=button_key):
        selected_path, picker_error = choose_folder_with_finder(st.session_state[state_key])
        if picker_error:
            st.error(picker_error)
        elif selected_path:
            st.session_state[state_key] = selected_path
            st.rerun()
    return path_col.text_input(label, key=state_key, help=help_text)

def render_video_file_path_picker(label, state_key, button_key, help_text=None):
    """Renders a labeled video file path field with a Finder file picker button."""
    st.session_state.setdefault(state_key, "")
    path_col, picker_col = st.columns([4, 1])
    if picker_col.button("Choose Video", key=button_key):
        start_path = st.session_state.get(state_key) or os.getcwd()
        selected_path, picker_error = choose_video_file_with_finder(start_path)
        if picker_error:
            st.error(picker_error)
        elif selected_path:
            st.session_state[state_key] = selected_path
            st.rerun()
    return path_col.text_input(label, key=state_key, help=help_text)

def sidebyside_position_labels(layout):
    """Returns (primary_label, secondary_label, relation_phrase) for the current layout."""
    if layout == "horizontal":
        return "Left clip", "Right clip", "left of"
    return "Top clip", "Bottom clip", "above"

def _sidebyside_frame_html(label, title, hint=None, empty=False):
    """Builds one 16:9 placeholder frame for the layout guide or empty preview slot."""
    hint_html = f'<div class="vc-sbs-frame-hint">{html.escape(hint)}</div>' if hint else ""
    empty_class = " empty" if empty else ""
    return (
        f'<div class="vc-sbs-frame{empty_class}">'
        f'<div class="vc-sbs-frame-label">{html.escape(label)}</div>'
        f'<div class="vc-sbs-frame-title">{html.escape(title)}</div>'
        f'{hint_html}'
        f'</div>'
    )

def render_sidebyside_layout_guide(layout):
    """Shows a simple visual of how the two clips will be stacked using 16:9 frames."""
    if layout == "horizontal":
        guide_html = (
            '<div class="vc-sbs-guide-horizontal">'
            f'{_sidebyside_frame_html("Left", "Clip A", "16:9")}'
            f'{_sidebyside_frame_html("Right", "Clip B", "16:9")}'
            '</div>'
        )
    else:
        guide_html = (
            '<div class="vc-sbs-guide-vertical">'
            f'{_sidebyside_frame_html("Top", "Clip A", "16:9")}'
            f'{_sidebyside_frame_html("Bottom", "Clip B", "16:9")}'
            '</div>'
        )
    st.markdown(guide_html, unsafe_allow_html=True)

def render_sidebyside_preview_slot(label, source):
    """Renders one preview slot: either a video or an empty 16:9 placeholder."""
    st.caption(label)
    if source is None:
        st.markdown(
            _sidebyside_frame_html(label, "Waiting for clip…", "16:9 placeholder", empty=True),
            unsafe_allow_html=True,
        )
    else:
        st.video(source)

def render_sidebyside_composition_preview(layout, primary_source=None, secondary_source=None, primary_name=None, secondary_name=None, show_heading=True):
    """Shows a live preview of the side-by-side composition as clips are selected."""
    primary_label, secondary_label, relation = sidebyside_position_labels(layout)
    has_any = primary_source is not None or secondary_source is not None
    if not has_any:
        return

    if show_heading:
        st.markdown("### Side-by-side preview")
    if primary_source is not None and secondary_source is not None:
        left_name = primary_name or "Clip A"
        right_name = secondary_name or "Clip B"
        st.caption(f"`{left_name}` will appear {relation} `{right_name}`.")
    else:
        st.caption("Add the second clip to complete the composition preview.")

    if layout == "horizontal":
        col_a, col_b = st.columns(2)
        with col_a:
            render_sidebyside_preview_slot(primary_label, primary_source)
        with col_b:
            render_sidebyside_preview_slot(secondary_label, secondary_source)
    else:
        render_sidebyside_preview_slot(primary_label, primary_source)
        render_sidebyside_preview_slot(secondary_label, secondary_source)

def render_sidebyside_clip_source_panel(position_label, path_state_key, choose_button_key):
    """Renders one clearly labeled single-video path picker for a side-by-side slot."""
    st.markdown(f"#### {position_label}")
    video_path = render_video_file_path_picker(
        "Video File Path",
        path_state_key,
        choose_button_key,
        help_text=f"Full path to the {position_label.lower()} MP4/MOV file.",
    ).strip()

    if not video_path:
        st.caption("Choose a single video file for this position.")
        return None
    if not os.path.exists(video_path):
        st.warning("That path does not exist.")
        return None
    if os.path.isdir(video_path):
        st.warning("Pick a video file, not a folder.")
        return None
    if not is_supported_video_path(video_path):
        st.warning("Choose an MP4 or MOV video file.")
        return None

    st.caption(f"Selected `{os.path.basename(video_path)}`")
    return os.path.abspath(video_path)

def render_sidebyside_position_controls(source_kind):
    """
    Renders Swap Positions / Reset controls for single side-by-side mode.
    source_kind: 'local' or 'upload'
    Stays in side-by-side mode; only clears or swaps the current clip selections.
    """
    st.session_state.setdefault("sidebyside_positions_swapped", False)
    st.session_state.setdefault("sidebyside_upload_gen", 0)

    swap_col, reset_col, _ = st.columns([1, 1, 2])
    with swap_col:
        if st.button("Swap Positions", key=f"sidebyside_swap_{source_kind}", use_container_width=True):
            if source_kind == "local":
                primary = st.session_state.get("sidebyside_primary_video_path", "")
                secondary = st.session_state.get("sidebyside_secondary_video_path", "")
                st.session_state["sidebyside_primary_video_path"] = secondary
                st.session_state["sidebyside_secondary_video_path"] = primary
            else:
                st.session_state["sidebyside_positions_swapped"] = not st.session_state.get(
                    "sidebyside_positions_swapped", False
                )
            st.rerun()
    with reset_col:
        if st.button("Reset Clips", key=f"sidebyside_reset_{source_kind}", use_container_width=True):
            if source_kind == "local":
                st.session_state["sidebyside_primary_video_path"] = ""
                st.session_state["sidebyside_secondary_video_path"] = ""
            else:
                st.session_state["sidebyside_upload_gen"] = st.session_state.get("sidebyside_upload_gen", 0) + 1
            st.session_state["sidebyside_positions_swapped"] = False
            st.rerun()

def render_sidebyside_single_local_sources(layout):
    """Two labeled video-file pickers for single side-by-side mode."""
    primary_label, secondary_label, _relation = sidebyside_position_labels(layout)
    st.markdown("### Choose the two clips")
    st.caption("Pick one video file for each position (top/bottom or left/right).")
    render_sidebyside_position_controls("local")

    col_a, col_b = st.columns(2)
    with col_a:
        primary_path = render_sidebyside_clip_source_panel(
            primary_label,
            "sidebyside_primary_video_path",
            "choose_sidebyside_primary_video",
        )
    with col_b:
        secondary_path = render_sidebyside_clip_source_panel(
            secondary_label,
            "sidebyside_secondary_video_path",
            "choose_sidebyside_secondary_video",
        )

    st.markdown("### Side-by-side preview")
    render_sidebyside_layout_guide(layout)
    render_sidebyside_composition_preview(
        layout,
        primary_source=primary_path,
        secondary_source=secondary_path,
        primary_name=os.path.basename(primary_path) if primary_path else None,
        secondary_name=os.path.basename(secondary_path) if secondary_path else None,
        show_heading=False,
    )
    return primary_path, secondary_path

def render_sidebyside_single_upload_sources(layout):
    """Two clearly labeled uploaders for single side-by-side mode."""
    primary_label, secondary_label, _relation = sidebyside_position_labels(layout)
    st.markdown("### Upload the two clips")
    st.caption("Upload one video for each position in the stacked output.")
    render_sidebyside_position_controls("upload")

    swapped = st.session_state.get("sidebyside_positions_swapped", False)
    upload_gen = st.session_state.get("sidebyside_upload_gen", 0)
    key_a = f"sidebyside_upload_a_{upload_gen}"
    key_b = f"sidebyside_upload_b_{upload_gen}"
    # Keep labels fixed; move uploader keys between slots on swap so files follow.
    primary_key = key_b if swapped else key_a
    secondary_key = key_a if swapped else key_b

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown(f"#### {primary_label}")
        primary_upload = st.file_uploader(
            f"Upload {primary_label}",
            type=["mp4", "mov"],
            accept_multiple_files=False,
            key=primary_key,
            help=f"This becomes the {primary_label.lower()}."
        )
    with col_b:
        st.markdown(f"#### {secondary_label}")
        secondary_upload = st.file_uploader(
            f"Upload {secondary_label}",
            type=["mp4", "mov"],
            accept_multiple_files=False,
            key=secondary_key,
            help=f"This becomes the {secondary_label.lower()}."
        )

    st.markdown("### Side-by-side preview")
    render_sidebyside_layout_guide(layout)
    render_sidebyside_composition_preview(
        layout,
        primary_source=primary_upload,
        secondary_source=secondary_upload,
        primary_name=primary_upload.name if primary_upload else None,
        secondary_name=secondary_upload.name if secondary_upload else None,
        show_heading=False,
    )
    return primary_upload, secondary_upload

def render_sticky_start_button(label, can_start, disabled_hint):
    """Renders the always-visible start control with enabled/disabled state."""
    with st.container(border=True):
        clicked = st.button(
            label,
            disabled=not can_start,
            type="primary",
            key="vc_start_process",
            use_container_width=True,
        )
        if can_start:
            st.caption("Ready to process.")
        else:
            st.caption(disabled_hint)
    return clicked and can_start

def make_progress_callbacks(indicator, last_progress_message_at):
    """Builds shared Streamlit progress/status/phase callbacks."""
    def streamlit_progress(current, total):
        indicator.update_processing(current, total)
        now = time.time()
        if now - last_progress_message_at[0] >= 2.5 and total > 0 and current < total:
            last_progress_message_at[0] = now
            indicator.add_message(f"Encoding progress: {current:.1f}s of {total:.1f}s")

    def streamlit_status(message):
        indicator.add_message(message)

    def streamlit_phase(phase_id, active, progress=None, message=""):
        indicator.update_phase(phase_id, active, progress, message)

    return streamlit_progress, streamlit_status, streamlit_phase

def link_or_copy_file(source_path, destination_path):
    """Prefer a symlink for speed; fall back to copy if linking fails."""
    if os.path.lexists(destination_path):
        os.remove(destination_path)
    try:
        os.symlink(os.path.abspath(source_path), destination_path)
    except OSError:
        shutil.copy2(source_path, destination_path)

def stage_sidebyside_pair(primary_abs, secondary_abs, staging_dir):
    """Places the two selected clips into one working directory for FFmpeg."""
    os.makedirs(staging_dir, exist_ok=True)
    primary_name = f"primary_{os.path.basename(primary_abs)}"
    secondary_name = f"secondary_{os.path.basename(secondary_abs)}"
    link_or_copy_file(primary_abs, os.path.join(staging_dir, primary_name))
    link_or_copy_file(secondary_abs, os.path.join(staging_dir, secondary_name))
    return primary_name, secondary_name

def render_sidebyside_batch_preview(files):
    """Shows automatically detected A/B-style pairs for batch mode."""
    pairs = find_side_by_side_pairs(files)
    if not pairs:
        st.warning(
            "No pairs found. Name clips like `shot_A.mp4` / `shot_B.mp4` "
            "(also supports top/bottom, left/right, and `_1`/`_2`)."
        )
        return pairs

    st.success(f"Detected {len(pairs)} pair{'s' if len(pairs) != 1 else ''}.")
    for pair in pairs:
        st.write(f"- `{pair['base']}` → `{pair['primary']}` + `{pair['secondary']}` → `{pair['base']}_sidebyside.mp4`")
    return pairs

def run_sidebyside_result_ui(output_paths, output_dir, open_folder_key):
    """Renders preview/download controls for one or more side-by-side outputs."""
    existing_paths = [path for path in output_paths if os.path.exists(path)]
    if not existing_paths:
        st.error("Processing reported success, but no output files were found.")
        return

    st.markdown("### 📽️ Preview Result")
    if len(existing_paths) == 1:
        st.video(existing_paths[0])
        download_col, folder_col = st.columns(2)
        with download_col:
            with open(existing_paths[0], "rb") as f:
                st.download_button(
                    "💾 Download Result",
                    f,
                    file_name=os.path.basename(existing_paths[0]),
                    use_container_width=True,
                )
        with folder_col:
            render_open_output_folder_button(output_dir, key=open_folder_key)
        return

    st.caption(f"Created {len(existing_paths)} side-by-side clips.")
    for path in existing_paths:
        st.markdown(f"**{os.path.basename(path)}**")
        st.video(path)
        with open(path, "rb") as f:
            st.download_button(
                f"💾 Download {os.path.basename(path)}",
                f,
                file_name=os.path.basename(path),
                key=f"download_{os.path.basename(path)}_{open_folder_key}",
                use_container_width=True,
            )
    render_open_output_folder_button(output_dir, key=open_folder_key)

def build_sidebyside_output_paths(output_file, files, submode, top_file=None, bottom_file=None):
    """Resolves the expected output path(s) for a side-by-side run."""
    if submode == "single":
        return [output_file]
    pairs = find_side_by_side_pairs(files)
    return [
        side_by_side_output_path(output_file, base_name=pair["base"], batch=True)
        for pair in pairs
    ]

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
    .stButton>button:disabled,
    .stButton>button[disabled] {
        background-color: #3a3a3a !important;
        color: #8f8f8f !important;
        border: 1px solid #555 !important;
        opacity: 0.75;
        cursor: not-allowed;
    }
    div[data-testid="stVerticalBlockBorderWrapper"]:has([data-testid="stBaseButton-primary"]) {
        position: sticky;
        top: 3.5rem;
        z-index: 1000;
        background: rgba(14, 17, 23, 0.96);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 75, 75, 0.45) !important;
        box-shadow: 0 10px 28px rgba(0, 0, 0, 0.35);
    }
    .vc-start-hint {
        color: rgba(250, 250, 250, 0.62);
        font-size: 0.9rem;
        margin-top: 0.15rem;
    }
    .stProgress > div > div > div > div {
        background-color: #ff4b4b;
    }
    .vc-processing-panel {
        background: linear-gradient(145deg, rgba(255, 75, 75, 0.12), rgba(255, 255, 255, 0.03));
        border: 1px solid rgba(255, 75, 75, 0.45);
        border-radius: 14px;
        padding: 1.35rem 1.5rem 1.2rem;
        margin: 0.75rem 0 1.25rem;
        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.28);
    }
    .vc-processing-panel.active {
        animation: vc-panel-pulse 2.4s ease-in-out infinite;
    }
    @keyframes vc-panel-pulse {
        0%, 100% { border-color: rgba(255, 75, 75, 0.45); box-shadow: 0 10px 30px rgba(0, 0, 0, 0.28); }
        50% { border-color: rgba(255, 120, 120, 0.85); box-shadow: 0 10px 34px rgba(255, 75, 75, 0.18); }
    }
    .vc-processing-header {
        display: flex;
        align-items: center;
        gap: 0.55rem;
        margin-bottom: 0.35rem;
    }
    .vc-processing-title {
        font-size: 1.2rem;
        font-weight: 700;
        color: #fafafa;
        letter-spacing: 0.01em;
    }
    .vc-processing-detail {
        margin: 0 0 1rem 0;
        color: rgba(250, 250, 250, 0.72);
        font-size: 0.95rem;
    }
    .vc-progress-track {
        height: 32px;
        background: rgba(0, 0, 0, 0.35);
        border-radius: 16px;
        overflow: hidden;
        position: relative;
        border: 1px solid rgba(255, 255, 255, 0.08);
        box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.45);
    }
    .vc-progress-fill {
        height: 100%;
        border-radius: 16px;
        background: linear-gradient(90deg, #d93636 0%, #ff4b4b 45%, #ff8a8a 100%);
        background-size: 200% 100%;
        animation: vc-bar-glow 1.6s ease-in-out infinite;
        transition: width 0.45s ease;
        position: relative;
        overflow: hidden;
        min-width: 4%;
    }
    .vc-progress-fill::after {
        content: "";
        position: absolute;
        inset: 0;
        background: linear-gradient(
            110deg,
            transparent 0%,
            rgba(255, 255, 255, 0.05) 35%,
            rgba(255, 255, 255, 0.45) 50%,
            rgba(255, 255, 255, 0.05) 65%,
            transparent 100%
        );
        transform: translateX(-120%);
        animation: vc-shimmer 1.5s linear infinite;
    }
    @keyframes vc-shimmer {
        0% { transform: translateX(-120%); }
        100% { transform: translateX(220%); }
    }
    @keyframes vc-bar-glow {
        0%, 100% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
    }
    .vc-progress-meta {
        display: flex;
        flex-wrap: wrap;
        justify-content: space-between;
        gap: 0.5rem 1rem;
        margin-top: 0.85rem;
        font-size: 0.92rem;
        color: rgba(250, 250, 250, 0.88);
    }
    .vc-progress-percent {
        font-weight: 700;
        color: #ff8a8a;
        font-size: 1rem;
    }
    .vc-progress-eta {
        font-weight: 600;
        color: #ffd0d0;
    }
    .vc-spinner {
        display: inline-block;
        width: 20px;
        height: 20px;
        border: 3px solid rgba(255, 75, 75, 0.25);
        border-top-color: #ff4b4b;
        border-radius: 50%;
        animation: vc-spin 0.85s linear infinite;
        flex-shrink: 0;
    }
    @keyframes vc-spin {
        to { transform: rotate(360deg); }
    }
    .vc-message-panel {
        margin: 0 0 1rem 0;
        padding: 0.95rem 1.1rem;
        border-radius: 10px;
        background: rgba(0, 0, 0, 0.28);
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .vc-message-label {
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: rgba(255, 138, 138, 0.95);
        margin-bottom: 0.45rem;
    }
    .vc-message-current {
        font-size: 1.02rem;
        font-weight: 600;
        color: #ffffff;
        line-height: 1.45;
        animation: vc-message-fade 0.35s ease;
    }
    @keyframes vc-message-fade {
        from { opacity: 0.45; transform: translateY(4px); }
        to { opacity: 1; transform: translateY(0); }
    }
    .vc-message-history {
        margin-top: 0.7rem;
        padding-top: 0.65rem;
        border-top: 1px solid rgba(255, 255, 255, 0.08);
        max-height: 110px;
        overflow-y: auto;
    }
    .vc-message-item {
        font-size: 0.84rem;
        color: rgba(250, 250, 250, 0.58);
        line-height: 1.4;
        padding: 0.12rem 0;
    }
    .vc-secondary-panel {
        margin-top: 1.15rem;
        padding-top: 1rem;
        border-top: 1px dashed rgba(255, 138, 138, 0.45);
    }
    .vc-secondary-header {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        margin-bottom: 0.3rem;
    }
    .vc-secondary-title {
        font-size: 1rem;
        font-weight: 700;
        color: #ffd8d8;
    }
    .vc-secondary-detail {
        margin: 0 0 0.75rem 0;
        color: rgba(250, 250, 250, 0.7);
        font-size: 0.9rem;
    }
    .vc-progress-track-secondary {
        height: 22px;
        border-radius: 11px;
    }
    .vc-progress-fill.indeterminate {
        width: 38% !important;
        position: relative;
        animation: vc-indeterminate-slide 1.35s ease-in-out infinite;
    }
    @keyframes vc-indeterminate-slide {
        0% { transform: translateX(-10%); }
        50% { transform: translateX(170%); }
        100% { transform: translateX(-10%); }
    }
    .vc-spinner-small {
        width: 16px;
        height: 16px;
        border-width: 2px;
    }
    .vc-secondary-meta {
        margin-top: 0.55rem;
    }
    .vc-sbs-guide-vertical {
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
        max-width: 480px;
        margin: 0.35rem 0 1rem;
    }
    .vc-sbs-guide-horizontal {
        display: flex;
        flex-direction: row;
        gap: 0.75rem;
        max-width: 760px;
        margin: 0.35rem 0 1rem;
    }
    .vc-sbs-guide-horizontal > .vc-sbs-frame {
        flex: 1;
        min-width: 0;
    }
    .vc-sbs-frame {
        aspect-ratio: 16 / 9;
        width: 100%;
        border: 1px solid rgba(255, 75, 75, 0.55);
        border-radius: 10px;
        background:
            linear-gradient(145deg, rgba(255, 75, 75, 0.14), rgba(255, 255, 255, 0.03)),
            repeating-linear-gradient(
                -45deg,
                rgba(255, 255, 255, 0.03),
                rgba(255, 255, 255, 0.03) 8px,
                transparent 8px,
                transparent 16px
            );
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        box-sizing: border-box;
        overflow: hidden;
    }
    .vc-sbs-frame.empty {
        border-style: dashed;
        border-color: rgba(255, 255, 255, 0.22);
        background: rgba(0, 0, 0, 0.28);
    }
    .vc-sbs-frame-label {
        font-size: 0.72rem;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: rgba(255, 208, 208, 0.9);
        margin-bottom: 0.35rem;
    }
    .vc-sbs-frame-title {
        font-weight: 700;
        color: #fff;
        font-size: 1rem;
    }
    .vc-sbs-frame-hint {
        margin-top: 0.35rem;
        font-size: 0.82rem;
        color: rgba(250, 250, 250, 0.55);
    }
    .vc-sbs-preview-slot {
        margin-bottom: 0.85rem;
    }
    .vc-sbs-preview-slot video {
        width: 100%;
        border-radius: 10px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        background: #000;
        aspect-ratio: 16 / 9;
        object-fit: contain;
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
st.markdown("Concatenate multiple MP4/MOV videos, preserve 360/VR metadata, create a TikTok-style slow-motion edit, or stack matching clips side by side.")

with st.sidebar:
    st.header("Settings")
    mode = st.radio(
        "Processing Mode",
        ["360", "normal", "tiktok", "sidebyside", "comparison"],
        format_func=lambda value: "Comparison Contact Sheets" if value == "comparison" else value,
        index=1,
        help="Side-by-side stacks paired clips vertically or horizontally. TikTok mode plays the first 0.5s of every clip normally, then slows the rest to 0.4x."
    )
    debug = st.checkbox("Enable Debug Logging", value=False)
    tiktok_normal_seconds = TIKTOK_NORMAL_SECONDS
    tiktok_slow_factor = TIKTOK_SLOW_FACTOR
    tiktok_cut_after_seconds = TIKTOK_CUT_AFTER_SECONDS
    sidebyside_submode = "single"
    sidebyside_layout = "vertical"
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
    if mode == "sidebyside":
        sidebyside_submode = st.radio(
            "Side-by-Side Submode",
            ["single", "batch"],
            format_func=lambda value: "Single" if value == "single" else "Batch",
            help="Single: pick two clips. Batch: auto-pair files named like clip_A / clip_B."
        )
        sidebyside_layout = st.radio(
            "Stack Layout",
            ["vertical", "horizontal"],
            format_func=lambda value: "Vertical (top / bottom)" if value == "vertical" else "Horizontal (left / right)",
            help="Vertical doubles height. Horizontal doubles width."
        )
    if mode != "comparison":
        output_filename = st.text_input(
            "Output Filename",
            value="concatenated_video.mp4" if mode != "sidebyside" else ("sidebyside_clip.mp4" if sidebyside_submode == "single" else "sidebyside_batch.mp4"),
            help="In batch side-by-side mode this filename only sets the output folder; each pair is saved as <name>_sidebyside.mp4."
        )
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


if mode == "comparison":
    from comparison_ui import render_comparison_mode
    render_comparison_mode(render_folder_path_picker, ProcessingIndicator, render_open_output_folder_button)
    st.stop()

input_method = st.radio("Input Method", ["Local Directory Path", "Upload Files"], horizontal=True)
start_bar = st.empty()
can_start = False
start_label = "Start Processing"
disabled_hint = "Select the required videos to enable start."
run_action = None

if input_method == "Upload Files":
    sidebyside_top = None
    sidebyside_bottom = None
    primary_upload = None
    secondary_upload = None
    uploaded_files = None
    upload_file_map = {}
    ordered_upload_names = []
    total_upload_size = 0

    if mode == "sidebyside" and sidebyside_submode == "single":
        primary_upload, secondary_upload = render_sidebyside_single_upload_sources(sidebyside_layout)
        start_label = "Start Side-by-Side"
        can_start = primary_upload is not None and secondary_upload is not None
        disabled_hint = "Upload both clips to enable Start Side-by-Side."
        run_action = "upload_sbs_single"
    else:
        uploaded_files = st.file_uploader(
            "Upload MP4/MOV Videos",
            type=["mp4", "mov"],
            accept_multiple_files=True,
            help="Uploads are configured up to 100 GB per file. For very large local footage, use Local Directory Path to avoid browser upload overhead."
        )
        if uploaded_files:
            upload_file_map = {uploaded_file.name: uploaded_file for uploaded_file in uploaded_files}
            upload_names = list(upload_file_map.keys())

            if mode == "sidebyside":
                pairs = render_sidebyside_batch_preview(upload_names)
                ordered_upload_names = upload_names
                total_upload_size = sum(upload_file_map[name].size for name in ordered_upload_names)
                st.success(f"Ready to process {len(ordered_upload_names)} uploaded file{'s' if len(ordered_upload_names) != 1 else ''} ({format_size(total_upload_size)} total).")
                start_label = "Start Side-by-Side"
                can_start = bool(pairs)
                disabled_hint = "Upload paired clips (for example shot_A / shot_B) to enable start."
                run_action = "upload_batch_or_concat"
            else:
                ordered_upload_names = render_video_selection_controls(
                    "uploaded_video_order",
                    "uploaded_video_removed",
                    upload_names
                )
                total_upload_size = sum(upload_file_map[name].size for name in ordered_upload_names)
                st.success(f"Selected {len(ordered_upload_names)} of {len(uploaded_files)} files ({format_size(total_upload_size)} total).")
                start_label = "Start Concatenation"
                can_start = len(ordered_upload_names) >= 1
                disabled_hint = "Upload and keep at least one video selected to enable start."
                run_action = "upload_batch_or_concat"
        else:
            start_label = "Start Side-by-Side" if mode == "sidebyside" else "Start Concatenation"
            disabled_hint = "Upload at least one video to enable start."
            st.warning("Please upload some MP4 files.")
else:
    st.session_state.setdefault("local_dir_input", os.getcwd())
    st.session_state.setdefault("output_dir_input", os.getcwd())
    st.session_state.setdefault("sidebyside_primary_video_path", "")
    st.session_state.setdefault("sidebyside_secondary_video_path", "")

    sidebyside_top = None
    sidebyside_bottom = None
    primary_path = None
    secondary_path = None
    ordered_local_files = []
    local_dir = st.session_state.local_dir_input
    output_dir = local_dir

    custom_output_dir = st.checkbox(
        "Use Custom Output Directory",
        value=False,
        help="Leave off to save the finished video next to the input videos."
    )

    if mode == "sidebyside" and sidebyside_submode == "single":
        if custom_output_dir:
            output_col, output_picker_col = st.columns([4, 1])
            if output_picker_col.button("Choose Folder", key="choose_output_dir"):
                selected_path, picker_error = choose_folder_with_finder(st.session_state.output_dir_input)
                if picker_error:
                    st.error(picker_error)
                elif selected_path:
                    st.session_state.output_dir_input = selected_path
                    st.rerun()
            output_dir = output_col.text_input(
                "Output Directory",
                key="output_dir_input",
                help="Where to save the stacked result."
            )
        else:
            output_dir = None

        primary_path, secondary_path = render_sidebyside_single_local_sources(sidebyside_layout)
        if not custom_output_dir:
            if primary_path:
                output_dir = os.path.dirname(primary_path)
            else:
                output_dir = st.session_state.local_dir_input
            st.caption(
                f"Output directory defaults to the {sidebyside_position_labels(sidebyside_layout)[0].lower()} folder: `{output_dir}`"
            )

        ordered_local_files = [
            os.path.basename(path) for path in (primary_path, secondary_path) if path
        ]
        local_dir = os.path.dirname(primary_path) if primary_path else st.session_state.local_dir_input
        start_label = "Start Side-by-Side"
        can_start = is_supported_video_path(primary_path) and is_supported_video_path(secondary_path)
        disabled_hint = "Select both video file paths to enable Start Side-by-Side."
        run_action = "local_sbs_single"
    else:
        local_col, local_picker_col = st.columns([4, 1])
        if local_picker_col.button("Choose Folder", key="choose_local_dir"):
            selected_path, picker_error = choose_folder_with_finder(st.session_state.local_dir_input)
            if picker_error:
                st.error(picker_error)
            elif selected_path:
                st.session_state.local_dir_input = selected_path
                st.rerun()
        local_dir = local_col.text_input(
            "Local Directory Path",
            key="local_dir_input",
            help="Absolute path to the folder containing videos."
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
                    st.rerun()
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
            local_files = list_videos_in_directory(
                local_dir,
                exclude_output_path=os.path.join(output_dir, output_filename) if output_dir else None
            )
            local_selection_key = f"local_video_selection_{local_dir}_{output_dir}_{output_filename}"
            if mode == "sidebyside":
                pairs = render_sidebyside_batch_preview(local_files)
                ordered_local_files = local_files
                st.success(f"Found {len(local_files)} video{'s' if len(local_files) != 1 else ''} in folder.")
                can_start = bool(pairs)
                disabled_hint = "Choose a folder with paired clips (for example shot_A / shot_B) to enable start."
            else:
                ordered_local_files = render_video_selection_controls(
                    f"{local_selection_key}_order",
                    f"{local_selection_key}_removed",
                    local_files
                )
                st.success(f"Selected {len(ordered_local_files)} of {len(local_files)} videos.")
                can_start = len(ordered_local_files) >= 1
                disabled_hint = "Select a folder with at least one video to enable start."
        else:
            ordered_local_files = []
            st.warning("Enter a valid local directory to preview and order videos.")
            can_start = False
            disabled_hint = "Enter a valid local directory with videos to enable start."

        start_label = "Start Side-by-Side" if mode == "sidebyside" else "Start Local Concatenation"
        run_action = "local_batch_or_concat"

with start_bar.container():
    start_clicked = render_sticky_start_button(start_label, can_start, disabled_hint)

if start_clicked:
    if run_action == "upload_sbs_single":
        if primary_upload.name == secondary_upload.name and primary_upload.size == secondary_upload.size:
            st.warning("Both uploads look identical — continuing anyway.")

        with tempfile.TemporaryDirectory() as tmp_dir:
            indicator = ProcessingIndicator()
            total_upload_size = primary_upload.size + secondary_upload.size
            saved_size = 0
            for uploaded_file, staged_name in (
                (primary_upload, f"primary_{primary_upload.name}"),
                (secondary_upload, f"secondary_{secondary_upload.name}"),
            ):
                indicator.update_upload(saved_size, total_upload_size, uploaded_file.name)
                with open(os.path.join(tmp_dir, staged_name), "wb") as f:
                    f.write(uploaded_file.getbuffer())
                saved_size += uploaded_file.size
                indicator.update_upload(
                    saved_size,
                    total_upload_size,
                    message=f"Saved {uploaded_file.name} as {staged_name}",
                )
            indicator.update_upload(total_upload_size, total_upload_size, message="Both clips saved")

            sidebyside_top = f"primary_{primary_upload.name}"
            sidebyside_bottom = f"secondary_{secondary_upload.name}"
            files = [sidebyside_top, sidebyside_bottom]
            st.success("Side-by-side mode will stack the two uploaded clips with a lossless H.264 encode.")

            timestamp = int(time.time())
            cache_dir = OUTPUT_CACHE_DIR
            cache_output_path = os.path.join(OUTPUT_CACHE_DIR, f"{timestamp}_{output_filename}")
            indicator.start_time = time.time()
            indicator.add_message("Starting side-by-side…")
            last_progress_message_at = [0.0]
            streamlit_progress, streamlit_status, streamlit_phase = make_progress_callbacks(
                indicator, last_progress_message_at
            )

            success = concatenate_videos_logic(
                tmp_dir,
                cache_output_path,
                mode=mode,
                list_name=list_name,
                debug=debug,
                progress_callback=streamlit_progress,
                status_callback=streamlit_status,
                phase_callback=streamlit_phase,
                ordered_files=files,
                sidebyside_layout=sidebyside_layout,
                sidebyside_submode=sidebyside_submode,
                sidebyside_top=sidebyside_top,
                sidebyside_bottom=sidebyside_bottom
            )
            output_paths = normalize_processing_result(success)
            if output_paths:
                indicator.complete("Side-by-side complete", detail="Your side-by-side video is ready")
                st.balloons()
                run_sidebyside_result_ui(
                    output_paths,
                    os.path.dirname(output_paths[0]) or cache_dir,
                    open_folder_key="open_upload_output_folder"
                )
            else:
                st.error("Side-by-side failed.")

    elif run_action == "upload_batch_or_concat":
        with tempfile.TemporaryDirectory() as tmp_dir:
            indicator = ProcessingIndicator()
            saved_size = 0
            for uploaded_file_name in ordered_upload_names:
                uploaded_file = upload_file_map[uploaded_file_name]
                indicator.update_upload(saved_size, total_upload_size, uploaded_file.name)
                with open(os.path.join(tmp_dir, uploaded_file.name), "wb") as f:
                    f.write(uploaded_file.getbuffer())
                saved_size += uploaded_file.size
                indicator.update_upload(
                    saved_size,
                    total_upload_size,
                    message=f"Saved upload: {uploaded_file.name}",
                )
            indicator.update_upload(total_upload_size, total_upload_size, message="All uploads saved")

            files = [name for name in ordered_upload_names if name.lower().endswith(VIDEO_EXTENSIONS)]
            if mode == "tiktok":
                st.success("TikTok mode will normalize and re-encode the clips before combining.")
            elif mode == "sidebyside":
                st.success("Side-by-side mode will stack paired clips with a lossless H.264 encode.")
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

            timestamp = int(time.time())
            if mode == "sidebyside" and sidebyside_submode == "batch":
                cache_dir = os.path.join(OUTPUT_CACHE_DIR, f"{timestamp}_sidebyside")
                os.makedirs(cache_dir, exist_ok=True)
                cache_output_path = os.path.join(cache_dir, output_filename)
            else:
                cache_dir = OUTPUT_CACHE_DIR
                cache_output_path = os.path.join(OUTPUT_CACHE_DIR, f"{timestamp}_{output_filename}")

            indicator.start_time = time.time()
            indicator.add_message(
                "Validating clips and starting side-by-side…"
                if mode == "sidebyside"
                else "Validating clips and starting concatenation…"
            )
            last_progress_message_at = [0.0]
            streamlit_progress, streamlit_status, streamlit_phase = make_progress_callbacks(
                indicator, last_progress_message_at
            )

            success = concatenate_videos_logic(
                tmp_dir,
                cache_output_path,
                mode=mode,
                list_name=list_name,
                debug=debug,
                progress_callback=streamlit_progress,
                status_callback=streamlit_status,
                phase_callback=streamlit_phase,
                tiktok_normal_seconds=tiktok_normal_seconds,
                tiktok_slow_factor=tiktok_slow_factor,
                tiktok_cut_after_seconds=tiktok_cut_after_seconds,
                ordered_files=files,
                sidebyside_layout=sidebyside_layout,
                sidebyside_submode=sidebyside_submode,
                sidebyside_top=sidebyside_top,
                sidebyside_bottom=sidebyside_bottom
            )
            output_paths = normalize_processing_result(success)
            if output_paths:
                if mode == "sidebyside":
                    indicator.complete("Side-by-side complete", detail="Your side-by-side video is ready")
                    st.balloons()
                    run_sidebyside_result_ui(
                        output_paths,
                        os.path.dirname(output_paths[0]) or cache_dir,
                        open_folder_key="open_upload_output_folder"
                    )
                else:
                    result_path = output_paths[0]
                    indicator.complete("Concatenation complete", detail="Your combined video is ready")
                    st.balloons()
                    st.markdown("### 📽️ Preview Result")
                    st.video(result_path)

                    download_col, folder_col = st.columns(2)
                    with download_col:
                        with open(result_path, "rb") as f:
                            st.download_button(
                                "💾 Download Result",
                                f,
                                file_name=os.path.basename(result_path),
                                use_container_width=True,
                            )
                    with folder_col:
                        render_open_output_folder_button(
                            os.path.dirname(result_path) or OUTPUT_CACHE_DIR,
                            key="open_upload_output_folder"
                        )
            else:
                st.error("Side-by-side failed." if mode == "sidebyside" else "Concatenation failed.")

    elif run_action == "local_sbs_single":
        primary_label, secondary_label, _relation = sidebyside_position_labels(sidebyside_layout)
        if os.path.abspath(primary_path) == os.path.abspath(secondary_path):
            st.error("Choose two different video files for side-by-side mode.")
            st.stop()
        if not output_dir:
            st.error("Output directory is missing.")
            st.stop()

        output_path = os.path.join(output_dir, output_filename)
        indicator = ProcessingIndicator()
        indicator.add_message("Preparing the two selected clips…")
        last_progress_message_at = [0.0]
        streamlit_progress, streamlit_status, streamlit_phase = make_progress_callbacks(
            indicator, last_progress_message_at
        )

        st.success("Side-by-side mode will stack the selected clips with a lossless H.264 encode.")
        with tempfile.TemporaryDirectory(prefix="sidebyside_single_") as staging_dir:
            sidebyside_top, sidebyside_bottom = stage_sidebyside_pair(
                primary_path,
                secondary_path,
                staging_dir
            )
            files = [sidebyside_top, sidebyside_bottom]
            success = concatenate_videos_logic(
                staging_dir,
                output_path,
                mode=mode,
                list_name=list_name,
                debug=debug,
                progress_callback=streamlit_progress,
                status_callback=streamlit_status,
                phase_callback=streamlit_phase,
                ordered_files=files,
                sidebyside_layout=sidebyside_layout,
                sidebyside_submode=sidebyside_submode,
                sidebyside_top=sidebyside_top,
                sidebyside_bottom=sidebyside_bottom
            )

        output_paths = normalize_processing_result(success)
        if output_paths:
            result_path = output_paths[0]
            indicator.complete("Side-by-side complete", detail="Your side-by-side video is ready")
            st.success(f"Done! Saved to: {result_path}")
            st.balloons()
            run_sidebyside_result_ui(
                output_paths,
                os.path.dirname(result_path) or output_dir,
                open_folder_key="open_local_output_folder"
            )
        else:
            st.error("Failed. Check video paths and formats.")

    elif run_action == "local_batch_or_concat":
        if not os.path.isdir(local_dir):
            st.error("Local directory does not exist.")
            st.stop()
        files = ordered_local_files
        if not files:
            st.error("No MP4/MOV videos found in the selected local directory.")
            st.stop()
        if mode == "sidebyside" and not find_side_by_side_pairs(files):
            st.error("No side-by-side pairs found in this folder.")
            st.stop()
        if mode == "tiktok":
            st.success("TikTok mode will normalize and re-encode the clips before combining.")
        elif mode == "sidebyside":
            st.success("Side-by-side mode will stack paired clips with a lossless H.264 encode.")
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

        output_path = os.path.join(output_dir, output_filename)
        indicator = ProcessingIndicator()
        indicator.add_message(
            "Starting local side-by-side…"
            if mode == "sidebyside"
            else "Starting local concatenation…"
        )
        last_progress_message_at = [0.0]
        streamlit_progress, streamlit_status, streamlit_phase = make_progress_callbacks(
            indicator, last_progress_message_at
        )

        success = concatenate_videos_logic(
            local_dir,
            output_path,
            mode=mode,
            list_name=list_name,
            debug=debug,
            progress_callback=streamlit_progress,
            status_callback=streamlit_status,
            phase_callback=streamlit_phase,
            tiktok_normal_seconds=tiktok_normal_seconds,
            tiktok_slow_factor=tiktok_slow_factor,
            tiktok_cut_after_seconds=tiktok_cut_after_seconds,
            ordered_files=files,
            sidebyside_layout=sidebyside_layout,
            sidebyside_submode=sidebyside_submode,
            sidebyside_top=sidebyside_top,
            sidebyside_bottom=sidebyside_bottom
        )
        output_paths = normalize_processing_result(success)
        if output_paths:
            if mode == "sidebyside":
                indicator.complete("Side-by-side complete", detail="Your side-by-side video is ready")
                st.success(f"Done! Saved {len(output_paths)} clip{'s' if len(output_paths) != 1 else ''} to: {output_dir}")
                st.balloons()
                run_sidebyside_result_ui(output_paths, output_dir, open_folder_key="open_local_output_folder")
            else:
                result_path = output_paths[0]
                indicator.complete("Concatenation complete", detail="Your combined video is ready")
                st.success(f"Done! Saved to: {result_path}")
                st.balloons()
                st.markdown("### 📽️ Preview Result")
                st.video(result_path)

                folder_col, _ = st.columns([1, 1])
                with folder_col:
                    render_open_output_folder_button(
                        os.path.dirname(result_path) or output_dir,
                        key="open_local_output_folder"
                    )
        else:
            st.error("Failed. Check directory paths and video formats.")


st.markdown("---")
st.markdown("### How it works")
st.markdown("""
1. **Upload:** Select all the MP4/MOV clips you want to join or stack.
2. **Order / Pair:** Review clip order for concat modes, or choose / auto-detect pairs for side-by-side.
3. **Concat:** Normal and 360 modes join matching videos without re-encoding.
4. **TikTok:** TikTok mode uses the sidebar timing and cut controls, then combines everything.
5. **Side-by-side:** Single mode shows two clearly labeled video-file pickers (top/bottom or left/right). Batch mode pairs `_A`/`_B` names and writes one output per pair.
6. **Metadata:** If '360' mode is selected, ExifTool is used to inject spatial metadata from the first clip.
""")
