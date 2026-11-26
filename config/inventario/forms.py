from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from catalog.models import TipoArticulo

from .models import (
    Color, FotoColor, PrecioLista, PrecioVenta, TallaCalzado, TarifaFlete, _existe)
from .services.fotos import reducir_foto


class EstiloInv:
    """Pone las clases del panel a los widgets (Django los deja sin clase y salen crudos)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            w = field.widget
            if isinstance(w, forms.CheckboxSelectMultiple):
                continue
            if isinstance(w, forms.CheckboxInput):
                cls = 'form-check-input'
                w.attrs.setdefault('role', 'switch')
            elif isinstance(w, forms.Select):  # incluye SelectMultiple
                cls = 'form-select'
            else:
                cls = 'form-control'
            w.attrs['class'] = f"{w.attrs.get('class', '')} {cls}".strip()


class ColorForm(EstiloInv, forms.ModelForm):
    class Meta:
        model = Color
        fields = ['nombre', 'alias', 'activo']
        widgets = {'alias': forms.TextInput(attrs={'placeholder': 'Ej. marino, azul rey'})}
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


class TallaForm(EstiloInv, forms.ModelForm):
    class Meta:
        model = TallaCalzado
        fields = ['mx_cm', 'eu', 'us_hombre', 'us_mujer', 'cn_mm', 'orden', 'activa']
        labels = {'mx_cm': 'Talla MX (cm)', 'eu': 'EU', 'us_hombre': 'US hombre',
                  'us_mujer': 'US mujer', 'cn_mm': 'CN (mm)', 'orden': 'Orden', 'activa': 'Activa'}
        help_texts = {'orden': 'Menor sale primero en altas, etiquetas y stock.'}


class TarifaFleteForm(EstiloInv, forms.ModelForm):
    monto_por_par = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2)

    class Meta:
        model = TarifaFlete
        fields = ['monto_por_par']


class PrecioListaForm(EstiloInv, forms.ModelForm):
    precio = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2)

    class Meta:
        model = PrecioLista
        fields = ['precio']


class PrecioVentaForm(EstiloInv, forms.ModelForm):
    # Todos los tipos: PrecioVenta.clean() rechaza la variante con su mensaje.
    precio = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2)

    class Meta:
        model = PrecioVenta
        fields = ['tipo', 'nombre', 'precio', 'orden']
        labels = {'tipo': 'Modelo', 'nombre': 'Nombre del precio', 'orden': 'Orden'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['tipo'].queryset = TipoArticulo.objects.order_by('nombre')
        self.fields['nombre'].widget.attrs.setdefault('placeholder', 'Ej. Promoción')


class ColoresModeloForm(EstiloInv, forms.Form):
    """Asigna los colores disponibles de un modelo base."""
    tipo = forms.ModelChoiceField(
        queryset=TipoArticulo.objects.filter(padre__isnull=True).order_by('nombre'))
    colores = forms.ModelMultipleChoiceField(
        queryset=Color.objects.filter(activo=True), required=False,
        widget=forms.CheckboxSelectMultiple)

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


class FotosModeloForm(EstiloInv, forms.Form):
    """Foto del modelo base y una foto por cada color marcado del modelo."""
    foto = forms.FileField(required=False, label='Foto del modelo')
    quitar_foto = forms.BooleanField(required=False, label='Quitar')

    def __init__(self, *args, tipo, **kwargs):
        super().__init__(*args, **kwargs)
        self.tipo = tipo
        self.colores = list(tipo.colores.order_by('nombre'))
        for c in self.colores:
            self.fields[f'foto_{c.pk}'] = forms.FileField(required=False, label=c.nombre)
            self.fields[f'quitar_{c.pk}'] = forms.BooleanField(required=False, label='Quitar')
            self.fields[f'foto_{c.pk}'].widget.attrs['class'] = 'form-control form-control-sm'
            self.fields[f'quitar_{c.pk}'].widget.attrs['class'] = 'form-check-input'
        self.fields['foto'].widget.attrs['class'] = 'form-control form-control-sm'
        self.fields['quitar_foto'].widget.attrs['class'] = 'form-check-input'
        self._reducidas = {}

    def filas(self):
        """Una fila por color para la plantilla: campos y URL de la miniatura (o None)."""
        actuales = {f.color_id: f for f in self.tipo.fotos_color.all()}
        filas = []
        for c in self.colores:
            fc = actuales.get(c.pk)
            filas.append({
                'color': c, 'foto': self[f'foto_{c.pk}'], 'quitar': self[f'quitar_{c.pk}'],
                'url': fc.imagen.url if fc and _existe(fc.imagen) else None})
        return filas

    def url_base(self):
        return self.tipo.foto.url if _existe(self.tipo.foto) else None

    def clean(self):
        cleaned = super().clean()
        # Se valida todo antes de escribir: un solo archivo malo cancela el POST entero.
        for nombre in ['foto'] + [f'foto_{c.pk}' for c in self.colores]:
            archivo = cleaned.get(nombre)
            if not archivo:
                continue
            try:
                self._reducidas[nombre] = reducir_foto(archivo)
            except ValueError as e:
                self.add_error(nombre, str(e))
        return cleaned

    @transaction.atomic
    def save(self):
        tipo, cleaned = self.tipo, self.cleaned_data
        viejos = []
        nueva = self._reducidas.get('foto')
        if nueva or (cleaned.get('quitar_foto') and tipo.foto):
            if tipo.foto:
                viejos.append(tipo.foto.name)
            if nueva:
                tipo.foto.save(nueva.name, nueva, save=False)
            else:
                tipo.foto = ''
            tipo.save(update_fields=['foto'])
        actuales = {f.color_id: f for f in tipo.fotos_color.all()}
        for c in self.colores:
            nueva = self._reducidas.get(f'foto_{c.pk}')
            fc = actuales.get(c.pk)
            if nueva:
                if fc is None:
                    fc = FotoColor(tipo=tipo, color=c)
                elif fc.imagen:
                    viejos.append(fc.imagen.name)
                fc.imagen.save(nueva.name, nueva, save=False)
                fc.save()
            elif cleaned.get(f'quitar_{c.pk}') and fc:
                viejos.append(fc.imagen.name)
                fc.delete()
        storage = FotoColor._meta.get_field('imagen').storage
        for nombre in viejos:
            storage.delete(nombre)
        return tipo


class TarifaNuevaForm(EstiloInv, forms.ModelForm):
    """Alta de la tarifa de una vía que se quedó sin ella."""
    monto_por_par = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2,
                                       label='Monto por par')

    class Meta:
        model = TarifaFlete
        fields = ['via', 'monto_por_par']
        labels = {'via': 'Vía'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        usadas = set(TarifaFlete.objects.values_list('via', flat=True))
        self.fields['via'].choices = [c for c in self.fields['via'].choices
                                     if c[0] and c[0] not in usadas]


class PrecioListaNuevaForm(EstiloInv, forms.ModelForm):
    """Alta de una combinación calidad × tipo de venta que falta en la lista."""
    precio = forms.DecimalField(min_value=0, max_digits=8, decimal_places=2)

    class Meta:
        model = PrecioLista
        fields = ['calidad', 'tipo_venta', 'precio']
        labels = {'calidad': 'Calidad', 'tipo_venta': 'Tipo de venta'}
