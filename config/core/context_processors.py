from django.conf import settings
from django.db.models import Prefetch

from catalog.models import Category, SiteConfig


def cart_count(request):
    cart = request.session.get('cart', {})
    count = sum(item['quantity'] for item in cart.values())
    return {'cart_count': count}


def active_categories(request):
    active_subs = Category.objects.filter(is_active=True).order_by('display_order')
    categories = (
        Category.objects
        .filter(is_active=True, parent=None)
        .prefetch_related(Prefetch('subcategories', queryset=active_subs))
        .order_by('display_order')
    )
    return {'nav_categories': categories}


def site_config(request):
    return {'site_config': SiteConfig.get()}


def meta_pixel(request):
    # Expone el ID del Meta Pixel a todos los templates.
    # Si esta vacio, el bloque del pixel en base.html no se renderiza.
    return {'META_PIXEL_ID': settings.META_PIXEL_ID}
