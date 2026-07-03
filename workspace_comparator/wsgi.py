"""WSGI config for workspace_comparator project."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'workspace_comparator.settings')
application = get_wsgi_application()
