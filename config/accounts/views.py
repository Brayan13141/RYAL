from allauth.account.views import LoginView as AllauthLoginView, SignupView as AllauthSignupView
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import render, redirect
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from orders.models import Order


def _client_ip(group, request):
    """IP real del cliente. django-ratelimit 4.x pasa (group, request).
    X-Real-IP (seteado por Nginx desde $remote_addr) no es spoofeable.
    X-Forwarded-For puede ser falsificado — NO usar el primer elemento."""
    real_ip = request.META.get('HTTP_X_REAL_IP', '').strip()
    if real_ip:
        return real_ip
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[-1].strip()
    return request.META.get('REMOTE_ADDR', '127.0.0.1')


# ─── Magic bytes validation ──────────────────────────────────────────────────

_IMAGE_MAGIC = (
    b'\xff\xd8\xff',        # JPEG
    b'\x89PNG\r\n\x1a\n',  # PNG
    b'GIF87a', b'GIF89a',  # GIF
)
_AVATAR_MAX_BYTES = 5 * 1024 * 1024  # 5 MB


def _validate_avatar(f):
    """Return error string or None. Checks size and magic bytes."""
    if f.size > _AVATAR_MAX_BYTES:
        return 'El avatar no puede superar 5 MB.'
    header = f.read(12)
    f.seek(0)
    if any(header.startswith(sig) for sig in _IMAGE_MAGIC):
        return None
    if header[:4] == b'RIFF' and header[8:12] == b'WEBP':
        return None
    return 'Tipo de archivo no permitido (se aceptan JPEG, PNG, GIF, WebP)'


# ─── Rate-limited allauth views ───────────────────────────────────────────────

@method_decorator(ratelimit(key=_client_ip, rate='10/m', method='POST', block=False), name='post')
class RateLimitedLoginView(AllauthLoginView):
    def post(self, request, *args, **kwargs):
        if getattr(request, 'limited', False):
            messages.error(request, 'Demasiados intentos de acceso. Espera un minuto.')
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)


@method_decorator(ratelimit(key=_client_ip, rate='5/m', method='POST', block=False), name='post')
class RateLimitedSignupView(AllauthSignupView):
    def post(self, request, *args, **kwargs):
        if getattr(request, 'limited', False):
            messages.error(request, 'Demasiados intentos de registro. Espera un minuto.')
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)



@require_POST
def logout_view(request):
    logout(request)
    return redirect('catalog:home')


@login_required
def profile_view(request):
    profile = request.user.profile
    errors  = []
    success = False

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        phone      = request.POST.get('phone', '').strip()
        address    = request.POST.get('address', '').strip()
        avatar_file = request.FILES.get('avatar')
        remove_avatar = request.POST.get('remove_avatar') == '1'

        if not first_name:
            errors.append('El nombre es requerido.')

        if not errors:
            request.user.first_name = first_name
            request.user.save(update_fields=['first_name'])
            profile.phone   = phone
            profile.address = address
            if remove_avatar and profile.avatar:
                profile.avatar.delete(save=False)
                profile.avatar = None
            elif avatar_file:
                avatar_err = _validate_avatar(avatar_file)
                if avatar_err:
                    errors.append(avatar_err)
                else:
                    if profile.avatar:
                        profile.avatar.delete(save=False)
                    profile.avatar = avatar_file
            if not errors:
                profile.save()
                success = True

    q = Q(user=request.user)
    if profile.phone:
        q |= Q(customer_phone=profile.phone)
    recent_orders = (
        Order.objects
        .filter(q)
        .prefetch_related('items')
        .distinct()
        .order_by('-created_at')[:3]
    )

    return render(request, 'accounts/profile.html', {
        'profile':       profile,
        'errors':        errors,
        'success':       success,
        'recent_orders': recent_orders,
    })
