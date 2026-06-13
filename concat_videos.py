#!/usr/bin/env python3

# =============================================================================
# EXAMPLE USAGE:
# 360 Videos: python3 concat_videos.py --working_dir . --output_file ./final_360_video.mp4 --debug --mode 360   
## Normal MP4s: python3 concat_videos.py --working_dir . --output_file ./output.mp4 --mode normal
## TikTok edit: python3 concat_videos.py --working_dir . --output_file ./tiktok_edit.mp4 --mode tiktok
# =============================================================================

import os
import argparse
import subprocess
import logging
import re
import json
import tempfile
from tqdm import tqdm

VIDEO_EXTENSIONS = (".mp4", ".mov")
TIKTOK_NORMAL_SECONDS = 0.5
TIKTOK_SLOW_FACTOR = 0.4
TIKTOK_CUT_AFTER_SECONDS = 0.0
MIN_TIKTOK_SLOW_FACTOR = 0.1

def setup_logger(debug_mode):
    """Sets up the logging level based on user input."""
    level = logging.DEBUG if debug_mode else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

def get_video_duration(file_path):
    """Uses ffprobe to get the duration of a video file in seconds."""
    cmd = [
        "ffprobe", 
        "-v", "error", 
        "-show_entries", "format=duration", 
        "-of", "default=noprint_wrappers=1:nokey=1", 
        file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to get duration for {file_path}. Error: {e.stderr}")
        return 0.0

def has_audio_stream(file_path):
    """Returns True when the file has at least one audio stream."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=index",
        "-of", "csv=p=0",
        file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False

def get_video_files(working_dir, output_file=None):
    """Finds supported video inputs in the working directory, excluding the output file."""
    output_abs = os.path.abspath(output_file) if output_file else None
    return [
        f for f in os.listdir(working_dir)
        if f.lower().endswith(VIDEO_EXTENSIONS)
        and (output_abs is None or os.path.abspath(os.path.join(working_dir, f)) != output_abs)
    ]

def format_concat_file_line(file_path):
    """Formats a file path for FFmpeg concat demuxer lists."""
    escaped_path = file_path.replace("'", "'\\''")
    return f"file '{escaped_path}'\n"

def parse_time_to_seconds(time_str):
    """Converts FFmpeg time format (HH:MM:SS.ms) to total seconds."""
    try:
        h, m, s = time_str.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception as e:
        logging.debug(f"Could not parse time string '{time_str}': {e}")
        return 0.0

def get_video_properties(file_path):
    """Uses ffprobe to get resolution, frame rate, codec, and audio properties."""
    # Video properties
    cmd_v = [
        "ffprobe", "-v", "error", 
        "-select_streams", "v:0", 
        "-show_entries", "stream=width,height,r_frame_rate,codec_name,pix_fmt", 
        "-of", "json", 
        file_path
    ]
    video_props = {}
    try:
        result = subprocess.run(cmd_v, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(result.stdout)
        if 'streams' in data and len(data['streams']) > 0:
            video_props = data['streams'][0]
    except Exception as e:
        logging.error(f"Failed to get video properties for {file_path}: {e}")

    # Audio properties
    cmd_a = [
        "ffprobe", "-v", "error", 
        "-select_streams", "a:0", 
        "-show_entries", "stream=sample_rate,channels,codec_name", 
        "-of", "json", 
        file_path
    ]
    audio_props = {}
    try:
        result = subprocess.run(cmd_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(result.stdout)
        if 'streams' in data and len(data['streams']) > 0:
            audio_props = data['streams'][0]
    except Exception:
        # Some videos might not have audio
        pass
    
    return {
        "width": video_props.get("width"),
        "height": video_props.get("height"),
        "r_frame_rate": video_props.get("r_frame_rate"),
        "video_codec": video_props.get("codec_name"),
        "pix_fmt": video_props.get("pix_fmt"),
        "sample_rate": audio_props.get("sample_rate"),
        "channels": audio_props.get("channels"),
        "audio_codec": audio_props.get("codec_name")
    }

def validate_video_consistency(working_dir, files):
    """Checks if all videos have matching properties. Returns (mismatches, base_properties)."""
    if not files:
        return [], {}
    
    first_file_path = os.path.join(working_dir, files[0])
    base_props = get_video_properties(first_file_path)
    mismatches = []
    
    for f in files[1:]:
        file_path = os.path.join(working_dir, f)
        props = get_video_properties(file_path)
        
        for key, base_val in base_props.items():
            current_val = props.get(key)
            if current_val != base_val:
                mismatches.append(f"File '{f}' mismatch: {key} is '{current_val}' (expected '{base_val}')")
                
    return mismatches, base_props

def get_tiktok_source_duration_for_cut(
    duration,
    normal_seconds=TIKTOK_NORMAL_SECONDS,
    slow_factor=TIKTOK_SLOW_FACTOR,
    cut_after_seconds=TIKTOK_CUT_AFTER_SECONDS
):
    """Calculates how much source video is needed for the requested output cut time."""
    if cut_after_seconds <= 0:
        return duration
    if cut_after_seconds <= normal_seconds:
        return min(duration, cut_after_seconds)
    source_cut = normal_seconds + ((cut_after_seconds - normal_seconds) * slow_factor)
    return min(duration, source_cut)

def get_tiktok_output_duration(
    duration,
    normal_seconds=TIKTOK_NORMAL_SECONDS,
    slow_factor=TIKTOK_SLOW_FACTOR,
    cut_after_seconds=TIKTOK_CUT_AFTER_SECONDS
):
    """Calculates the output duration after the per-clip TikTok speed ramp."""
    source_duration = get_tiktok_source_duration_for_cut(
        duration,
        normal_seconds,
        slow_factor,
        cut_after_seconds
    )
    normal_duration = min(source_duration, normal_seconds)
    slow_duration = max(source_duration - normal_seconds, 0.0) / slow_factor
    return normal_duration + slow_duration

def build_atempo_chain(speed_factor):
    """Builds FFmpeg atempo filters while staying inside each filter's supported range."""
    filters = []
    remaining = speed_factor
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    filters.append(f"atempo={remaining:.6g}")
    return ",".join(filters)

def validate_tiktok_settings(normal_seconds, slow_factor, cut_after_seconds=TIKTOK_CUT_AFTER_SECONDS):
    """Returns an error string for invalid TikTok mode settings."""
    if normal_seconds < 0:
        return "TikTok normal play duration must be 0 seconds or greater."
    if slow_factor < MIN_TIKTOK_SLOW_FACTOR:
        return f"TikTok slow speed must be at least {MIN_TIKTOK_SLOW_FACTOR}x."
    if cut_after_seconds < 0:
        return "TikTok cut-after duration must be 0 seconds or greater."
    return None

def run_ffmpeg_command(ffmpeg_cmd, total_duration, progress_callback=None, progress_offset=0.0):
    """Runs FFmpeg and reports progress based on stderr time markers."""
    process = subprocess.Popen(
        ffmpeg_cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True
    )

    for line in process.stderr:
        logging.debug(line.strip())
        time_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})", line)
        if time_match and progress_callback:
            current_time = parse_time_to_seconds(time_match.group(1))
            progress_callback(progress_offset + current_time, total_duration)

    process.wait()
    return process.returncode == 0

