import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.http import Http404, JsonResponse
from django.utils.html import format_html
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from catalog.models import TipoArticulo
from inventario import etiquetas
from inventario.models import (
    VIAS, Color, LecturaRepetida, Lote, MovimientoPar, Par, TallaCalzado, fmt_talla)
from inventario.services.lotes import cerrar_lote, crear_lote, leer_flete, tarifas_por_via
from inventario.services.tallas import CORREGIBLES, corregir_talla
from inventario.services.tipos import costo_sugerido, crear_variante, tarifas_decimal
from inventario.validacion import cabe_en

ESTADOS = ['por_recibir', 'en_stock', 'vendido', 'baja']


MAX_POR_TALLA = 500


def _entero(v):
    """Entero >= 0 desde un int o un string de dígitos; ValueError si es float, bool, negativo o basura."""
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, str) and v.strip().isdigit():
        v = int(v.strip())
    if not isinstance(v, int) or v < 0:
        raise ValueError
    return v


def _error(msg):
    return JsonResponse({'ok': False, 'error': msg}, status=400)


def _respuesta_lote(lote, duplicado=False):
    datos = {'ok': True, 'lote_id': lote.pk,
             'url': reverse('inventario:lote_detalle', args=[lote.pk])}
    if duplicado:
        datos['duplicado'] = True
    return JsonResponse(datos)


def _modelos():
    """Modelos base para el alta: sus colores asignados y la variante (con
    prefijo) que ya tienen en cada vía."""
    bases = (TipoArticulo.objects.filter(padre__isnull=True)
             .prefetch_related('colores', 'variantes').order_by('nombre'))
    return [{
        'id': b.pk, 'nombre': b.nombre,
        'colores': [{'id': c.pk, 'nombre': c.nombre}
                    for c in sorted(b.colores.all(), key=lambda c: c.nombre)],
        # Sin prefijo no hay código de par; sin tallas no se captura por talla.
        'variantes': {v.via: v.pk for v in b.variantes.all() if v.prefijo and v.usa_tallas},
    } for b in bases]


def _variante_para(tipo, via, n, tarifas):
    """La variante de `via` para un modelo base (se crea con el costo sugerido si
    no existe); una variante se devuelve tal cual y crear_lote valida su vía."""
    if tipo.padre_id is not None:
        return tipo
    variante = tipo.variante(via)
    if variante is not None:
        return variante
    try:
        return crear_variante(tipo, via, costo=costo_sugerido(tipo, via, tarifas))
    except (ValidationError, ValueError) as e:
        detalle = '; '.join(e.messages) if hasattr(e, 'messages') else str(e)
        raise ValueError(f'Línea {n}: no se pudo crear "{tipo.nombre}" {VIAS[via].lower()}: {detalle}')


def _lineas_desde_json(datos, via):
    """Convierte el JSON del formulario en líneas para `crear_lote`; ValueError si es inválido.
    `tipo_id` puede ser el modelo base: se usa (o se crea) su variante de `via`."""
    lineas, numeros = [], []
    tarifas = tarifas_decimal()
    crudas = datos.get('lineas') or []
    if not isinstance(crudas, list):
        raise ValueError('Solicitud inválida.')
    for n, l in enumerate(crudas, 1):
        if not isinstance(l, dict) or not isinstance(l.get('cantidades') or {}, dict):
            raise ValueError(f'Línea {n}: formato inválido.')
        try:
            tipo = TipoArticulo.objects.get(pk=l.get('tipo_id'))
            color = Color.objects.get(pk=l.get('color_id'))
        except (TipoArticulo.DoesNotExist, Color.DoesNotExist, ValueError, TypeError):
            raise ValueError(f'Línea {n}: tipo o color inexistente.')
        if not tipo.colores_efectivos().filter(pk=color.pk).exists():
            raise ValueError(f'Línea {n}: el color «{color.nombre}» no está asignado a '
                             f'«{tipo.modelo_base.nombre}». Asígnalo en Catálogos.')
        crudo = l.get('precio_proveedor')
        try:
            precio = None if crudo in (None, '') else Decimal(str(crudo))
            if precio is not None and not precio.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            raise ValueError(f'Línea {n}: el precio del proveedor no es un número válido.')
        if precio is not None and not cabe_en(Par._meta.get_field('precio_proveedor'), precio):
            raise ValueError(f'Línea {n}: el precio del proveedor es demasiado grande.')
        for talla_txt, cant in (l.get('cantidades') or {}).items():
            if cant in (None, '', 0, '0'):
                continue
            try:
                talla = TallaCalzado.objects.get(mx_cm=Decimal(str(talla_txt)))
                cantidad = _entero(cant)
            except (InvalidOperation, TallaCalzado.DoesNotExist):
                raise ValueError(f'Línea {n}: la talla {talla_txt} no existe.')
            except (ValueError, TypeError):
                raise ValueError(f'Línea {n}: cantidad inválida para la talla {talla_txt}.')
            if cantidad > MAX_POR_TALLA:
                raise ValueError(
                    f'Línea {n}: cantidad demasiado grande para la talla {talla_txt} (máximo {MAX_POR_TALLA}).')
            if cantidad == 0:
                continue
            lineas.append({'tipo': tipo, 'color': color, 'talla': talla,
                           'cantidad': cantidad, 'precio_proveedor': precio})
            numeros.append(n)
    if not lineas:
        raise ValueError('Captura al menos un par (cantidad mayor que cero).')
    variantes = {}
    for n, linea in zip(numeros, lineas):
        tipo = linea['tipo']
        if tipo.pk not in variantes:
            variantes[tipo.pk] = _variante_para(tipo, via, n, tarifas)
        linea['tipo'] = variantes[tipo.pk]
    return lineas


