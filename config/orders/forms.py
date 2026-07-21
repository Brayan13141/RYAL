import datetime

from django import forms

from .models import OrderPayment


class OrderPaymentForm(forms.ModelForm):
    class Meta:
        model = OrderPayment
        fields = ['fecha', 'monto', 'metodo_pago', 'notas']
        widgets = {
            'fecha': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['fecha'].initial = datetime.date.today

    def clean_monto(self):
        monto = self.cleaned_data.get('monto')
        if monto is None or monto <= 0:
            raise forms.ValidationError('El monto debe ser mayor a 0.')
        return monto
