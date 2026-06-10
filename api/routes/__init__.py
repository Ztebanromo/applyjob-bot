"""api/routes — Blueprints Flask agrupados por dominio."""
from __future__ import annotations

from api.app import app

from . import pages, bot_control, config, stats, sessions, quick_links, qa


def register_blueprints():
    app.register_blueprint(pages.bp)
    app.register_blueprint(bot_control.bp)
    app.register_blueprint(config.bp)
    app.register_blueprint(stats.bp)
    app.register_blueprint(sessions.bp)
    app.register_blueprint(quick_links.bp)
    app.register_blueprint(qa.bp)
