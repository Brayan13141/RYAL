from django.contrib import admin

from .models import Color, TallaCalzado, TarifaFlete


@admin.register(TallaCalzado)
class TallaCalzadoAdmin(admin.ModelAdmin):
    list_display = ('mx_cm', 'eu', 'us_hombre', 'us_mujer', 'cn_mm', 'orden', 'activa')
    list_editable = ('orden', 'activa')


@admin.register(Color)
class ColorAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'alias', 'activo')
    search_fields = ('nombre', 'alias')


@admin.register(TarifaFlete)
class TarifaFleteAdmin(admin.ModelAdmin):
    list_display = ('via', 'monto_por_par')
