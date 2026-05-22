from django.urls import path
# pyrefly: ignore [missing-import]
from . import views

app_name = 'catalog'

urlpatterns = [
    path('',                    views.home,           name='home'),
    path('nosotros/',           views.nosotros,       name='nosotros'),
    path('catalogo/',           views.catalog_hub,    name='hub'),
    path('catalogo/buscar/',    views.search_results, name='search'),
    path('catalogo/<int:pk>/',  views.product_detail, name='detail'),
    path('catalogo/<slug:cat_slug>/',
         views.category_hub, name='category'),
    path('catalogo/<slug:cat_slug>/<slug:subcat_slug>/',
         views.product_list,  name='product_list'),
]
