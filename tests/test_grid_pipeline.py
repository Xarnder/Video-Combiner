"""Small real FFmpeg fixtures: pixel identities verify frame indices, not just metadata."""
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from video_grid import Cancelled, _run, audit, layout, probe, render


def fixture(path, count=8, fps='10', offset=0, width=64, height=64):
    # Every frame has a distinct grey level; lossless source preserves exact values.
    raw=b''.join(bytes([20+i*20])*(width*height*3) for i in range(count))
    subprocess.run(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24',
                    '-s',f'{width}x{height}','-r',fps,'-i','pipe:0',
                    '-vf',f'setpts=PTS+{offset}/TB','-fps_mode','passthrough',
                    '-c:v','libx264','-crf','0','-pix_fmt','yuv444p',str(path)],input=raw,check=True)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.folder=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()
    def inputs(self, fps='10'):
        paths=[self.folder/'a.mp4',self.folder/'b.mp4']
        fixture(paths[0],8,fps,offset=2)
        fixture(paths[1],3,fps,offset=5)
        return paths
    def test_alignment_hold_rational_fps_and_progress(self):
        for fps in ('10','30000/1001'):
            paths=self.inputs(fps)
            output=self.folder/f'out-{fps.replace("/","-")}.mp4'
            events=[]
            render(paths,output,128,64,'Fill',threading.Event(),events.append)
            sources=[probe(p) for p in paths]
            tiles=layout(sources,128,64)
            data=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
            self.assertEqual(len(data),8*128*64*3)
            for frame in range(8):
                for i,(x,y,w,h) in enumerate(tiles):
                    pixel=(frame*128*64+(y+h//2)*128+x+w//2)*3
                    self.assertAlmostEqual(data[pixel],20+min(frame,7 if i==0 else 2)*20,delta=4)
            encoding=[e for e in events if e['phase']=='Encoding']
            self.assertEqual(encoding[-1]['current'],8)
            self.assertEqual(events[-1]['phase'],'Complete')
    def test_cancellation_each_phase_and_cleanup(self):
        paths=self.inputs()
        for phase in ('Inspecting','Validating inputs','Encoding','Verifying'):
            cancel=threading.Event()
            def report(event):
                if event['phase']==phase: cancel.set()
            with self.assertRaises(Cancelled):
                render(paths,self.folder/'cancel.mp4',128,64,'Fit',cancel,report)
            self.assertFalse((self.folder/'cancel.mp4').exists())
            self.assertFalse(list(self.folder.glob('.grid-*')))
    def test_cancel_silent_child_is_prompt(self):
        import sys,time
        event=threading.Event()
        timer=threading.Timer(.2,event.set); timer.start()
        started=time.monotonic()
        try:
            with self.assertRaises(Cancelled):
                _run([sys.executable,'-c','import time; time.sleep(30)'],event)
        finally: timer.join()
        self.assertLess(time.monotonic()-started,4)
    def test_mismatch_corruption_and_failure_cleanup(self):
        paths=self.inputs()
        fixture(paths[1],fps='12')
        with self.assertRaisesRegex(ValueError,'matching'):
            render(paths,self.folder/'bad.mp4',128,64,'Fill',threading.Event())
        paths[1].write_text('corrupt')
        with self.assertRaises(RuntimeError): probe(paths[1])
        paths=self.inputs()
        original=_run
        def fail(command,*args,**kwargs):
            if '-progress' in command: raise RuntimeError('Encoder failed')
            return original(command,*args,**kwargs)
        with patch('video_grid._run',side_effect=fail), self.assertRaisesRegex(RuntimeError,'Encoder failed'):
            render(paths,self.folder/'bad.mp4',128,64,'Fill',threading.Event())
        self.assertFalse(list(self.folder.glob('.grid-*')))
        self.assertFalse((self.folder/'bad.mp4').exists())
    def test_single_odd_dimension_source(self):
        source=self.folder/'odd.mp4'; fixture(source,1,width=65,height=63)
        for mode in ('Fit','Fill'):
            result=render([source],self.folder/f'{mode}.mp4',128,128,mode,threading.Event())
            self.assertEqual(audit(probe(result),threading.Event()),1)
    def test_variable_timing_rejected(self):
        source=self.folder/'vfr.mkv'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=s=64x64:r=10',
                        '-frames:v','8','-vf',"setpts='if(lt(N,4),N,N+1)/(10*TB)'",'-fps_mode','vfr',str(source)],check=True)
        with self.assertRaises(ValueError): audit(probe(source),threading.Event())


    def test_cancel_running_encoder_and_sixteen_inputs(self):
        source=self.folder/'source.mp4'; fixture(source,8)
        events=[]
        result=render([source]*16,self.folder/'sixteen.mp4',256,256,'Fill',threading.Event(),events.append)
        self.assertEqual(audit(probe(result),threading.Event()),8)
        cancel=threading.Event()
        def report(event):
            if event['phase']=='Encoding' and event['current']>0: cancel.set()
        with self.assertRaises(Cancelled):
            render([source]*16,self.folder/'stopped.mp4',256,256,'Fill',cancel,report)
        self.assertFalse((self.folder/'stopped.mp4').exists())
        self.assertFalse(list(self.folder.glob('.grid-*')))

    def test_fit_letterbox_and_fill(self):
        source=self.folder/'wide.mp4'; fixture(source,1,width=64,height=32)
        for mode in ('Fill','Fit'):
            output=render([source],self.folder/f'wide-{mode}.mp4',64,64,mode,threading.Event())
            data=subprocess.check_output(['ffmpeg','-v','error','-i',output,'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'])
            self.assertAlmostEqual(data[(32*64+32)*3],20,delta=3)
            self.assertAlmostEqual(data[0],0 if mode=='Fit' else 20,delta=3)

    def test_matroska_timestamp_quantisation(self):
        source=self.folder/'rational.mkv'; fixture(source,8,'30000/1001')
        output=render([source],self.folder/'rational.mp4',128,64,'Fit',threading.Event())
        self.assertEqual(audit(probe(output),threading.Event()),8)
