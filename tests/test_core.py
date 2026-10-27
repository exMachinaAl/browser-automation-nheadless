import tempfile
import unittest
from pathlib import Path
from gateway.core import Store, valid_rect, origin, overlap

class CoreTests(unittest.TestCase):
    def test_origin_normalization(self):
        self.assertEqual(origin('https://Example.COM:443/chat'), 'https://example.com')
        self.assertEqual(origin('http://127.0.0.1:8787/'), 'http://127.0.0.1:8787')
        self.assertEqual(origin('file:///etc/passwd'),'')
        self.assertEqual(origin('javascript:alert(1)'),'')
        self.assertEqual(origin('https://user:pass@example.com'),'')

    def test_rect_guard(self):
        self.assertTrue(valid_rect(dict(x1=30,y1=40,x2=80,y2=100),1280,900))
        self.assertFalse(valid_rect(dict(x1=30,y1=40,x2=20,y2=100),1280,900))
        self.assertFalse(valid_rect(dict(x1=30,y1=40,x2=1281,y2=100),1280,900))
        self.assertFalse(valid_rect(dict(x1=30,y1=40,x2=31,y2=100),1280,900))

    def test_overlap(self):
        self.assertEqual(overlap(dict(x1=0,y1=0,x2=10,y2=10),dict(x1=5,y1=5,x2=15,y2=15)),25)

    def test_recorder_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(Path(tmp)/'db.sqlite')
            self.assertTrue(s.change('role:response','Halo','one'))
            self.assertFalse(s.change('role:response','Halo','one'))
            self.assertTrue(s.change('role:response','Halo dunia','one'))
            self.assertEqual(len(s.changes()),2)
            s.db.close()

if __name__=='__main__':unittest.main()