@staff_member_required
def alta_manual(request):
    if request.method == 'POST':
        try:
            datos = json.loads(request.body or b'{}')
            if not isinstance(datos, dict):
                raise ValueError
        except ValueError:
            return _error('Solicitud inválida.')
        via, origen = datos.get('via'), datos.get('origen')
        if via not in VIAS or origen not in ('manual', 'inicial'):
            return _error('Vía u origen inválidos.')
        try:
            flete = leer_flete(datos.get('flete_por_par'))
        except ValueError as e:
            return _error(str(e))
        referencia = datos.get('referencia') or ''
        if not isinstance(referencia, str):
            return _error('La referencia debe ser texto.')
        referencia = referencia.strip()[:100]
        client_token = datos.get('client_token')
        sha = None
        if isinstance(client_token, str) and client_token.strip():
            sha = hashlib.sha256(client_token.strip().encode('utf-8')).hexdigest()
            existente = Lote.objects.filter(token_sha=sha).first()
            if existente is not None:
                return _respuesta_lote(existente, duplicado=True)
        try:
            # Atómico: si el lote falla, tampoco quedan las variantes creadas al vuelo.
            with transaction.atomic():
                lineas = _lineas_desde_json(datos, via)
                lote = crear_lote(
                    via=via, origen=origen, lineas=lineas, usuario=request.user,
                    referencia=referencia, token_sha=sha, flete_por_par=flete)
        except ValueError as e:
            return _error(str(e))
        except IntegrityError:
            # Dos envíos simultáneos: el UNIQUE dejó pasar solo uno.
            existente = Lote.objects.filter(token_sha=sha).first() if sha else None
            if existente is None:
                raise
            return _respuesta_lote(existente, duplicado=True)
        return _respuesta_lote(lote)
    return render(request, 'inventario/alta_manual.html', {
        'modelos': _modelos(),
        'tallas': TallaCalzado.objects.filter(activa=True),
        'vias': VIAS, 'tarifas': tarifas_por_via(),
    })


@staff_member_required
def tipo_colores(request, pk):
    tipo = get_object_or_404(TipoArticulo, pk=pk)
    colores = tipo.colores_efectivos().order_by('nombre')
    return JsonResponse({'colores': [{'id': c.pk, 'nombre': c.nombre} for c in colores]})


# Un par cuenta como recibido si llegó: está en stock, se vendió o se dio de baja después
# de llegar (dañado / perdido). El faltante del cierre de lote nunca llegó.
RECIBIDO = (Q(estado__in=[Par.Estado.EN_STOCK, Par.Estado.VENDIDO])
            | (Q(estado=Par.Estado.BAJA) & ~Q(motivo_baja='faltante')))
_RECIBIDO_EN_LOTE = (Q(pares__estado__in=[Par.Estado.EN_STOCK, Par.Estado.VENDIDO])
                     | (Q(pares__estado=Par.Estado.BAJA) & ~Q(pares__motivo_baja='faltante')))


@staff_member_required
def lotes(request):
    qs = Lote.objects.annotate(
        total=Count('pares', filter=~Q(pares__estado=Par.Estado.ANULADO)),
        recibidos=Count('pares', filter=_RECIBIDO_EN_LOTE),
        vendidos=Count('pares', filter=Q(pares__estado=Par.Estado.VENDIDO)))
    return render(request, 'inventario/lotes_lista.html', {'lotes': qs[:200]})


