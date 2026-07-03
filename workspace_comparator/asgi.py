"""ASGI config for workspace_comparator project."""
import os
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'workspace_comparator.settings')
application = get_asgi_application()
