from django.urls import path
from . import api_views

urlpatterns = [
    path('cliente/<str:telefono>/', api_views.api_cliente, name='api_negocio_cliente'),
    path('log/',                    api_views.api_log,     name='api_negocio_log'),
]
