"""api/routes/pages.py — Páginas HTML del dashboard."""
from __future__ import annotations

from flask import Blueprint, render_template, make_response

bp = Blueprint('pages', __name__)


@bp.route('/')
def index():
    resp = make_response(render_template('index.html'))
    resp.headers['Cache-Control'] = 'no-store'
    return resp
