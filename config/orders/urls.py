from django.urls import path
from . import views

app_name = 'orders'

urlpatterns = [
    path('cart/get/',             views.cart_get,          name='cart_get'),
    path('cart/add/',             views.cart_add,           name='cart_add'),
    path('cart/remove/',          views.cart_remove,        name='cart_remove'),
    path('cart/update/',          views.cart_update,        name='cart_update'),
    path('checkout/',             views.checkout,           name='checkout'),
    path('checkout/confirm/',     views.checkout_confirm,   name='checkout_confirm'),
    path('pedido/<uuid:token>/',  views.order_confirmation, name='confirmation'),
    path('rastrear/',             views.order_track,        name='order_track'),
    path('mis-pedidos/',          views.my_orders,          name='my_orders'),
]
