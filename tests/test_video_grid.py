import unittest
from video_grid import layout
class GridTests(unittest.TestCase):
    def test_geometry(self):
        for n in (1,2,3,4,5,6,9,12,16):
            sources=[dict(width=w,height=h) for w,h in [(511,513),(1920,1080),(512,1024)]*(n//3+1)][:n]
            for width,height in ((1920,1080),(2160,3840)):
                tiles=layout(sources,width,height)
                self.assertEqual(len(tiles),n)
                self.assertEqual(sum(w*h for x,y,w,h in tiles),width*height)
                for i,(x,y,w,h) in enumerate(tiles):
                    self.assertTrue(x>=0 and y>=0 and x+w<=width and y+h<=height)
                    for X,Y,W,H in tiles[i+1:]:
                        self.assertTrue(x+w<=X or X+W<=x or y+h<=Y or Y+H<=y)
                self.assertEqual(tiles,layout(sources,width,height))