def build_tiktok_filter(duration, width, height, audio_mode, normal_seconds, slow_factor):
    """Builds the FFmpeg filter graph for one TikTok-style processed clip."""
    normal_duration = min(duration, normal_seconds)
    has_normal_segment = normal_duration > 0
    has_slow_segment = duration > normal_duration
    output_duration = get_tiktok_output_duration(duration, normal_seconds, slow_factor)
    atempo_chain = build_atempo_chain(slow_factor)
    video_base = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
    )

    if has_normal_segment and has_slow_segment:
        video_filter = (
            f"{video_base},split=2[vnormal][vslow];"
            f"[vnormal]trim=0:{normal_duration},setpts=PTS-STARTPTS[vnormalout];"
            f"[vslow]trim=start={normal_duration}:end={duration},setpts=(PTS-STARTPTS)/{slow_factor}[vslowout];"
            f"[vnormalout][vslowout]concat=n=2:v=1:a=0[vout]"
        )
    elif has_slow_segment:
        video_filter = (
            f"{video_base},trim=0:{duration},setpts=(PTS-STARTPTS)/{slow_factor}[vout]"
        )
    else:
        video_filter = (
            f"{video_base},trim=0:{normal_duration},setpts=PTS-STARTPTS[vout]"
        )

    if audio_mode == "none":
        return video_filter, ["-map", "[vout]"]

    if audio_mode == "silent":
        audio_filter = (
            f"anullsrc=channel_layout=stereo:sample_rate=48000,"
            f"atrim=0:{output_duration},asetpts=PTS-STARTPTS[aout]"
        )
        return f"{video_filter};{audio_filter}", ["-map", "[vout]", "-map", "[aout]"]

    if has_normal_segment and has_slow_segment:
        audio_filter = (
            f"[0:a]asplit=2[anormal][aslow];"
            f"[anormal]atrim=0:{normal_duration},asetpts=PTS-STARTPTS[anormalout];"
            f"[aslow]atrim=start={normal_duration}:end={duration},asetpts=PTS-STARTPTS,"
            f"{atempo_chain}[aslowout];"
            "[anormalout][aslowout]concat=n=2:v=0:a=1,"
            "aformat=sample_rates=48000:channel_layouts=stereo[aout]"
        )
    elif has_slow_segment:
        audio_filter = (
            f"[0:a]atrim=0:{duration},asetpts=PTS-STARTPTS,{atempo_chain},"
            "aformat=sample_rates=48000:channel_layouts=stereo[aout]"
        )
    else:
        audio_filter = (
            f"[0:a]atrim=0:{normal_duration},asetpts=PTS-STARTPTS,"
            "aformat=sample_rates=48000:channel_layouts=stereo[aout]"
        )

    return f"{video_filter};{audio_filter}", ["-map", "[vout]", "-map", "[aout]"]