# La lista de no escaneados se muestra mientras sean menos que esto (filtrando por modelo).
MAX_LISTA_NO_ESCANEADOS = 30


def _modelos_del_lote(filas):
    """Los renglones del lote agrupados en una card por modelo, con sus totales."""
    modelos = {}
    for f in filas:
        m = modelos.setdefault(f['tipo_id'], {
            'tipo_id': f['tipo_id'], 'tipo': f['tipo'], 'filas': [],
            'esperados': 0, 'recibidos': 0, 'vendidos': 0, 'pendientes': 0})
        m['filas'].append(f)
        for k in ('esperados', 'recibidos', 'vendidos', 'pendientes'):
            m[k] += f[k]
    return list(modelos.values())


def _no_escaneados(lote):
    """Pares por recibir con su número de etiqueta: la posición en "Imprimir todo"."""
    pares = lote.pares.exclude(estado=Par.Estado.ANULADO).select_related('tipo', 'color', 'talla').order_by('pk')
    return [{
        'numero': i, 'codigo': p.codigo, 'tipo_id': p.tipo_id, 'tipo': p.tipo.nombre,
        'color': p.color.nombre, 'talla': fmt_talla(p.talla.mx_cm),
        'url': reverse('inventario:par_ficha', args=[p.codigo]),
    } for i, p in enumerate(pares, 1) if p.estado == Par.Estado.POR_RECIBIR]


@staff_member_required
def lote_detalle(request, pk):
    lote = get_object_or_404(Lote, pk=pk)
    conteo = {e: 0 for e in ESTADOS}
    # Las etiquetas anuladas al editar el lote no son pares: no cuentan en nada.
    vigentes = lote.pares.exclude(estado=Par.Estado.ANULADO)
    for fila in vigentes.values('estado').annotate(n=Count('pk')):
        conteo[fila['estado']] = fila['n']
    filas = [{
        'tipo': r['tipo__nombre'], 'color': r['color__nombre'],
        'talla': fmt_talla(r['talla__mx_cm']), 'esperados': r['esperados'],
        'recibidos': r['recibidos'], 'vendidos': r['vendidos'], 'pendientes': r['pendientes'],
        'url_tallas': reverse('inventario:lote_tallas',
                              args=[lote.pk, r['tipo_id'], r['color_id'], r['talla_id']]),
        # Campo de "cuántas imprimir" de este grupo (lo lee views/etiquetas.py).
        'campo': f'c_{r["tipo_id"]}_{r["color_id"]}_{r["talla_id"]}',
        'tipo_id': r['tipo_id'],
    } for r in vigentes.values('tipo_id', 'color_id', 'talla_id',
                                 'tipo__nombre', 'color__nombre', 'talla__mx_cm')
        .annotate(esperados=Count('pk'),
                  recibidos=Count('pk', filter=RECIBIDO),
                  vendidos=Count('pk', filter=Q(estado=Par.Estado.VENDIDO)),
                  pendientes=Count('pk', filter=Q(estado=Par.Estado.POR_RECIBIR)))
        .order_by('tipo__nombre', 'color__nombre', 'talla__orden')]
    total = sum(conteo.values())
    recibidos = sum(f['recibidos'] for f in filas)
    modelos = _modelos_del_lote(filas)
    for m in modelos:  # botón "Imprimir sin escanear" de la card, en PDFs de MAX_POR_PDF
        m['partes_pend'] = etiquetas.partes(m['pendientes'])
    return render(request, 'inventario/lote_detalle.html', {
        'lote': lote, 'conteo': conteo, 'filas': filas, 'total': total, 'recibidos': recibidos,
        'modelos': modelos, 'no_escaneados': _no_escaneados(lote),
        'max_lista': MAX_LISTA_NO_ESCANEADOS,
        'n_anulados': lote.pares.filter(estado=Par.Estado.ANULADO).count(),
        'n_repetidos': lote.lecturas_repetidas.values('par').distinct().count(),
        # La app de la impresora no acepta más de `limite` páginas por trabajo.
        'limite': etiquetas.MAX_POR_PDF,
        'botones_lote': [
            ('todos', 'Imprimir todo', etiquetas.partes(total)),
            ('pendientes', 'Imprimir pendientes', etiquetas.partes(conteo['por_recibir'])),
        ]})


