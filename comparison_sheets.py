"""Relative-position comparison sheets, using FFmpeg and bounded thumbnail memory."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont, ImageOps

from concat_videos import VIDEO_EXTENSIONS

COMPARISON_EXTENSIONS = tuple(set(VIDEO_EXTENSIONS + ('.mkv', '.avi', '.webm', '.m4v')))


def find_comparison_videos(folder):
    return sorted((p for p in Path(folder).iterdir()
                   if p.is_file() and p.suffix.lower() in COMPARISON_EXTENSIONS),
                  key=lambda p: (p.name.casefold(), p.name))


def _probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                             '-show_entries', 'stream=duration,avg_frame_rate:format=duration',
                             '-of', 'json', str(path)], capture_output=True, text=True,
                            check=True, timeout=60)
    data = json.loads(result.stdout)
    stream = data['streams'][0]
    duration = float(stream.get('duration') or data['format']['duration'])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('No valid video duration')
    try:
        numerator, denominator = map(float, stream['avg_frame_rate'].split('/'))
        frame_time = denominator / numerator
        if not math.isfinite(frame_time) or frame_time <= 0:
            frame_time = 0.04
    except (KeyError, ValueError, ZeroDivisionError):
        frame_time = 0.04
    return duration, frame_time


def _extract(path, timestamp, destination, size):
    width, height = size
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                    '-ss', str(timestamp), '-i', str(path), '-map', '0:v:0',
                    '-frames:v', '1', '-vf',
                    f'scale={width}:{height}:force_original_aspect_ratio=decrease:reset_sar=1',
                    '-threads', '1', str(destination)], capture_output=True, check=True, timeout=120)
    if not destination.is_file():
        raise ValueError('No decodable frame at this position')


def generate_comparison_sheets(folder, output_dir, frame_count=16, thumbnail_size=(320, 180),
                               sheet_size=None, image_format='PNG', show_position=True,
                               keep_frames=False, progress_callback=None, status_callback=None):
    """Return paths, frame count, and warnings. Progress callback takes (current, total).

    Samples include 0% and 100% (the latter uses the last frame's start time).
    A single requested sample uses 50%. Each run gets its own non-overwriting folder.
    """
    if not isinstance(frame_count, int) or not 1 <= frame_count <= 1000:
        raise ValueError('Frame count must be between 1 and 1000')
    if image_format.upper() not in ('PNG', 'JPEG'):
        raise ValueError('Choose PNG or JPEG')
    if len(thumbnail_size) != 2 or any(not isinstance(v, int) or v < 32 or v > 4096 for v in thumbnail_size):
        raise ValueError('Thumbnail dimensions must be between 32 and 4096 pixels')
    videos = find_comparison_videos(folder)
    if not videos:
        raise ValueError('No supported videos found in this folder')
    for binary in ('ffmpeg', 'ffprobe'):
        if not shutil.which(binary):
            raise ValueError(f'{binary} is required and was not found on PATH')
    columns = math.ceil(math.sqrt(len(videos)))
    rows = math.ceil(len(videos) / columns)
    margin, label_height = 12, 60
    tw, th = thumbnail_size
    natural = (columns * (tw + margin) + margin, rows * (th + label_height + margin) + margin)
    width, height = sheet_size or natural
    if any(not isinstance(v, int) or v <= 0 for v in (width, height)) or width * height > 80_000_000:
        raise ValueError('Sheet dimensions must be positive and at most 80 million pixels')
    cell_w = (width - margin) // columns - margin
    cell_h = (height - margin) // rows - margin
    if cell_w < 64 or cell_h < label_height + 32:
        raise ValueError('Sheet dimensions are too small for this number of videos')
    os.makedirs(output_dir, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='Comparison_', dir=os.path.abspath(output_dir)))
    warnings, metadata, paths = [], [], []
    total = len(videos) * (frame_count + 1) + frame_count
    completed = extracted = 0

    def advance(message):
        nonlocal completed
        completed += 1
        if status_callback:
            status_callback(message)
        if progress_callback:
            progress_callback(completed, total)

    for video in videos:
        try:
            metadata.append(_probe(video))
        except (subprocess.SubprocessError, ValueError, KeyError, IndexError, OSError) as exc:
            metadata.append(None)
            warnings.append(f'{video.name}: cannot read video ({exc})')
        advance(f'Inspected {video.name}')
    if not any(metadata):
        run_dir.rmdir()
        raise ValueError('None of the selected videos could be read')
    font = ImageFont.load_default(size=14)
    with tempfile.TemporaryDirectory(prefix='comparison_frames_') as temporary:
        for position in range(frame_count):
            relative = position / (frame_count - 1) if frame_count > 1 else 0.5
            with Image.new('RGB', (width, height), '#171b23') as sheet:
                draw = ImageDraw.Draw(sheet)
                for index, (video, info) in enumerate(zip(videos, metadata)):
                    x = margin + (index % columns) * (cell_w + margin)
                    y = margin + (index // columns) * (cell_h + margin)
                    image_height = cell_h - label_height
                    draw.rectangle((x, y, x + cell_w, y + image_height), fill='black')
                    timestamp = None
                    destination = Path(temporary) / 'frame.png'
                    destination.unlink(missing_ok=True)
                    try:
                        if info is None:
                            raise ValueError('Unreadable video')
                        duration, frame_time = info
                        timestamp = min(relative * duration, max(0, duration - frame_time))
                        try:
                            _extract(video, timestamp, destination, thumbnail_size)
                        except (subprocess.SubprocessError, ValueError):
                            if relative != 1:
                                raise
                            timestamp = max(0, duration - max(frame_time * 2, 0.25))
                            _extract(video, timestamp, destination, thumbnail_size)
                        with Image.open(destination) as frame:
                            thumb = ImageOps.contain(frame, (cell_w, image_height))
                            sheet.paste(thumb, (x + (cell_w - thumb.width) // 2,
                                                y + (image_height - thumb.height) // 2))
                            thumb.close()
                        extracted += 1
                        if keep_frames:
                            frame_dir = run_dir / 'frames' / f'{index + 1:03d}_{video.name}'
                            frame_dir.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(destination, frame_dir / f'Frame_{position + 1:03d}.png')
                    except (subprocess.SubprocessError, ValueError, OSError) as exc:
                        draw.text((x + 6, y + 8), 'Frame unavailable', font=font, fill='white')
                        if info is not None:
                            warnings.append(f'{video.name}, position {position + 1}: {exc}')
                    # Wrap the complete filename rather than silently truncating it.
                    lines, line = [], ''
                    for character in video.name:
                        if draw.textlength(line + character, font=font) > cell_w - 8:
                            lines.append(line)
                            line = ''
                        line += character
                    lines.append(line)
                    label = '\n'.join(lines)
                    # Fit unusually long names inside their reserved label area.
                    label_image = Image.new('RGB', (cell_w, max(20, len(lines) * 18)), '#171b23')
                    ImageDraw.Draw(label_image).multiline_text((4, 0), label, font=font, fill='white', spacing=3)
                    fitted = ImageOps.contain(label_image, (cell_w, 36))
                    sheet.paste(fitted, (x, y + image_height + 3))
                    fitted.close()
                    label_image.close()
                    if show_position:
                        text = f'{relative:.1%}' + (f' | {timestamp:.3f}s' if timestamp is not None else '')
                        draw.text((x + 4, y + cell_h - 18), text, font=font, fill='#b4bdcc')
                    advance(f'Position {position + 1}/{frame_count}: {video.name}')
                suffix = 'png' if image_format.upper() == 'PNG' else 'jpg'
                path = run_dir / f'Comparison_{position + 1:03d}.{suffix}'
                sheet.save(path, quality=95, subsampling=0)
                paths.append(str(path))
            advance(f'Saved {path.name}')
    manifest = {'videos': [p.name for p in videos], 'frame_count': frame_count,
                'extracted_frames': extracted, 'warnings': warnings,
                'positions': [i / (frame_count - 1) if frame_count > 1 else 0.5 for i in range(frame_count)]}
    (run_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return dict(paths=paths, output_dir=str(run_dir), extracted_frames=extracted, warnings=warnings)
