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


def page_help(request):
    # Ayuda contextual del panel (core/help_registry.py). Solo la pinta
    # base_panel.html, así que la tienda pública nunca la muestra.
    from core.help_registry import HELP_REGISTRY, get_help_cards

    match = getattr(request, 'resolver_match', None)
    view_name = getattr(match, 'view_name', '') or ''
    user = getattr(request, 'user', None)
    cards = get_help_cards(view_name, user) if view_name else []
    profile = getattr(user, 'profile', None) if getattr(user, 'is_staff', False) else None
    return {
        'page_help_cards': cards,
        'page_help_view': view_name,
        # Staff con la ayuda apagada en una pantalla que sí tiene: se le ofrece encenderla.
        'page_help_dismissed': bool(
            profile is not None and profile.help_dismissed and view_name in HELP_REGISTRY),
    }
