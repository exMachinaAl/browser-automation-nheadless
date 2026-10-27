"""One allowlisted origin -> selectors; profiles never widen navigation permissions."""
import json
from urllib.parse import urlsplit

SELECTORS = frozenset(('input', 'send', 'response', 'ready'))


def validate_profile(payload, allowed):
    if not isinstance(payload, dict):
        raise ValueError('invalid_site_profile')
    url = payload.get('origin')
    from .core import origin
    if not isinstance(url, str) or url != origin(url) or url not in allowed:
        raise ValueError('site_profile_origin_not_allowed')
    name = payload.get('name')
    if not isinstance(name, str) or not (1 <= len(name.strip()) <= 80):
        raise ValueError('invalid_site_profile_name')
    selectors = payload.get('selectors')
    if not isinstance(selectors, dict) or not selectors or any(key not in SELECTORS for key in selectors):
        raise ValueError('invalid_site_profile_selectors')
    if any(not isinstance(v, str) or len(v)>512 or not v.strip() for v in selectors.values()):
        raise ValueError('invalid_site_profile_selector')
    return {'origin':url,'name':name.strip(),'selectors':selectors}
