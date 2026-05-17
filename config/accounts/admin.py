from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import Profile


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = 'Perfil'
    fields = ['phone', 'address']


class UserAdmin(BaseUserAdmin):
    inlines = [ProfileInline]
    list_display = ['username', 'email', 'first_name', 'last_name', 'is_staff', 'get_phone']

    @admin.display(description='Teléfono')
    def get_phone(self, obj):
        return obj.profile.phone if hasattr(obj, 'profile') else ''


admin.site.unregister(User)
admin.site.register(User, UserAdmin)
