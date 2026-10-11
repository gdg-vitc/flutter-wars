"""Application imports must be safe to execute in the deployment snapshot."""

import os
import subprocess
import sys


def test_factory_import_needs_no_bindings_or_database():
    env = dict(os.environ)
    for key in ("APP_NAME", "DATABASE_URL", "JWT_SECRET_KEY"):
        env.pop(key, None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from unittest.mock import patch
from app.core.config import Settings
from app.core import db
with patch.object(Settings, '__init__', side_effect=AssertionError('settings read during import')):
    with patch.object(db, 'configure_database', side_effect=AssertionError('database configured during import')):
        from app.factory import create_app, build_app
        app = build_app()
        schema = app.openapi()
        app.middleware_stack = app.build_middleware_stack()
assert callable(create_app)
assert not db.engine_configured()
assert '/auth/refresh' in schema['paths']
assert '/market/rounds/current' in schema['paths']
assert not hasattr(app.state, 'settings')
""",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
