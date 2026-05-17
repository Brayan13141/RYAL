import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'config'))

django.setup()

from catalog.models import Product, Category

# 1. Borrar productos TN2
deleted, _ = Product.objects.filter(sku__startswith='RYL-TN2-').delete()
print(f'Eliminados: {deleted} productos TN2')

# 2. Actualizar shipping_cost de categorias Calzado
s = Category.objects.filter(parent__slug='calzado').update(shipping_cost=280)
r = Category.objects.filter(slug='calzado').update(shipping_cost=280)
print(f'Actualizadas: {s} subcategorias + {r} raiz Calzado -> shipping $280')

print('Listo. Ahora corre: python config\manage.py load_productos --only calzado')
