"""Opt-in real pipeline benchmark; no extra Python dependencies on Windows.
Run: python benchmarks/grid_benchmark.py --counts 2 16 32 --frames 60
Peak child working set includes the compositor's decoders and encoder, excludes UI.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from video_grid import render
from concat_videos import process_side_by_side_pair


class Counters(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]


def measured(operation):
    peak=[0]
    original=subprocess.Popen
    class Tracked(original):
        def wait(self,*args,**kwargs):
            result=super().wait(*args,**kwargs)
            if os.name=='nt':
                counters=Counters(); counters.cb=ctypes.sizeof(counters)
                api=ctypes.WinDLL('psapi').GetProcessMemoryInfo
                api.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
                if api(wintypes.HANDLE(int(self._handle)),ctypes.byref(counters),counters.cb):
                    peak[0]=max(peak[0],counters.PeakWorkingSetSize)
            return result
    started=time.perf_counter()
    with patch('subprocess.Popen',Tracked): operation()
    return dict(seconds=round(time.perf_counter()-started,3),peak_child_mib=round(peak[0]/1024**2,2) if peak[0] else None)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--counts',nargs='+',type=int,default=[2,16,32]); parser.add_argument('--frames',type=int,default=60); parser.add_argument('--source-size',default='640x360'); parser.add_argument('--report',default='benchmarks/latest.json'); args=parser.parse_args()
    results=[]
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory); source=root/'source.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'testsrc2=s={args.source_size}:r=30','-frames:v',str(args.frames),'-c:v','libx264','-threads','2',str(source)],check=True)
        w,h=map(int,args.source_size.split('x'))
        def legacy():
            ok,_=process_side_by_side_pair(str(source),str(source),str(root/'legacy.mp4'),layout='horizontal')
            if not ok: raise RuntimeError('Legacy benchmark failed')
        results.append(dict(name='legacy-two',**measured(legacy)))
        for count in args.counts:
            output=root/f'grid-{count}.mp4'
            phases={}
            def progress(event): phases.setdefault(event['phase'],time.perf_counter())
            result=measured(lambda: render([str(source)]*count,output,w*2,h,'Fill',threading.Event(),progress))
            result.update(name=f'grid-{count}',output_bytes=output.stat().st_size)
            if 'Encoding' in phases and 'Verifying' in phases:
                result['encoding_seconds']=round(phases['Verifying']-phases['Encoding'],3)
            results.append(result)
            print(json.dumps(results[-1]),flush=True)
    report=dict(source_size=args.source_size,frames=args.frames,fps=30,output_size=[w*2,h],results=results,
                note='Legacy: ultrafast lossless, no frame audit. Grid: medium CRF18 plus input/output audits. Not equivalent encoder settings. Repeated source opened as independent inputs. Peak child RSS is measured on Windows; null elsewhere.')
    Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
if __name__=='__main__': main()
