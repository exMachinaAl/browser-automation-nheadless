"""Integration checks for snapshot-bearing mapper API headers and fail-closed inspect."""
import unittest
from unittest.mock import AsyncMock, patch
try:
    import httpx
except ImportError:
    httpx=None

@unittest.skipUnless(httpx,'Requires httpx (requirements-dev.txt)')
class MapperApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from gateway.api import app,TOKEN
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8788')
        self.h={'Authorization':'Bearer '+TOKEN}

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_screenshot_bears_fresh_id_and_css_size(self):
        from gateway.api import engine
        with patch.object(engine,'capture',new_callable=AsyncMock) as capture:
            capture.return_value={'data':b'png-test-fixture','id':'abc123',
                                  'viewport':{'width':960,'height':740},'presentation':{}}
            result=await self.client.get('/api/screenshot',headers=self.h)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.headers['x-bag-snapshot-id'],'abc123')
        self.assertEqual(result.headers['x-bag-viewport-width'],'960')
        self.assertEqual(result.headers['cache-control'],'no-store')

    async def test_inspect_refuses_stale_snapshot_before_dom(self):
        from gateway.api import engine
        with patch.object(engine,'require_snapshot',new_callable=AsyncMock) as require:
            require.side_effect=ValueError('snapshot_missing_refresh_required')
            with patch.object(engine,'inspect',new_callable=AsyncMock) as inspect:
                result=await self.client.post('/api/inspect',headers=self.h,json={
                    'x1':10,'y1':20,'x2':60,'y2':80,'snapshot_id':'expired'})
                inspect.assert_not_awaited()
        self.assertEqual(result.status_code,409)
        self.assertEqual(result.json()['error'],'snapshot_missing_refresh_required')
