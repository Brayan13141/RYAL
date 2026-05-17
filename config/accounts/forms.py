from django import forms


class CustomSignupForm(forms.Form):
    first_name = forms.CharField(
        max_length=100,
        label='Nombre completo',
        required=True,
        widget=forms.TextInput(attrs={'placeholder': 'Tu nombre'}),
    )

    def signup(self, request, user):
        user.first_name = self.cleaned_data['first_name']
        user.save()
