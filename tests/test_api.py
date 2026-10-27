"""No-browser HTTP security tests, use loopback host like production."""
import asyncio
import unittest

try:
    import httpx
except ImportError:
    httpx = None


@unittest.skipIf(httpx is None, 'Install requirements-dev.txt for HTTP tests')
class ApiSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from gateway.api import app, TOKEN
        self.token = TOKEN
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8788')

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_health_is_loopback_only(self):
        r = await self.client.get('/health')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['version'], '0.6.1')
        evil = await self.client.get('/health',headers={'Host':'localhost:8788@evil.test'})
        self.assertEqual(evil.status_code,403)

    async def test_bearer_token_required(self):
        self.assertEqual((await self.client.get('/api/maps')).status_code, 401)
        self.assertEqual((await self.client.get('/api/maps',headers={'Authorization':'Bearer invalid'})).status_code,401)
        good=await self.client.get('/api/maps',headers={'Authorization':'Bearer '+self.token})
        self.assertEqual(good.status_code,200)

    async def test_web_origin_restriction(self):
        h={'Origin':'https://untrusted.example','Authorization':'Bearer '+self.token}
        r=await self.client.get('/api/maps',headers=h)
        self.assertEqual(r.status_code,403)
        h['Origin']='http://127.0.0.1:8788'
        r=await self.client.get('/api/maps',headers=h)
        self.assertEqual(r.status_code,200)


if __name__ == '__main__':
    unittest.main()
