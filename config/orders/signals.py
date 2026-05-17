from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in)
def load_saved_cart(sender, request, user, **kwargs):
    from .models import SavedCartItem
    saved = SavedCartItem.objects.filter(user=user).select_related('product', 'variant')
    if not saved.exists():
        return

    cart = request.session.get('cart', {})
    for item in saved:
        if item.cart_key in cart:
            continue  # sesión tiene prioridad
        try:
            price = float(
                item.variant.final_price if item.variant else item.product.final_price
            )
        except Exception:
            continue
        cart[item.cart_key] = {
            'product_id':   item.product_id,
            'variant_id':   item.variant_id,
            'variant_name': item.variant_name,
            'quantity':     item.quantity,
            'price':        price,
        }

    request.session['cart'] = cart
    request.session.modified = True
