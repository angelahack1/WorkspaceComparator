"""URL configuration for workspace_comparator project."""
from django.urls import path, include

urlpatterns = [
    path('', include('comparator.urls')),
]
