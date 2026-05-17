from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path('accounts/perfil/',  views.profile_view, name='profile'),
    path('accounts/salir/',   views.logout_view,  name='logout'),
]