def process_tiktok_mode(
    working_dir,
    files,
    output_file,
    list_path,
    progress_callback=None,
    normal_seconds=TIKTOK_NORMAL_SECONDS,
    slow_factor=TIKTOK_SLOW_FACTOR,
    cut_after_seconds=TIKTOK_CUT_AFTER_SECONDS
):
    """Creates TikTok-style slowed clips and concatenates them."""
    settings_error = validate_tiktok_settings(normal_seconds, slow_factor, cut_after_seconds)
    if settings_error:
        logging.error(settings_error)
        return False

    first_props = get_video_properties(os.path.join(working_dir, files[0]))
    width = first_props.get("width") or 1080
    height = first_props.get("height") or 1920
    clip_durations = [get_video_duration(os.path.join(working_dir, f)) for f in files]
    audio_streams = [has_audio_stream(os.path.join(working_dir, f)) for f in files]
    keep_audio = any(audio_streams)
    processed_durations = [
        get_tiktok_source_duration_for_cut(duration, normal_seconds, slow_factor, cut_after_seconds)
        for duration in clip_durations
    ]
    total_duration = sum(
        get_tiktok_output_duration(duration, normal_seconds, slow_factor, cut_after_seconds)
        for duration in clip_durations
    )
    cut_label = "full clip" if cut_after_seconds <= 0 else f"cut each clip after {cut_after_seconds:.2f}s"
    logging.info(f"TikTok mode settings: {normal_seconds:.2f}s normal, then {slow_factor:.2f}x speed, {cut_label}.")
    logging.info(f"Total TikTok edit duration to process: {total_duration:.2f} seconds.")

    with tempfile.TemporaryDirectory(prefix="tiktok_mode_", dir=working_dir) as temp_dir:
        processed_files = []
        completed_duration = 0.0

        for index, (filename, duration, processed_duration, has_audio) in enumerate(zip(files, clip_durations, processed_durations, audio_streams), start=1):
            input_path = os.path.join(working_dir, filename)
            temp_output = os.path.join(temp_dir, f"{index:04d}_processed.mp4")
            audio_mode = "input" if has_audio else "silent" if keep_audio else "none"
            filter_complex, mappings = build_tiktok_filter(
                processed_duration,
                width,
                height,
                audio_mode,
                normal_seconds,
                slow_factor
            )
            logging.info(f"Processing TikTok clip {index}/{len(files)}: {filename}")

            ffmpeg_cmd = [
                "ffmpeg", "-y", "-i", input_path,
                "-filter_complex", filter_complex,
                *mappings,
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "18",
                "-fps_mode", "vfr",
                "-movflags", "+faststart"
            ]
            if "-map" in mappings and "[aout]" in mappings:
                ffmpeg_cmd.extend(["-c:a", "aac", "-b:a", "192k"])
            ffmpeg_cmd.append(temp_output)

            if not run_ffmpeg_command(ffmpeg_cmd, total_duration, progress_callback, completed_duration):
                logging.error(f"Failed to process TikTok clip: {filename}")
                return False

            processed_files.append(temp_output)
            completed_duration += get_tiktok_output_duration(duration, normal_seconds, slow_factor, cut_after_seconds)
            if progress_callback:
                progress_callback(completed_duration, total_duration)

        logging.info(f"Creating FFmpeg concat list at: {list_path}")
        with open(list_path, 'w') as list_file:
            for processed_file in processed_files:
                list_file.write(format_concat_file_line(processed_file))

        concat_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-c", "copy", output_file
        ]
        logging.info("Combining TikTok clips...")
        success = run_ffmpeg_command(concat_cmd, total_duration)
        if success and progress_callback:
            progress_callback(total_duration, total_duration)
        return success

