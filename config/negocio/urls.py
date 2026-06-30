from django.urls import path
from . import views

app_name = 'negocio'

urlpatterns = [
    path('',                                   views.resumen,             name='resumen'),
    path('clientes/',                          views.clientes_list,       name='clientes_list'),
    path('clientes/nuevo/',                    views.cliente_create,      name='cliente_create'),
    path('clientes/<int:pk>/',                 views.cliente_detail,      name='cliente_detail'),
    path('clientes/<int:pk>/editar/',          views.cliente_edit,        name='cliente_edit'),
    path('pedidos/',                           views.pedidos_list,        name='pedidos_list'),
    path('pedidos/nuevo/',                     views.pedido_create,       name='pedido_create'),
    path('pedidos/<int:pk>/',                  views.pedido_detail,       name='pedido_detail'),
    path('pedidos/<int:pk>/pago/',             views.pedido_pago_add,     name='pedido_pago_add'),
    path('items/<int:pk>/editar/',             views.pedido_item_edit,    name='pedido_item_edit'),
    path('gastos/',                            views.gastos_list,         name='gastos_list'),
    path('pos/',                               views.pos,                 name='pos'),
    path('pos/productos/',                     views.pos_productos,       name='pos_productos'),
    path('pos/cobrar/',                        views.pos_cobrar,          name='pos_cobrar'),
    path('api/receipt/<int:pedido_id>/',       views.receipt_print_json,  name='receipt_print_json'),
    path('api/label/<str:sku>/',               views.label_print_json,    name='label_print_json'),
    path('label/<str:sku>/',                   views.label_html,          name='label_html'),
    path('etiquetas/',                         views.etiquetas_list,      name='etiquetas_list'),
    path('etiquetas/print/',                   views.etiquetas_print,     name='etiquetas_print'),
    # TipoArticulo
    path('tipos/',                             views.tipos_list,          name='tipos_list'),
    path('tipos/nuevo/',                       views.tipo_create,         name='tipo_create'),
    path('tipos/<int:pk>/editar/',             views.tipo_edit,           name='tipo_edit'),
    path('tipos/<int:pk>/eliminar/',           views.tipo_delete,         name='tipo_delete'),
    # CodigoDescuento
    path('codigos/',                           views.codigos_list,        name='codigos_list'),
    path('codigos/nuevo/',                     views.codigo_create,       name='codigo_create'),
    path('codigos/<int:pk>/editar/',           views.codigo_edit,         name='codigo_edit'),
    path('codigos/<int:pk>/eliminar/',         views.codigo_delete,       name='codigo_delete'),
]
