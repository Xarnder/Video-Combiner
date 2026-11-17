"""Deterministic layouts and streaming, frame-index-synchronised FFmpeg grids."""
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time
from fractions import Fraction


def probe(path, cancel=None):
    import threading
    cancel = cancel or threading.Event()
    data = json.loads(_run(['ffprobe','-v','error','-select_streams','v:0',
                           '-show_streams','-show_format','-of','json',str(path)], cancel))
    if not data.get('streams'):
        raise ValueError(f'{path}: no readable video stream')
    s = data['streams'][0]
    fps = Fraction(s['r_frame_rate'])
    if fps <= 0 or Fraction(s['avg_frame_rate']) != fps:
        raise ValueError(f'{path}: variable or invalid frame rate; convert to constant frame rate first')
    if s.get('sample_aspect_ratio', '1:1') not in ('1:1', 'N/A') or any(d.get('rotation', 0) for d in s.get('side_data_list', [])):
        raise ValueError(f'{path}: normalise rotation and pixel aspect ratio before combining')
    if s['width'] <= 0 or s['height'] <= 0:
        raise ValueError(f'{path}: invalid video dimensions')
    return dict(path=str(path), width=s['width'], height=s['height'], fps=str(fps),
                format=data['format']['format_name'], time_base=s.get('time_base','1/1000000'),
                declared_frames=s.get('nb_frames'), duration=float(s.get('duration', data['format'].get('duration', 0))))


