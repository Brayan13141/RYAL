from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q

from catalog.models import TipoArticulo

from .models import Color, PrecioVenta, TallaCalzado, TarifaFlete


class ColorForm(forms.ModelForm):
    class Meta:
        model = Color
        fields = ['nombre', 'alias', 'activo']
        help_texts = {'alias': 'Sinónimos separados por coma; es lo que reconoce el bot.'}

    def clean_nombre(self):
        # La unicidad real vive sobre el valor normalizado (save() lo normaliza):
        # validarla aquí evita un IntegrityError por "NEGRO" cuando existe "negro".
        nombre = Color.normalizar(self.cleaned_data['nombre'])
        if not nombre:
            raise ValidationError('Escribe un nombre.')
        if Color.objects.filter(nombre=nombre).exclude(pk=self.instance.pk).exists():
            raise ValidationError(f'Ya existe el color "{nombre}".')
        return nombre


class TallaForm(forms.ModelForm):
    class Meta:
        model = TallaCalzado
        fields = ['mx_cm', 'eu', 'us_hombre', 'us_mujer', 'cn_mm', 'orden', 'activa']


class TarifaFleteForm(forms.ModelForm):
    monto_por_par = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2)

    class Meta:
        model = TarifaFlete
        fields = ['monto_por_par']


class PrecioVentaForm(forms.ModelForm):
    # Todos los tipos: PrecioVenta.clean() rechaza la variante con su mensaje.
    class Meta:
        model = PrecioVenta
        fields = ['tipo', 'nombre', 'precio', 'orden']


class ColoresModeloForm(forms.Form):
    """Asigna los colores disponibles de un modelo base."""
    tipo = forms.ModelChoiceField(
        queryset=TipoArticulo.objects.filter(padre__isnull=True).order_by('nombre'))
    colores = forms.ModelMultipleChoiceField(
        queryset=Color.objects.filter(activo=True), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Activos MÁS los ya asignados al modelo aunque estén inactivos: si no, guardar los
        # quitaría en silencio. Sin modelo elegido (form vacío) entran los asignados a cualquiera.
        asignados = Color.objects.filter(tipoarticulo__isnull=False)
        tipo_pk = self.data.get(self.add_prefix('tipo')) if self.is_bound else None
        if tipo_pk:
            try:
                asignados = Color.objects.filter(tipoarticulo__pk=int(tipo_pk))
            except (TypeError, ValueError):
                asignados = Color.objects.none()
        self.fields['colores'].queryset = Color.objects.filter(
            Q(activo=True) | Q(pk__in=asignados.values('pk'))).order_by('nombre')
        self.fields['colores'].label_from_instance = (
            lambda c: c.nombre if c.activo else f'{c.nombre} (inactivo)')

    def save(self):
        tipo = self.cleaned_data['tipo']
        tipo.colores.set(self.cleaned_data['colores'])
        return tipo
