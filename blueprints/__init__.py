# blueprints/__init__.py
from .auth import auth_bp
from .data_fetch import data_fetch_bp
from .dashboard import dashboard_bp

__all__ = ['auth_bp', 'data_fetch_bp', 'dashboard_bp']