from django.http import JsonResponse

# Stubs — full implementation in Task 3

def api_cliente(request, telefono): return JsonResponse({'descuento': 0})
def api_log(request): return JsonResponse({'ok': True})