@staff_member_required
def repetidos(request):
    """Pares escaneados para recibir cuando ya estaban en stock, del más reciente al más viejo."""
    lotes_con = Lote.objects.filter(lecturas_repetidas__isnull=False).distinct().order_by('-pk')
    try:
        lote = Lote.objects.filter(pk=int(request.GET.get('lote', ''))).first()
    except ValueError:
        lote = None
    qs = Par.objects.filter(lecturas_repetidas__isnull=False)
    if lote is not None:
        qs = qs.filter(lecturas_repetidas__lote=lote)
    pares = list(
        qs.annotate(repetidas=Count('lecturas_repetidas', distinct=True),
                    ultima_at=Max('lecturas_repetidas__creado_at'))
        .select_related('tipo__padre', 'color', 'talla', 'lote')
        .order_by('-ultima_at', '-pk')[:500])
    # La recepción (la primera lectura) y la última repetida de cada par, con su usuario.
    recepcion, ultima = {}, {}
    for m in (MovimientoPar.objects.filter(par__in=pares, a_estado=Par.Estado.EN_STOCK)
              .select_related('usuario').order_by('creado_at', 'pk')):
        recepcion[m.par_id] = m
    for lr in (LecturaRepetida.objects.filter(par__in=pares)
               .select_related('usuario').order_by('creado_at', 'pk')):
        ultima[lr.par_id] = lr
    filas = [{
        'par': p, 'talla': fmt_talla(p.talla.mx_cm), 'veces': p.repetidas + 1,
        'recepcion': recepcion.get(p.pk), 'ultima': ultima.get(p.pk),
    } for p in pares]
    return render(request, 'inventario/repetidos.html', {
        'filas': filas, 'lote': lote, 'lotes_con': lotes_con})


@staff_member_required
def lote_tallas(request, pk, tipo_id, color_id, talla_id):
    """Corregir la talla de pares de un renglón del lote (tipo × color × talla)."""
    lote = get_object_or_404(Lote, pk=pk)
    pares = list(lote.pares.filter(tipo_id=tipo_id, color_id=color_id, talla_id=talla_id)
                 .select_related('tipo__padre', 'color', 'talla').order_by('pk'))
    if not pares:
        raise Http404('Ese renglón no está en el lote.')
    corregibles = {p.pk: p for p in pares if p.estado in CORREGIBLES}
    tallas_activas = TallaCalzado.objects.filter(activa=True).exclude(pk=talla_id)
    if request.method == 'POST':
        ids = set()
        for v in request.POST.getlist('par'):
            if v.isdigit() and int(v) in corregibles:
                ids.add(int(v))
        talla = tallas_activas.filter(pk=request.POST.get('talla') or 0).first() \
            if (request.POST.get('talla') or '').isdigit() else None
        if not ids:
            messages.error(request, 'Marca al menos un par por recibir o en stock.')
        elif talla is None:
            messages.error(request, 'Elige la talla correcta.')
        else:
            try:
                cambiados = corregir_talla([corregibles[i] for i in sorted(ids)], talla,
                                           usuario=request.user, nota=request.POST.get('nota', ''))
            except ValueError as e:
                messages.error(request, str(e))
            else:
                codigos = ','.join(p.codigo for p in cambiados)
                url = reverse('inventario:etiquetas_lote', args=[lote.pk]) + f'?codigos={codigos}'
                n = len(cambiados)
                messages.success(request, format_html(
                    '{} par{} pasado{} de la talla {} a la {}. La etiqueta impresa trae la talla '
                    'anterior: <a href="{}" target="_blank">imprime las nuevas</a>.',
                    n, 'es' if n != 1 else '', 's' if n != 1 else '',
                    fmt_talla(pares[0].talla.mx_cm), fmt_talla(talla.mx_cm), url))
                return redirect('inventario:lote_detalle', pk=lote.pk)
    try:
        marcado = int(request.GET.get('par', ''))
    except ValueError:
        marcado = None
    return render(request, 'inventario/lote_tallas.html', {
        'lote': lote, 'pares': pares, 'muestra': pares[0], 'talla': fmt_talla(pares[0].talla.mx_cm),
        'corregibles': corregibles, 'marcado': marcado,
        'tallas': [(t.pk, fmt_talla(t.mx_cm), etiquetas.equivalencias(t)) for t in tallas_activas]})


@staff_member_required
@require_POST
def lote_cerrar(request, pk):
    lote = get_object_or_404(Lote, pk=pk)
    n = cerrar_lote(lote, usuario=request.user)
    plural = 's' if n != 1 else ''
    messages.success(request, f'Lote cerrado: {n} par{"es" if n != 1 else ""} marcado{plural} como faltante.')
    return redirect('inventario:lote_detalle', pk=lote.pk)
