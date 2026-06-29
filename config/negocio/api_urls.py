from django.urls import path
from . import api_views

urlpatterns = [
    path('cliente/<str:telefono>/', api_views.api_cliente,          name='api_negocio_cliente'),
    path('clientes/buscar/',        api_views.api_clientes_buscar,  name='api_negocio_clientes_buscar'),
    path('pedido/',                 api_views.api_pedido_create,    name='api_negocio_pedido_create'),
    path('tienda/',                 api_views.api_tienda_create,    name='api_negocio_tienda_create'),
    path('tipos/',                  api_views.api_tipos_list,       name='api_negocio_tipos'),
    path('articulo/buscar/',        api_views.api_articulo_buscar,  name='api_negocio_articulo_buscar'),
    path('codigos/validar/',        api_views.api_codigos_validar,         name='api_negocio_codigos_validar'),
    path('codigos/validar-publico/', api_views.api_codigos_validar_publico, name='api_negocio_codigos_validar_publico'),
]