def layout(sources, width, height, mode='Fill', cancel=None):
    if not sources or width < 2 or height < 2 or width % 2 or height % 2:
        raise ValueError('H.264 output requires positive even dimensions and at least one input')
    if mode not in ("Fill", "Fit"): raise ValueError("Scaling must be Fill or Fit")
    best = None
    # Evaluate both row and column partitions, including unequal tile sizes.
    for transpose in (False, True):
        W,H = (height,width) if transpose else (width,height)
        ratios = [(s['height']/s['width'] if transpose else s['width']/s['height']) for s in sources]
        for count in range(1, len(sources)+1):
            if cancel is not None and cancel.is_set(): raise Cancelled("Cancelled")
            groups = [list(range(i*len(sources)//count, (i+1)*len(sources)//count)) for i in range(count)]
            weights = [1/sum(ratios[i] for i in g) for g in groups]
            tiles=[]; y=0
            weight_total=sum(weights)
            weight_used=0
            for row,g in enumerate(groups):
                weight_used += weights[row]
                end_y = H if row==count-1 else 2*round(H*weight_used/weight_total/2)
                x=0
                ratio_total=sum(ratios[i] for i in g)
                ratio_used=0
                for col,i in enumerate(g):
                    ratio_used += ratios[i]
                    end_x = W if col==len(g)-1 else 2*round(W*ratio_used/ratio_total/2)
                    tile=(y,x,end_y-y,end_x-x) if transpose else (x,y,end_x-x,end_y-y)
                    tiles.append(tile); x=end_x
                y=end_y
            if any(min(t[2:])<2 for t in tiles): continue
            cost=0
            areas=[]
            for s,(_,_,w,h) in zip(sources,tiles):
                ratio=(w/h)/(s['width']/s['height'])
                retained=min(ratio,1/ratio)
                scale=(max if mode=='Fill' else min)(w/s['width'],h/s['height'])
                cost += (1-retained)*10 + max(0,math.log2(scale))*.2
                areas.append(w*h)
            mean=sum(areas)/len(areas)
            cost += .15*sum(abs(a/mean-1) for a in areas)
            if best is None or cost<best[0]: best=(cost,tiles)
    if best is None: raise ValueError('Output is too small to allocate every input a tile')
    return best[1]


class Cancelled(RuntimeError):
    """A user-requested stop, after the child process has been reaped."""


def _run(command, cancel, on_line=None):
    """Drain stdout with bounded buffering; poll cancellation even during silent work."""
    import queue
    import threading
    from collections import deque
    if cancel.is_set():
        raise Cancelled('Cancelled')
    lines = queue.Queue(maxsize=128)
    stopped = threading.Event()
    errors = deque(maxlen=40)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', errors='replace',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    def read_output():
        for line in process.stdout:
            while not stopped.is_set():
                try:
                    lines.put(line, timeout=.05)
                    break
                except queue.Full:
                    pass
            if stopped.is_set(): break
    def read_errors():
        for line in process.stderr:
            errors.append(line)
    readers = [threading.Thread(target=read_output), threading.Thread(target=read_errors)]
    for reader in readers: reader.start()
    collected = []
    try:
        while readers[0].is_alive() or not lines.empty() or process.poll() is None:
            if cancel.is_set(): raise Cancelled('Cancelled')
            try:
                line = lines.get(timeout=.05)
            except queue.Empty:
                continue
            if on_line: on_line(line)
            else: collected.append(line)
        process.wait()
        readers[1].join()
        if process.returncode or errors:
            raise RuntimeError(''.join(errors)[-4000:] or
                               'FFmpeg failed; check codec support, free memory and disk space')
        if cancel.is_set(): raise Cancelled('Cancelled')
        return ''.join(collected)
    finally:
        stopped.set()
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for reader in readers: reader.join()
        process.stdout.close()
        process.stderr.close()


def audit(source, cancel, report=lambda value: None):
    """Count decoded frames and validate cadence using constant memory."""
    fps = Fraction(source['fps'])
    first = previous = None
    count = 0
    tolerance = max(Fraction(source.get("time_base", "1/1000000")), Fraction(1,100000))
    def frame(line):
        nonlocal first, previous, count
        value = line.strip().split(',')[0]
        if not value: return  # ffprobe can emit a blank side-data record
        try: timestamp = Fraction(value)
        except (ValueError, ZeroDivisionError):
            raise ValueError(source['path'] + ': missing frame timestamps')
        if first is None: first = timestamp
        # Compare to frame index, not just adjacent deltas (detect cumulative drift).
        if (previous is not None and timestamp <= previous) or abs(timestamp-first-Fraction(count, 1)/fps) > tolerance:
            raise ValueError(source['path'] + ': irregular frame timing; convert to constant frame rate first')
        previous = timestamp
        count += 1
        if count % 256 == 0: report(count)
    _run(['ffprobe','-v','error','-threads','1','-select_streams','v:0',
          '-show_entries','frame=best_effort_timestamp_time','-of','csv=p=0',source['path']],cancel,frame)
    if not count: raise ValueError(source['path'] + ': no decodable frames')
    declared = source.get('declared_frames')
    if declared not in (None, 'N/A', '0') and int(declared) != count:
        raise ValueError(source['path'] + ': decoded frame count differs from container metadata; input may be incomplete')
    return count


def render(paths, destination, width, height, mode, cancel, progress=lambda event: None):
    if mode not in ('Fill', 'Fit'): raise ValueError('Scaling must be Fill or Fit')
    destination = Path(destination).absolute()
    if destination.exists(): raise ValueError('Output exists; choose a new filename')
    if not paths: raise ValueError('Select at least one input')
    sources = []
    for i, path in enumerate(paths):
        if cancel.is_set(): raise Cancelled('Cancelled')
        progress(dict(phase='Inspecting', current=i, total=len(paths), message=Path(path).name))
        sources.append(probe(path, cancel))
    if len({(s['fps'],s['format']) for s in sources}) != 1:
        raise ValueError('Inputs must have matching constant frame rates and container formats')
    tiles = layout(sources,width,height,mode,cancel)
    fps = Fraction(sources[0]['fps'])
    counts = []
    for i, source in enumerate(sources):
        def report(count=0):
            progress(dict(phase='Validating inputs', current=i, total=len(sources),
                          message=f"{Path(source['path']).name}: {count} frames checked"))
        report()
        counts.append(audit(source,cancel,report))
    target = max(counts)
    destination.parent.mkdir(parents=True,exist_ok=True)
    # All intermediates are bounded metadata, a filter script, and the encoded output.
    with tempfile.TemporaryDirectory(prefix='.grid-',dir=destination.parent) as temporary:
        output = str(Path(temporary)/'output.mp4')
        command = ['ffmpeg','-v','error','-xerror','-y','-nostdin',
                   '-filter_complex_threads','1','-stats_period','0.2','-progress','pipe:1']
        filters = []
        for i,(source,(_,_,w,h)) in enumerate(zip(sources,tiles)):
            command += ['-threads','1','-err_detect','explode','-i',source['path']]
            fit = mode == 'Fit'
            # Explicit dimensions preserve the source ratio to within one pixel.
            scale = (min if fit else max)(w/source['width'], h/source['height'])
            rounding = math.floor if fit else math.ceil
            sw = max(1,rounding(source['width']*scale))
            sh = max(1,rounding(source['height']*scale))
            sizing = f'scale={sw}:{sh}:flags=lanczos,format=yuv444p,'
            sizing += f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2' if fit else f'crop={w}:{h}:exact=1'
            filters.append(f'[{i}:v:0]settb={fps.denominator}/{fps.numerator},setpts=N,{sizing},setsar=1,tpad=stop_mode=clone:stop={target-counts[i]}[v{i}]')
        if len(sources)>1:
            filters.append(''.join(f'[v{i}]' for i in range(len(sources)))+
                           f'xstack=inputs={len(sources)}:layout='+
                           '|'.join(f'{x}_{y}' for x,y,w,h in tiles)+':shortest=1[out]')
        else: filters.append('[v0]null[out]')
        graph = Path(temporary)/'filters.txt'
        graph.write_text(';'.join(filters),encoding='utf-8')
        command += ['-filter_complex_script',str(graph),'-map','[out]','-an','-frames:v',str(target),
                    '-fps_mode','passthrough','-enc_time_base',f'{fps.denominator}/{fps.numerator}',
                    '-c:v','libx264','-threads','2','-crf','18','-preset','medium',
                    '-pix_fmt','yuv420p','-movflags','+faststart',output]
        started = time.monotonic()
        progress(dict(phase='Encoding',current=0,total=target,message='Starting encoder'))
        def encoding(line):
            key,_,value = line.strip().partition('=')
            if key == 'frame':
                current = min(int(value),target)
                elapsed = time.monotonic()-started
                progress(dict(phase='Encoding',current=current,total=target,
                              elapsed=elapsed,eta=elapsed*(target-current)/current if current else None,
                              message=f'{current} / {target} frames'))
        _run(command,cancel,encoding)
        progress(dict(phase='Verifying',current=0,total=target,message='Checking output frame count and timing'))
        verified = probe(output,cancel)
        actual = audit(verified,cancel,lambda count: progress(dict(phase='Verifying',current=count,total=target,message='Checking output')))
        if (verified['width'],verified['height'],Fraction(verified['fps']),actual) != (width,height,fps,target):
            raise RuntimeError('Output verification failed: dimensions, frame rate or count differ')
        if cancel.is_set(): raise Cancelled('Cancelled')
        # Exclusive creation prevents overwriting an output created during encoding.
        if os.name == "nt":
            os.rename(output,destination)  # Windows rename refuses existing destinations.
        else:
            os.link(output,destination)
    progress(dict(phase='Complete',current=target,total=target,message=str(destination)))
    return str(destination)