def concatenate_videos_logic(
    working_dir,
    output_file,
    mode="360",
    list_name="mylist.txt",
    debug=False,
    progress_callback=None,
    tiktok_normal_seconds=TIKTOK_NORMAL_SECONDS,
    tiktok_slow_factor=TIKTOK_SLOW_FACTOR,
    tiktok_cut_after_seconds=TIKTOK_CUT_AFTER_SECONDS,
    ordered_files=None
):
    """
    Core logic for concatenating videos.
    progress_callback: A function that takes (current_seconds, total_seconds)
    """
    setup_logger(debug)
    working_dir = os.path.abspath(working_dir)
    output_file = os.path.abspath(output_file)
    list_path = os.path.join(working_dir, list_name)

    # 1. Ensure output directory exists
    output_dir = os.path.dirname(output_file)
    if not os.path.exists(output_dir):
        logging.info(f"Output directory does not exist. Creating: {output_dir}")
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            logging.critical(f"Failed to create output directory: {e}")
            return False

    # 2. Find video files (ignoring the output file if it's in the same dir)
    if ordered_files is None:
        logging.info(f"Scanning for video files in: {working_dir}")
        try:
            files = get_video_files(working_dir, output_file)
        except FileNotFoundError:
            logging.critical(f"Working directory not found: {working_dir}")
            return False
        files.sort()
    else:
        logging.info("Using selected video order.")
        files = [
            filename for filename in ordered_files
            if filename.lower().endswith(VIDEO_EXTENSIONS)
            and os.path.exists(os.path.join(working_dir, filename))
        ]

    if not files:
        logging.error("No MP4 or MOV files found in the specified directory.")
        return False

    logging.info(f"Found {len(files)} video files. Calculating total duration...")

    # 3. Calculate total duration
    total_duration = 0.0
    for f in files:
        file_path = os.path.join(working_dir, f)
        duration = get_video_duration(file_path)
        total_duration += duration
        logging.debug(f"File: {f} | Duration: {duration:.2f}s")
    
    logging.info(f"Total video duration to process: {total_duration:.2f} seconds.")

    if mode == "tiktok":
        try:
            return process_tiktok_mode(
                working_dir,
                files,
                output_file,
                list_path,
                progress_callback,
                tiktok_normal_seconds,
                tiktok_slow_factor,
                tiktok_cut_after_seconds
            )
        finally:
            if os.path.exists(list_path):
                os.remove(list_path)

    # 4. Create the mylist.txt file
    logging.info(f"Creating FFmpeg concat list at: {list_path}")
    try:
        with open(list_path, 'w') as list_file:
            for f in files:
                abs_path = os.path.join(working_dir, f)
                list_file.write(format_concat_file_line(abs_path))
    except Exception as e:
        logging.critical(f"Failed to write to {list_path}: {e}")
        return False

    # Grab the very first video to act as our metadata donor
    first_video_path = os.path.join(working_dir, files[0])

    # 5. Run the FFmpeg concat command
    if mode == "360":
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, 
            "-i", first_video_path, "-map", "0", "-map_metadata", "1", 
            "-map_metadata:s:v", "1:s:v", "-c", "copy", "-movflags", "use_metadata_tags", 
            output_file
        ]
    else:
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, 
            "-c", "copy", output_file
        ]

    logging.info(f"Running FFmpeg concatenation...")
    
    try:
        if run_ffmpeg_command(ffmpeg_cmd, total_duration, progress_callback):
            logging.info(f"FFmpeg concatenation finished successfully.")
            if mode == "360":
                logging.info("Step 6: Injecting exact 360 spatial metadata using ExifTool...")
                exiftool_cmd = [
                    "exiftool", "-TagsFromFile", first_video_path, "-all:all>all:all", 
                    "-overwrite_original", output_file
                ]
                try:
                    subprocess.run(exiftool_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
                    logging.info("Success! ExifTool copied all 360/spherical tags.")
                except FileNotFoundError:
                    logging.warning("ExifTool not found.")
                except subprocess.CalledProcessError:
                    logging.error("ExifTool failed.")
            return True
        else:
            logging.error("FFmpeg exited with an error.")
            return False

    except Exception as e:
        logging.critical(f"Error: {e}")
        return False
        
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)

