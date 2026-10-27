"""Owned Chromium lifecycle, deterministic active tab and isolated local profiles.

External CDP browsers are attached, never launched/killed by BAG. No stealth or anti-bot bypass.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from playwright.async_api import async_playwright

PROFILE_NAME = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')


def profile_dir(data: Path, name: str) -> Path:
    if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
        raise ValueError('invalid_browser_profile_name')
    if name == 'default':  # Backwards compatible with v0.4.1 data/browser-profile.
        folder = data / 'browser-profile'
    else:
        folder = data / 'browser-profiles' / name
    if folder.is_symlink() or folder.parent.is_symlink():
        raise ValueError('browser_profile_symlink_not_allowed')
    return folder


class BrowserManager:
    def __init__(self, cfg, data: Path, route):
        self.cfg, self.data, self.route = cfg, data, route
        self.pw = self.browser = self.context = self.page = None
        self.profile = 'default'
        self.state = 'stopped'
        self.failure = None
        self._tabs = {}  # ephemeral opaque ID -> Page; no cookies/passwords exposed

    def _track(self, page):
        if page not in self._tabs.values():
            self._tabs[uuid.uuid4().hex] = page

    def _prune(self):
        self._tabs = {key: page for key, page in self._tabs.items() if not page.is_closed()}

    async def start(self, profile='default'):
        if self.state not in ('stopped', 'failed'):
            raise ValueError('browser_already_started')
        folder = profile_dir(self.data, profile)
        self.state, self.failure, self.profile = 'starting', None, profile
        try:
            self.pw = await async_playwright().start()
            if self.cfg['cdp_endpoint']:
                if profile != 'default':
                    raise ValueError('remote_cdp_cannot_switch_profile')
                self.browser = await self.pw.chromium.connect_over_cdp(self.cfg['cdp_endpoint'])
                if not self.browser.contexts:
                    raise ValueError('cdp_context_missing')
                self.context = self.browser.contexts[0]
            else:
                options = dict(headless=self.cfg['headless'], viewport=self.cfg['viewport'],
                               accept_downloads=False, device_scale_factor=1)
                exe = self.cfg['chromium_executable'].strip()
                if exe:
                    options['executable_path'] = exe
                folder.parent.mkdir(parents=True, exist_ok=True)
                self.context = await self.pw.chromium.launch_persistent_context(str(folder), **options)
            # v0.6.1: Chromium must behave as a normal interactive browser.
            # Do NOT register an all-navigation route. Bot controls stay allowlisted.
            self.context.on('page', self._track)
            for p in self.context.pages:
                self._track(p)
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self._track(self.page)
            self.state = 'running'
            return self.page
        except Exception as exc:
            self.failure = type(exc).__name__
            await self.stop(failed=True)
            raise

    async def stop(self, failed=False):
        self.state = 'stopping'
        cleanup_failed = False
        try:
            if self.context and not self.cfg['cdp_endpoint']:
                await self.context.close()
            if self.browser and self.cfg['cdp_endpoint']:
                # Disconnect from remote Chrome; never send process termination.
                await self.browser.close()
        except Exception as exc:
            cleanup_failed = True
            self.failure = type(exc).__name__
        finally:
            try:
                if self.pw:
                    await self.pw.stop()
            except Exception as exc:
                cleanup_failed = True
                self.failure = type(exc).__name__
            finally:
                self.pw = self.browser = self.context = self.page = None
                self._tabs.clear()
                self.state = 'failed' if failed or cleanup_failed else 'stopped'

    def selected(self):
        if self.state != 'running' or not self.page or self.page.is_closed():
            raise ValueError('browser_page_unavailable')
        return self.page

    def tabs(self):
        self._prune()
        return [{'id': key, 'selected': self.page is page,
                 'url': page.url if page.url.startswith(('http://', 'https://')) else 'internal',
                 'closed': False} for key, page in self._tabs.items()]

    async def new_tab(self, url, allow):
        if not allow(url):
            raise ValueError('origin_not_allowed')
        self.selected()
        page = await self.context.new_page()
        self._track(page)
        try:
            await page.goto(url, wait_until='domcontentloaded', timeout=25000)
            if not allow(page.url):
                raise ValueError('unexpected_origin')
            self.page = page
            return self.tabs()
        except Exception:
            await page.close()
            self._prune()
            raise

    async def browse_tab(self, url):
        """User-requested visual browsing, not a grant to automate the destination."""
        from .security import valid_browse_url
        if not valid_browse_url(url):
            raise ValueError('invalid_browse_url')
        self.selected()
        page = await self.context.new_page()
        self._track(page)
        self.page = page
        navigation_error = None
        try:
            await page.goto(url, wait_until='commit', timeout=12000)
        except Exception as exc:
            # Keep the tab open so the user can inspect network/certificate errors.
            from .security import navigation_message
            navigation_error = navigation_message(exc)
        return {'tabs':self.tabs(), 'active_url':page.url,
                'navigation_error':navigation_error, 'automation_authorized':False}

    def select_tab(self, tab_id, allow):
        self._prune()
        page = self._tabs.get(tab_id)
        if not page:
            raise ValueError('unknown_browser_tab')
        if not allow(page.url):
            raise ValueError('tab_origin_not_allowed')
        self.page = page
        return self.tabs()

    async def close_tab(self, tab_id, allow):
        self._prune()
        page = self._tabs.get(tab_id)
        if not page:
            raise ValueError('unknown_browser_tab')
        if page is self.page:
            alternatives = [p for p in self._tabs.values() if p is not page and allow(p.url)]
            if not alternatives:
                raise ValueError('cannot_close_last_authorized_tab')
            self.page = alternatives[0]
        await page.close()
        self._prune()
        return self.tabs()

    def profiles(self):
        names = ['default']
        root = self.data / 'browser-profiles'
        if root.is_dir():
            names += sorted(p.name for p in root.iterdir()
                            if p.is_dir() and not p.is_symlink() and PROFILE_NAME.fullmatch(p.name))
        return {'active': self.profile, 'profiles': sorted(set(names)), 'remote': bool(self.cfg['cdp_endpoint'])}
