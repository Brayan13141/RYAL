from allauth.account.views import LoginView as AllauthLoginView, SignupView as AllauthSignupView
from django.contrib import messages
from django.contrib.auth import login, authenticate, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Q
from django.shortcuts import render, redirect
from django.utils.decorators import method_decorator
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from orders.models import Order


def _client_ip(group, request):
    """Lee la IP real del cliente desde X-Forwarded-For (Nginx proxy).
    django-ratelimit 4.x llama callables con (group, request)."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
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


def login_view(request):
    if request.user.is_authenticated:
        return redirect('catalog:home')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user     = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            return redirect(request.POST.get('next') or 'catalog:home')
        return render(request, 'accounts/login.html', {'form': {'errors': True}})

    return render(request, 'accounts/login.html', {
        'next': request.GET.get('next', ''),
    })


def register_view(request):
    if request.user.is_authenticated:
        return redirect('catalog:home')

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        email      = request.POST.get('email', '').strip().lower()
        password1  = request.POST.get('password1', '')
        password2  = request.POST.get('password2', '')

        errors = []
        if not first_name:
            errors.append('El nombre es requerido.')
        if not email:
            errors.append('El correo es requerido.')
        elif User.objects.filter(username=email).exists():
            errors.append('Ya existe una cuenta con ese correo.')
        if len(password1) < 8:
            errors.append('La contraseña debe tener al menos 8 caracteres.')
        if password1 != password2:
            errors.append('Las contraseñas no coinciden.')

        if errors:
            return render(request, 'accounts/register.html', {'form': {'errors': errors}})

        user = User.objects.create_user(
            username   = email,
            email      = email,
            password   = password1,
            first_name = first_name,
        )
        login(request, user)
        messages.success(request, f'Bienvenido, {first_name}.')
        return redirect('catalog:home')

    return render(request, 'accounts/register.html', {})


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
