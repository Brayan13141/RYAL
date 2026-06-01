from django.urls import path
from . import views

app_name = 'negocio'

urlpatterns = [
    path('',                             views.resumen,         name='resumen'),
    path('clientes/',                    views.clientes_list,   name='clientes_list'),
    path('clientes/nuevo/',              views.cliente_create,  name='cliente_create'),
    path('clientes/<int:pk>/',           views.cliente_detail,  name='cliente_detail'),
    path('clientes/<int:pk>/editar/',    views.cliente_edit,    name='cliente_edit'),
    path('pedidos/',                     views.pedidos_list,    name='pedidos_list'),
    path('pedidos/nuevo/',               views.pedido_create,   name='pedido_create'),
    path('pedidos/<int:pk>/',            views.pedido_detail,   name='pedido_detail'),
    path('pedidos/<int:pk>/pago/',       views.pedido_pago_add, name='pedido_pago_add'),
    path('gastos/',                      views.gastos_list,     name='gastos_list'),
]
