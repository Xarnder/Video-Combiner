"""Run explicitly: requires local sockets for Streamlit's event loop."""
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_grid_pipeline import fixture
from streamlit.testing.v1 import AppTest
from video_grid import Cancelled


def button(app,label):
    return next(b for b in app.button if b.label==label)


with tempfile.TemporaryDirectory() as directory:
    source=Path(directory)/'source.mp4'; fixture(source)
    script=f'''
from grid_ui import show_grid
show_grid(lambda *args: {directory!r}, lambda *args: ['source.mp4'])
'''
    app=AppTest.from_string(script,default_timeout=10).run()
    assert not app.exception,app.exception
    def slow(paths,destination,width,height,mode,cancel,progress):
        progress(dict(phase='Encoding',current=1,total=8,elapsed=1,eta=7,message='1 / 8 frames'))
        cancel.wait(10)
        if cancel.is_set(): raise Cancelled('Cancelled')
        raise RuntimeError('Test failed to cancel')
    with patch('grid_ui.render',side_effect=slow):
        button(app,'Render grid').click().run()
        assert not app.exception,app.exception
        assert app.progress
        button(app,'Cancel rendering').click().run()
        time.sleep(.1)
        app.run()
        assert any('cancelled' in info.value for info in app.info)
    app.number_input[0].set_value(128)
    app.number_input[1].set_value(64)
    button(app,'Render grid').click().run()
    deadline=time.monotonic()+15
    while time.monotonic()<deadline and not app.success:
        time.sleep(.2); app.run()
    assert not app.exception,app.exception
    assert app.success,app.error
    assert (Path(directory)/'grid.mp4').exists()
print('Grid UI checks passed: metadata, progress, cancellation, completion')

app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'streamlit_app.py'),default_timeout=15).run()
assert not app.exception,app.exception
for mode in ('sidebyside','comparison','grid','normal'):
    app.sidebar.radio[0].set_value(mode).run()
    assert not app.exception,app.exception
print('Main app mode-switch regression checks passed')
