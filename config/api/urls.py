from django.urls import path
from api.views import auth, catalog, cart, orders, accounts

app_name = 'api'

urlpatterns = [
    # Auth
    path('auth/me/',      auth.me,          name='auth-me'),
    path('auth/login/',   auth.login_view,  name='auth-login'),
    path('auth/signup/',  auth.signup_view, name='auth-signup'),
    path('auth/logout/',  auth.logout_view, name='auth-logout'),

    # Catalog
    path('catalog/home/',                                    catalog.home,           name='catalog-home'),
    path('catalog/categories/',                              catalog.categories,     name='catalog-categories'),
    path('catalog/categories/<slug:cat_slug>/',              catalog.category_detail, name='catalog-category'),
    path('catalog/categories/<slug:cat_slug>/<slug:subcat_slug>/', catalog.product_list, name='catalog-product-list'),
    path('catalog/products/<int:pk>/',                       catalog.product_detail, name='catalog-product'),
    path('catalog/search/',                                  catalog.search,         name='catalog-search'),

    # Cart
    path('cart/',         cart.cart_get,    name='cart-get'),
    path('cart/add/',     cart.cart_add,    name='cart-add'),
    path('cart/remove/',  cart.cart_remove, name='cart-remove'),
    path('cart/update/',  cart.cart_update, name='cart-update'),

    # Orders
    path('orders/checkout/',           orders.checkout,     name='orders-checkout'),
    path('orders/<uuid:token>/',       orders.order_detail, name='orders-detail'),
    path('orders/track/',              orders.order_track,  name='orders-track'),
    path('orders/my-orders/',          orders.my_orders,    name='orders-my'),

    # Accounts
    path('accounts/profile/',          accounts.profile,    name='accounts-profile'),
]
