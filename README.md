# Video Combiner

Run `pip install -r requirements.txt`, then `streamlit run streamlit_app.py`.
FFmpeg and ffprobe must be installed and available on PATH. Existing 360 mode also uses ExifTool.

## Multiple Video Comparison Contact Sheets

Select **Comparison Contact Sheets** in Processing Mode. Choose an input folder and output directory. The mode scans the immediate folder for MP4, MOV, MKV, AVI, WebM and M4V files, sorted alphabetically. Set the number of frames (default 16), thumbnail bounds, optional sheet dimensions, image format, position labels and whether to retain extracted thumbnails.

Each sample position produces one sheet containing every video. Samples span 0% to 100% of each video's own duration; the endpoint seeks to the last frame, with a small backwards retry if that frame cannot be decoded. A single sample uses 50%. FFmpeg preserves display aspect ratio and the grid letterboxes thumbnails. Filename labels always appear. Custom sheet dimensions must have enough space for the grid; thumbnail dimensions control extraction resolution.

Each run creates a separate `Comparison_…` directory with `Comparison_001.png` (or `.jpg`) and subsequent sheets, plus a manifest containing ordering, sample positions, extraction count and warnings. Optional thumbnails are grouped under `frames` by source video. Unreadable videos and failed frames retain labelled placeholder cells in the grid. The UI provides progress, a sheet preview/download selector and an output-folder button. Processing happens in a worker; memory holds one sheet and thumbnail at a time.

Run checks with `python -m unittest discover -s tests -v`.


## Multi-video grid

Select **Multi-video Grid** in Processing Mode. Choose a folder, remove/reorder inputs,
select an even output width/height, choose Fill or Fit, and supply a new MP4 output
path. The numbered preview follows the selected input order. Existing two-video
side-by-side and contact-sheet workflows remain available.

The grid uses one FFmpeg filter pipeline and one H.264 CRF 18 encode. Audio is
omitted. Layout candidates use unequal rows/columns and score cropping (or Fit
padding), upscaling and tile-area balance. Every tile covers an exact portion of
the canvas. The search is deterministic and quadratic in input count; it is a
heuristic, not a globally optimal arbitrary rectangle packer.

Every input is decoded once during strict validation and once during composition.
Validation counts actual frames, checks frame timestamps against rational FPS,
allows container time-base rounding, and rejects decoder errors, timing gaps,
VFR and inconsistent declared frame counts. Inputs must share a probed container
family and constant rational FPS. Rotation metadata and non-square pixels must be
normalised externally first; these are reported explicitly. Source timestamps
are replaced with exact frame-index timestamps. Shorter tiles repeat their last
frame to the longest input's frame count. A final decode verifies the result's
frame count, cadence, FPS and dimensions before publishing it. This extra input
and output validation costs time but avoids silently accepting incomplete output.

Progress distinguishes metadata inspection, validation, encoding and verification.
Encoding shows completed/total frames and elapsed/estimated remaining time. Cancel
is available during all processing phases, including silent subprocess work;
processes are terminated and reaped and partial output/filter scripts are removed.
The output path is never overwritten. A failed job reports an error, not success.
Outputs are saved locally; browser preview/download is limited to 200 MB to avoid
Streamlit loading very large completed videos into memory.

Memory does not retain all decoded frames or timestamps: validation uses a bounded
line queue and encoding streams tiles through FFmpeg. Decoder threads are limited
to one per source, filter threads to one, and encoder threads to two. Memory still
increases with input count, source resolution, codec reference frames and output
size. There is no application input-count limit; available RAM, FFmpeg/OS resource
limits and Windows command-line length remain practical constraints. No hardware
acceleration is selected automatically. This gives a reproducible CPU path without
hidden codec or scaling fallbacks. FFmpeg with libx264 and xstack is required.

### Verification and benchmarks

Run `python -m unittest discover -s tests -v`. Tests cover geometry, odd source
sizes, Fit/Fill, integer/fractional FPS, non-zero start times, pixel-identified frame
alignment, final-frame holds, corruption, VFR, mismatched FPS, 16-input rendering,
progress, cancellation at each phase, child termination and temporary-file cleanup.
Run `python benchmarks/ui_smoke.py` for Streamlit metadata, progress, cancel and
completion checks; Streamlit's test event loop needs access to local sockets.

Run `python benchmarks/grid_benchmark.py --counts 2 16 32 --frames 60` for the
opt-in benchmark. `--source-size 1920x1080` tests HD inputs. JSON reports record
wall time, encode-only time, output size and peak child working set on Windows.
Peak memory excludes the Python UI and measures the FFmpeg/ffprobe subprocesses.
Sources are opened independently from the same synthetic test clip; results do
not represent all codecs or random disk access. Legacy two-video processing uses
ultrafast lossless encoding and no strict frame audits; the new path uses medium
CRF18 and audits. Their timings are not an encoder-quality-equivalent comparison.

Measured on this Windows environment (30 FPS):

| Inputs / source | Frames each | Total time | Encoding | Peak child memory |
| --- | ---: | ---: | ---: | ---: |
| Legacy 2 / 640x360 | 60 | 0.38 s | included | 105 MiB |
| Grid 2 / 640x360 | 60 | 1.14 s | 0.42 s | 140 MiB |
| Grid 16 / 640x360 | 60 | 4.54 s | 0.75 s | 199 MiB |
| Grid 32 / 640x360 | 60 | 8.40 s | 1.12 s | 265 MiB |
| Grid 16 / 1920x1080 | 30 | 7.87 s | 2.69 s | 818 MiB |
| Grid 32 / 1920x1080 | 30 | 13.81 s | 3.92 s | 1241 MiB |

Reports are in `benchmarks/latest.json` and `benchmarks/hd.json`.

For 32 inputs at 640x360, increasing clip length from 60 to 300 frames
raised peak child memory only from 265 to 272 MiB (about 3%), while total
time rose to 13.80 s. See `benchmarks/longer.json`. This supports bounded
streaming memory for these fixtures; higher-resolution codecs still need more RAM.
