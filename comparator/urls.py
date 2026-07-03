from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('api/compare/', views.compare, name='compare'),
    path('api/browse/', views.browse, name='browse'),
    path('file-compare/', views.file_compare, name='file_compare'),
    path('api/file-diff/', views.file_diff, name='file_diff'),
]
