#!/usr/bin/env python3

# =============================================================================
# EXAMPLE USAGE:
# 360 Videos: python3 concat_videos.py --working_dir . --output_file ./final_360_video.mp4 --debug --mode 360   
## Normal MP4s: python3 concat_videos.py --working_dir . --output_file ./output.mp4 --mode normal
# =============================================================================

import os
import argparse
import subprocess
import logging
import re
from tqdm import tqdm

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

def parse_time_to_seconds(time_str):
    """Converts FFmpeg time format (HH:MM:SS.ms) to total seconds."""
    try:
        h, m, s = time_str.split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception as e:
        logging.debug(f"Could not parse time string '{time_str}': {e}")
        return 0.0

def concatenate_videos_logic(working_dir, output_file, mode="360", list_name="mylist.txt", debug=False, progress_callback=None):
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

    # 2. Find and sort MP4 files (ignoring the output file if it's in the same dir)
    logging.info(f"Scanning for MP4 files in: {working_dir}")
    try:
        files = [f for f in os.listdir(working_dir) if f.lower().endswith(".mp4") and os.path.abspath(os.path.join(working_dir, f)) != output_file]
    except FileNotFoundError:
        logging.critical(f"Working directory not found: {working_dir}")
        return False

    if not files:
        logging.error("No MP4 files found in the specified directory.")
        return False

    files.sort()
    logging.info(f"Found {len(files)} MP4 files. Calculating total duration...")

    # 3. Calculate total duration
    total_duration = 0.0
    for f in files:
        file_path = os.path.join(working_dir, f)
        duration = get_video_duration(file_path)
        total_duration += duration
        logging.debug(f"File: {f} | Duration: {duration:.2f}s")
    
    logging.info(f"Total video duration to process: {total_duration:.2f} seconds.")

    # 4. Create the mylist.txt file
    logging.info(f"Creating FFmpeg concat list at: {list_path}")
    try:
        with open(list_path, 'w') as list_file:
            for f in files:
                abs_path = os.path.join(working_dir, f)
                list_file.write(f"file '{abs_path}'\n")
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
        process = subprocess.Popen(
            ffmpeg_cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, universal_newlines=True
        )

        previous_time = 0.0
        for line in process.stderr:
            logging.debug(line.strip())
            time_match = re.search(r"time=(\d{2}:\d{2}:\d{2}\.\d{2})", line)
            if time_match:
                time_str = time_match.group(1)
                current_time = parse_time_to_seconds(time_str)
                if progress_callback:
                    progress_callback(current_time, total_duration)

        process.wait()

        if process.returncode == 0:
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
            logging.error(f"FFmpeg exited with error code {process.returncode}.")
            return False

    except Exception as e:
        logging.critical(f"Error: {e}")
        return False
        
    finally:
        if os.path.exists(list_path):
            os.remove(list_path)

def main():
    parser = argparse.ArgumentParser(description="Losslessly concat 360 MP4 videos and preserve spatial metadata.")
    parser.add_argument("--working_dir", type=str, required=True, help="Directory containing the MP4 files.")
    parser.add_argument("--output_file", type=str, required=True, help="Full path for the output MP4 file.")
    parser.add_argument("--list_name", type=str, default="mylist.txt", help="Name of the temporary text file.")
    parser.add_argument("--mode", type=str, choices=["360", "normal"], default="360", help="Processing mode: '360' for VR/360 videos (preserves metadata) or 'normal' for standard MP4s.")
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
        progress_callback=cli_progress
    )
    
    if pbar_ref[0]:
        pbar_ref[0].close()
    
    if success:
        logging.info(f"Final video is at: {args.output_file}")
    else:
        logging.error("Concatenation failed.")

if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()