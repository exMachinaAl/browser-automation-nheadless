"""Geometry, presentation-state and element identity guards for user-authored visual mappings.

All measurements are CSS viewport coordinates; no screen-level clicks or CAPTCHA bypass.
"""
from __future__ import annotations
import hashlib
import json
import math


def capture_signature(state: dict) -> str:
    normalized = {key: state.get(key) for key in ('url','viewport','scroll','presentation','tab_marker')}
    return hashlib.sha256(json.dumps(normalized,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def validate_snapshot(expected: dict, actual: dict, *, elapsed: float, ttl: int = 90) -> None:
    if not expected or elapsed > ttl:
        raise ValueError('snapshot_expired_refresh_required')
    if capture_signature(expected) != capture_signature(actual):
        raise ValueError('snapshot_layout_changed_refresh_required')


def validate_presentation(recorded: dict | None, current: dict) -> None:
    """Strict for v2 mappings; old mappings retain original viewport/position safeguards."""
    if not recorded:
        return
    for key in ('device_pixel_ratio','visual_scale','visual_offset_x','visual_offset_y'):
        if key in recorded:
            a, b = recorded[key], current.get('presentation',{}).get(key)
            if not isinstance(b,(float,int)) or not math.isclose(float(a),float(b),abs_tol=.015):
                raise ValueError('presentation_mismatch_remap_required')
    for key in ('x','y'):
        if key in recorded.get('scroll',{}):
            if abs(recorded['scroll'][key] - current.get('scroll',{}).get(key,1e12)) > 2:
                raise ValueError('scroll_mismatch_remap_required')


def anchor_evidence(inspection:dict, role:str) -> dict:
    # Never save response text (private/changing), and never fingerprint input value.
    return {'tag':inspection['tag'], 'input_type':inspection['input_type'],
            'testid':inspection.get('testid') or '', 'aria_label':inspection.get('aria_label') or '',
            'placeholder':inspection.get('placeholder') or '',
            'button_text': inspection.get('text','')[:80] if role in ('send','action') else '',
            'dom_role':inspection.get('dom_role') or ''}


def check_anchor(expected:dict, actual:dict) -> None:
    for name,value in expected.items():
        if value and (actual.get(name) or '') != value:
            raise ValueError('element_identity_mismatch_remap_required')


def movement_px(a:dict,b:dict)->float:
    return max(abs(a[k]-b[k]) for k in ('x1','y1','x2','y2'))
