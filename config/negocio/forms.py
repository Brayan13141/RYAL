import datetime
from django import forms
from .models import Cliente, Pedido, Pago, Gasto


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ['nombre', 'telefono', 'descuento', 'notas']
        widgets = {
            'notas': forms.Textarea(attrs={'rows': 3}),
        }


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
