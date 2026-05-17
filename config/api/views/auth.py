from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.middleware.csrf import get_token
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from allauth.account.forms import LoginForm
from django_ratelimit.decorators import ratelimit


def _user_data(user):
    return {
        'id': user.pk,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'is_staff': user.is_staff,
    }


@api_view(['GET'])
@permission_classes([AllowAny])
def me(request):
    get_token(request)  # ensure csrftoken cookie is set
    if request.user.is_authenticated:
        return Response(_user_data(request.user))
    return Response({'detail': 'No autenticado.'}, status=status.HTTP_401_UNAUTHORIZED)


@api_view(['POST'])
@permission_classes([AllowAny])
@ratelimit(key='ip', rate='10/m', method='POST', block=False)
def login_view(request):
    if getattr(request, 'limited', False):
        return Response({'detail': 'Demasiados intentos. Espera un momento.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    email_or_username = request.data.get('username', '').strip()
    password = request.data.get('password', '')

    # Try email lookup first
    user = None
    if '@' in email_or_username:
        try:
            u = User.objects.get(email__iexact=email_or_username)
            user = authenticate(request, username=u.username, password=password)
        except User.DoesNotExist:
            pass
    if user is None:
        user = authenticate(request, username=email_or_username, password=password)

    if user is None:
        return Response({'detail': 'Credenciales incorrectas.'}, status=status.HTTP_400_BAD_REQUEST)
    if not user.is_active:
        return Response({'detail': 'Cuenta desactivada.'}, status=status.HTTP_400_BAD_REQUEST)

    login(request, user)
    return Response(_user_data(user))


@api_view(['POST'])
@permission_classes([AllowAny])
@ratelimit(key='ip', rate='5/m', method='POST', block=False)
def signup_view(request):
    if getattr(request, 'limited', False):
        return Response({'detail': 'Demasiados intentos. Espera un momento.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

    email = request.data.get('email', '').strip()
    username = request.data.get('username', '').strip()
    password = request.data.get('password', '')
    first_name = request.data.get('first_name', '').strip()

    if not email or not password:
        return Response({'detail': 'Email y contraseña requeridos.'}, status=status.HTTP_400_BAD_REQUEST)

    if User.objects.filter(email__iexact=email).exists():
        return Response({'detail': 'Ya existe una cuenta con ese email.'}, status=status.HTTP_400_BAD_REQUEST)

    if not username:
        username = email.split('@')[0]
    base = username
    n = 1
    while User.objects.filter(username=username).exists():
        username = f'{base}{n}'
        n += 1

    user = User.objects.create_user(username=username, email=email, password=password, first_name=first_name)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return Response(_user_data(user), status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def logout_view(request):
    logout(request)
    return Response({'detail': 'Sesión cerrada.'})
