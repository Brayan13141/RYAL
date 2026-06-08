"""
Puebla Product.modaverse_name desde scraped_modaverse.json para todos los
productos existentes en BD. Ejecutar desde la raíz del repo:
  python -X utf8 fix_mv_names.py
"""
import os, sys, json
from pathlib import Path

# Bootstrap Django
sys.path.insert(0, str(Path(__file__).parent / 'config'))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()

from catalog.models import Product

json_path = Path(__file__).parent / 'scraped_modaverse.json'
with open(json_path, encoding='utf-8') as f:
    data = json.load(f)

products = data.get('products', [])
print(f'{len(products)} productos en JSON')

updated = skipped = missing = 0
for p in products:
    raw_name = (p.get('name') or '').strip()
    pid = p.get('sku', '')
    if not raw_name or not pid:
        continue
    try:
        prod = Product.objects.get(supplier_url__icontains=pid)
    except Product.DoesNotExist:
        missing += 1
        continue
    except Product.MultipleObjectsReturned:
        # Tomar el más reciente si hay duplicados
        prod = Product.objects.filter(supplier_url__icontains=pid).order_by('-pk').first()

    if prod.modaverse_name == raw_name:
        skipped += 1
        continue

    prod.modaverse_name = raw_name
    prod.save(update_fields=['modaverse_name'])
    updated += 1

print(f'Actualizados: {updated}  ya correctos: {skipped}  no encontrados: {missing}')