def main():
    parser = argparse.ArgumentParser(description="Concat MP4/MOV videos with normal, 360, or TikTok edit modes.")
    parser.add_argument("--working_dir", type=str, required=True, help="Directory containing the MP4/MOV files.")
    parser.add_argument("--output_file", type=str, required=True, help="Full path for the output MP4 file.")
    parser.add_argument("--list_name", type=str, default="mylist.txt", help="Name of the temporary text file.")
    parser.add_argument("--mode", type=str, choices=["360", "normal", "tiktok"], default="360", help="Processing mode: '360', 'normal', or 'tiktok'.")
    parser.add_argument("--tiktok_normal_seconds", type=float, default=TIKTOK_NORMAL_SECONDS, help="TikTok mode: seconds to play normally before slowing each clip.")
    parser.add_argument("--tiktok_slow_factor", type=float, default=TIKTOK_SLOW_FACTOR, help="TikTok mode: playback speed after the normal section, e.g. 0.4 for 40% speed.")
    parser.add_argument("--tiktok_cut_after_seconds", type=float, default=TIKTOK_CUT_AFTER_SECONDS, help="TikTok mode: output seconds to keep from each processed clip. Use 0 for full clips.")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging.")
    args = parser.parse_args()

    # Progress bar using tqdm for CLI
    from tqdm import tqdm
    pbar_ref = [None]
    
    def cli_progress(current, total):
        if pbar_ref[0] is None:
            pbar_ref[0] = tqdm(total=total, unit="s", desc="Concatenating", bar_format="{l_bar}{bar}| {n:.1f}/{total:.1f}s [{elapsed}<{remaining}]")
        
        increment = current - pbar_ref[0].n
        if increment > 0:
            pbar_ref[0].update(increment)

    success = concatenate_videos_logic(
        args.working_dir, 
        args.output_file, 
        mode=args.mode, 
        list_name=args.list_name, 
        debug=args.debug,
        progress_callback=cli_progress,
        tiktok_normal_seconds=args.tiktok_normal_seconds,
        tiktok_slow_factor=args.tiktok_slow_factor,
        tiktok_cut_after_seconds=args.tiktok_cut_after_seconds
    )
    
    if pbar_ref[0]:
        pbar_ref[0].close()
    
    if success:
        logging.info(f"Final video is at: {args.output_file}")
    else:
        logging.error("Concatenation failed.")

if __name__ == "__main__":
    main()
