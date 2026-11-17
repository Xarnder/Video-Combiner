import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import streamlit as st
from comparison_sheets import find_comparison_videos
from video_grid import probe, layout, render, Cancelled


@st.cache_data(show_spinner=False)
def cached_probe(path, size, modified):
    return probe(path)


def show_grid(folder_picker, selection):
    st.subheader('Multi-video grid')
    folder=folder_picker('Input folder','grid_folder','grid_pick')
    try: names=[p.name for p in find_comparison_videos(folder)]
    except OSError: names=[]
    names=selection('grid_order_'+str(Path(folder).absolute()),'grid_removed_'+str(Path(folder).absolute()),names)
    a,b=st.columns(2)
    width=int(a.number_input('Output width',min_value=2,value=1920,step=2))
    height=int(b.number_input('Output height',min_value=2,value=1080,step=2))
    mode=st.radio('Scaling',['Fill','Fit'],horizontal=True)
    destination=st.text_input('Output MP4 path',str(Path(folder)/'grid.mp4'))
    sources=[]
    try:
        sources=[]
        for name in names:
            path=Path(folder)/name
            stat=path.stat()
            sources.append(cached_probe(str(path),stat.st_size,stat.st_mtime_ns))
        if len({(s['fps'],s['format']) for s in sources}) > 1:
            raise ValueError('Inputs must have matching constant frame rates and container formats')
        if sources:
            st.dataframe(sources)
            tiles=layout(sources,width,height,mode)
            boxes=''.join(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="hsl({i*53%360},40%,35%)" stroke="white"/><text x="{x+w/2}" y="{y+h/2}" text-anchor="middle" fill="white" font-size="{max(width,height)/40}">{i+1}</text>' for i,(x,y,w,h) in enumerate(tiles))
            st.markdown(f'<svg viewBox="0 0 {width} {height}" style="max-height:400px;width:100%">{boxes}</svg>',unsafe_allow_html=True)
    except Exception as exc:
        st.error(str(exc)); sources=[]
    active=st.session_state.get('grid_job')
    if st.button('Render grid',disabled=not sources or bool(active and not active['future'].done())):
        cancel=threading.Event(); state={'cancel':cancel,'event':dict(phase='Starting',current=0,total=1,message='Preparing inputs'),'lock':threading.Lock()}
        executor=ThreadPoolExecutor(max_workers=1)
        def report(event):
            with state['lock']: state['event']=event
        state['future']=executor.submit(render,[s['path'] for s in sources],destination,width,height,mode,cancel,report)
        executor.shutdown(wait=False)
        st.session_state['grid_job']=state
    @st.fragment(run_every=1)
    def monitor():
        job=st.session_state.get('grid_job')
        if not job: return
        if not job['future'].done():
            with job['lock']: event=dict(job['event'])
            st.progress(min(1.0,event['current']/max(1,event['total'])),text=event['phase']+' — '+event['message'])
            if event.get('eta') is not None:
                st.caption(f"Elapsed: {event['elapsed']:.0f}s · Estimated remaining: {event['eta']:.0f}s")
            if st.button('Cancel rendering',disabled=job['cancel'].is_set()):
                job['cancel'].set()
            if job['cancel'].is_set(): st.info('Stopping and removing partial output…')
        else:
            try:
                path=job['future'].result()
                st.success('Saved '+path)
                st.caption('The completed video is saved at the path above.')
                if Path(path).stat().st_size <= 200*1024*1024:
                    st.video(path)
                    with open(path,'rb') as data: st.download_button('Download grid',data,file_name=Path(path).name)
                else:
                    st.info('Large output saved to disk. Browser download is disabled above 200 MB to avoid loading the entire video into server memory.')
            except Cancelled: st.info('Rendering cancelled. Partial output removed.')
            except Exception as exc: st.error(str(exc))
    monitor()
