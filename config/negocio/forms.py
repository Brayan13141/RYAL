import datetime
from django import forms
from .models import Cliente, Pedido, Pago, Gasto
from .phone import normalize_telefono


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ['nombre', 'telefono', 'descuento', 'notas']
        widgets = {
            'notas': forms.Textarea(attrs={'rows': 3}),
        }

    def clean_telefono(self):
        telefono = normalize_telefono(self.cleaned_data['telefono'])
        if len(telefono) != 10:
            raise forms.ValidationError('El teléfono debe tener al menos 10 dígitos.')
        return telefono


class PedidoForm(forms.ModelForm):
    class Meta:
        model = Pedido
        fields = ['cliente', 'descripcion', 'costo_producto', 'precio_venta', 'envio', 'estado']
        widgets = {
            'descripcion': forms.Textarea(attrs={'rows': 3}),
        }


class PagoForm(forms.ModelForm):
    class Meta:
        model = Pago
        fields = ['fecha', 'monto', 'notas']
        widgets = {
            'fecha': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fecha'].initial = datetime.date.today


class GastoForm(forms.ModelForm):
    class Meta:
        model = Gasto
        fields = ['fecha', 'descripcion', 'monto', 'categoria']
        widgets = {
            'fecha': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fecha'].initial = datetime.date.today


from catalog.models import TipoArticulo, CodigoDescuento
from .models import PedidoItem


class TipoArticuloForm(forms.ModelForm):
    class Meta:
        model = TipoArticulo
        fields = ['nombre', 'keywords', 'costo']
        widgets = {
            'keywords': forms.Textarea(attrs={'rows': 3, 'style': 'font-family:monospace'}),
        }
        help_texts = {
            'keywords': 'Palabras clave separadas por coma. Ej: gorra,cap,ny,la,za',
            'costo': 'Costo al proveedor en MXN (sin markup).',
        }


class CodigoDescuentoForm(forms.ModelForm):
    class Meta:
        model = CodigoDescuento
        fields = ['codigo', 'descripcion', 'descuento', 'tipo_articulo',
                  'is_active', 'usos_max', 'valid_hasta']
        widgets = {
            'valid_hasta': forms.DateInput(attrs={'type': 'date'}),
            'codigo': forms.TextInput(attrs={'style': 'text-transform:uppercase'}),
            'descripcion': forms.TextInput(attrs={'placeholder': 'Ej. Descuento clientes mayoreo'}),
        }


class PedidoItemForm(forms.ModelForm):
    class Meta:
        model = PedidoItem
        fields = ['nombre_snapshot', 'cantidad', 'costo_unitario', 'precio_unitario']
        labels = {
            'nombre_snapshot': 'Descripción',
            'costo_unitario':  'Costo unitario (MXN)',
            'precio_unitario': 'Precio unitario (MXN)',
        }
        widgets = {
            'nombre_snapshot': forms.TextInput(attrs={'placeholder': 'Ej. Gorra NY talla única'}),
            'costo_unitario':  forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
            'precio_unitario': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }
