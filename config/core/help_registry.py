"""Ayuda contextual del panel de staff.

Portado de Space Cheer. Cada pantalla del panel y de negocio tiene 2-3 tarjetas
que el modal de ayuda muestra como un carrusel: qué es la pantalla, cómo se usa
y el detalle que más se olvida.

La llave es el nombre de la vista (`namespace:name`). No hay roles: la ayuda es
solo para staff. `core/tests_help.py` falla si se agrega una pantalla del panel
o de negocio sin entrada aquí — las imprimibles van en EXCLUDED_VIEWS.
"""

HELP_REGISTRY = {
    # ── PANEL · INICIO ──────────────────────────────────────────────────────
    'panel:dashboard': [
        'Vista rápida de la tienda web: pedidos de los últimos 7 días, cuántos hay en cada estado, '
        'lo más vendido con su margen y los últimos pedidos.',
        '«Total en caja» viene del módulo Negocio: lo cobrado menos los gastos más los ajustes. '
        'Si no cuadra con el efectivo real, entra a «ajustar» y registra el conteo.',
        '«Desactivar sin foto» oculta de la tienda los productos activos que no tienen imagen. '
        'Se pueden volver a activar desde Productos.',
    ],
    'panel:resumen_global': [
        'Resumen de las ventas del negocio (WhatsApp, bot y tienda física) del periodo elegido: '
        'ingreso y ganancia.',
        'Cambia el mes con el selector de arriba. La comparación contra el periodo anterior se muestra '
        'en valor absoluto y en % del mes.',
    ],
    'panel:home_config': [
        'Aquí se arma la portada de la tienda: los textos del hero, sus estadísticas y las imágenes '
        'que rotan arriba.',
        '«Destacados en Nuevos ingresos»: busca un producto por nombre, SKU o categoría y pulsa '
        '«Destacar» para que salga en esa sección. «Quitar» lo saca.',
        'Las portadas de categorías son las imágenes de las tarjetas de categoría de la página de inicio.',
    ],

    # ── PANEL · PEDIDOS WEB ─────────────────────────────────────────────────
    'panel:orders_list': [
        'Pedidos que los clientes hicieron en la tienda web. Busca por código, cliente o teléfono y '
        'filtra por estado o por rango de fechas.',
        'La campana de arriba avisa cuando entra un pedido web nuevo.',
        '«Exportar CSV» descarga la lista con los filtros que tengas puestos.',
    ],
    'panel:order_detail': [
        'Detalle de un pedido web: datos del cliente, productos, pagos y estado. Cada cambio a los '
        'productos queda registrado con quién y cuándo lo hizo.',
        'Puedes agregar, editar o quitar productos. El precio que sugiere al agregar es el de hoy y '
        'se puede editar.',
        'Registra cada pago con su método; «Liquidar» registra el saldo restante de una vez. '
        'Al cambiar el estado puedes poner la URL de rastreo (17track) y enviar el ticket.',
    ],
    'panel:ventas_web': [
        'Lo que se ha vendido en la tienda web, producto por producto: unidades, ingreso, costo y '
        'ganancia. Los pedidos cancelados no cuentan.',
        'Filtra por rango de fechas y pulsa el título de una columna para ordenar por ella.',
        'Pulsa un producto para ver los pedidos donde se vendió.',
    ],
    'panel:ventas_web_producto': [
        'Historial de un producto: cada renglón vendido en la tienda web, con su pedido, cantidad, '
        'ingreso, costo y ganancia.',
        'Pulsa el código del pedido para abrir su detalle.',
    ],
    'panel:ventas_web_borrado': [
        'Historial de un producto que ya se borró del catálogo, agrupado por su SKU. Las cifras salen '
        'de lo que quedó grabado en cada pedido.',
        'Pulsa el código del pedido para abrir su detalle.',
    ],
    'panel:supplier_order_detail': [
        'Arma el pedido en la página del proveedor (modaverse) a partir de los productos de este pedido.',
        '«Ejecutar en servidor» lo hace automático y muestra el avance en el log. Si un ítem falla, '
        'revísalo y pulsa «Reintentar fallidos».',
        'Si lo prefieres a mano: abre modaverse en tu navegador, copia el script y pégalo allí para '
        'importar el carrito.',
    ],

    # ── PANEL · PRODUCTOS ───────────────────────────────────────────────────
    'panel:products_list': [
        'Todos los productos del catálogo. Busca por nombre o SKU y filtra por categoría, por activos '
        'o inactivos, o por los que no tienen foto.',
        'Marca varios (o «Seleccionar todos los resultados del filtro») para activarlos, '
        'desactivarlos, eliminarlos o cambiarles el precio base de una vez.',
        'Un producto inactivo no se ve en la tienda pero sigue guardado. Eliminar sí lo borra.',
    ],
    'panel:product_create': [
        'Alta de un producto. SKU, nombre, precio base y categoría son obligatorios.',
        'Si dejas vacío el override de envío o el precio final, se usan los de la categoría y el '
        'precio se calcula solo.',
        'Después de crearlo puedes subir las fotos. Marca «colorway» si cada foto es un color distinto '
        'del mismo producto.',
    ],
    'panel:product_edit': [
        'Edita los datos, precios y fotos del producto. «Producto activo» decide si se ve en la tienda.',
        'Con «colorway» cada foto es un color: ponle el nombre del color, que es el que ve el cliente '
        'en el carrito.',
        'Las tallas personalizadas sustituyen el grupo de tallas de la subcategoría solo para este producto.',
    ],
    'panel:pendientes_list': [
        'Productos que llegaron del proveedor y esperan revisión antes de salir en la tienda.',
        'Revisa el nombre para el catálogo y el precio de venta (costo del proveedor + envío + '
        'ganancia). Aprueba o rechaza uno por uno, en grupo o toda la página.',
        'Las pestañas Aprobados y Rechazados guardan el historial por si hay que corregir una decisión.',
    ],

    # ── PANEL · CATÁLOGO ────────────────────────────────────────────────────
    'panel:catalog_config': [
        'Estructura del catálogo: categorías raíz y sus subcategorías. Arrastra para cambiar el orden '
        'en que salen en la tienda.',
        'Desde cada tarjeta puedes activar u ocultar, poner imagen, elegir el grupo de tallas, '
        'activar colorway y escribir un aviso que el cliente ve al entrar.',
        'Las subcategorías heredan de la categoría padre lo que no tengan configurado.',
    ],
    'panel:category_create': [
        'Crea una categoría. El slug (la parte de la URL) se genera solo si lo dejas vacío.',
        'Precio de venta = costo + envío + margen de ganancia. Si dejas el margen vacío se usa el de '
        'la categoría raíz.',
        '«Mínimo total» es la cantidad de piezas que exige la categoría por pedido.',
    ],
    'panel:category_edit': [
        'Edita precios, reglas, imagen y estado de la categoría.',
        'Los descuentos por volumen restan un monto en MXN al precio de cada producto cuando el pedido '
        'llega a cierta cantidad. Agrega un tier por cada escalón.',
        'Cambiar el margen o el envío mueve el precio de todos los productos que no tengan uno propio. '
        '«Eliminar categoría» está en la zona de peligro.',
    ],
    'panel:subcategory_products_reorder': [
        'Orden en que aparecen los productos de esta subcategoría en la tienda.',
        'Arrastra cada producto a su lugar. El cambio se guarda solo.',
    ],
    'panel:sizes_list': [
        'Grupos de tallas reutilizables (ej. «Gorras numéricas»). Se asignan a una categoría o '
        'subcategoría y todos sus productos los usan.',
        'Escribe cada talla y presiona Enter o coma. Arrástralas para ordenarlas.',
        'La tabla de conversión es opcional: agrega columnas (MX, US…) y filas para mostrar equivalencias.',
    ],
    'panel:size_group_create': [
        'Nuevo grupo de tallas: ponle nombre y agrega las tallas en el orden en que deben salir.',
        'Después asígnalo a la categoría o subcategoría desde Catálogo.',
    ],
    'panel:size_group_edit': [
        'Edita las tallas o la tabla de conversión del grupo.',
        'El cambio afecta a todas las subcategorías y productos que usan este grupo.',
    ],
    'panel:sections_config': [
        'Secciones de la página de cada categoría raíz (ej. «Marcas de lujo»). Sirven para agrupar '
        'subcategorías en bloques.',
        'Crea la sección, agrégale subcategorías y ordénala. Una subcategoría sin sección sigue '
        'saliendo normal.',
    ],
    'panel:subsection_config': [
        'Secciones dentro de una subcategoría (ej. «Edición Limitada») para agrupar sus productos.',
        'Crea la sección y agrégale productos. El orden de los productos se cambia en «la lista de productos».',
    ],

    # ── PANEL · WHATSAPP ────────────────────────────────────────────────────
    'panel:whatsapp_qr_list': [
        'Estado de cada bot de WhatsApp: conectado, esperando escaneo, sesión cerrada o reconectando.',
        'Si un bot pide escaneo, entra a su tarjeta y escanea el QR desde WhatsApp > Dispositivos vinculados.',
        '«Sin datos» suele significar que el servicio del bot está detenido. Esta pantalla no lo '
        'arranca: hay que levantarlo en el servidor.',
    ],
    'panel:whatsapp_qr_detail': [
        'QR para vincular este bot. En el teléfono abre WhatsApp > Dispositivos vinculados > Vincular '
        'y escanéalo.',
        'El QR cambia cada pocos segundos; si caduca, espera el siguiente. Al conectar, el estado cambia solo.',
    ],

    # ── NEGOCIO · RESUMEN Y REPORTES ────────────────────────────────────────
    'negocio:resumen': [
        'Dashboard financiero del negocio: ingresos por método de pago, gastos por categoría y '
        'pedidos con saldo pendiente.',
        '«Ganancia neta» es lo que se gana con lo vendido (precio menos costo, menos gastos). '
        '«Flujo de caja» es el dinero que entró y salió. Pueden no coincidir.',
        'Elige el mes arriba. «Pendientes de cobro» lleva directo a cada pedido para registrar el pago.',
    ],
    'negocio:mas_vendidos': [
        'Lo más vendido del negocio agrupado por tipo de artículo. Ordena por piezas, ingreso o '
        'ganancia con los encabezados.',
        'Abre un tipo para ver los textos exactos con que se tecleó y su tendencia contra el periodo anterior.',
        'Si hay ventas sin tipo, asígnales una keyword o un alias en Tipos para que cuenten aquí.',
    ],
    'negocio:mayoreo_menudeo': [
        'Pares, ingreso y ganancia de mayoreo contra menudeo, con totales del periodo, por mes y por día.',
        'Cada venta se clasifica con el selector Menudeo / Mayoreo de la pantalla Vender del escáner. '
        'Las ventas del bot y las de antes del selector salen como «Sin clasificar».',
        'Solo cuenta pedidos pagados, por la fecha del pedido. Sin fechas muestra el mes en curso.',
    ],
    'negocio:contabilidad': [
        'Ingresos, egresos y diferencia mes por mes.',
        '«Descargar Excel» exporta el periodo para el contador.',
    ],

    # ── NEGOCIO · CLIENTES ──────────────────────────────────────────────────
    'negocio:clientes_list': [
        'Clientes del negocio con su descuento y lo que deben.',
        'El teléfono identifica al cliente: no puede haber dos con el mismo número.',
    ],
    'negocio:cliente_create': [
        'Alta de un cliente: nombre y teléfono (único).',
        'El descuento es el que el bot de WhatsApp le aplica al cotizar, buscándolo por su teléfono. '
        'Déjalo en 0 si no tiene.',
    ],
    'negocio:cliente_edit': [
        'Edita los datos, el descuento o las notas del cliente.',
        'Cambiar el descuento no modifica los pedidos que ya están registrados.',
    ],
    'negocio:cliente_detail': [
        'Ficha del cliente: sus datos y todos sus pedidos con lo que debe en cada uno.',
        '«+ Nuevo pedido» crea un pedido ya asignado a este cliente.',
    ],

    # ── NEGOCIO · PEDIDOS Y PAGOS ───────────────────────────────────────────
    'negocio:pedidos_list': [
        'Pedidos del negocio: los que registra el bot de WhatsApp, la tienda física y los que se '
        'capturan a mano.',
        'Filtra por estado o rango de fechas. «Por cobrar» es lo que falta pagar de cada uno.',
    ],
    'negocio:pedido_create': [
        'Captura manual de un pedido: cliente, descripción, costo, precio de venta y envío.',
        'El costo es el que define la ganancia: ponlo bien. Los pagos se registran después, en el '
        'detalle del pedido.',
    ],
    'negocio:pedido_detail': [
        'Detalle del pedido: ítems, pagos recibidos y gastos asociados.',
        'Registra cada pago con su método. Cuando los pagos cubren el total, el pedido pasa a pagado.',
        'Si un ítem quedó mal (precio, cantidad o costo), edítalo desde su fila.',
    ],
    'negocio:pedido_item_edit': [
        'Corrige descripción, cantidad, precio o costo de un ítem.',
        'El costo grabado del ítem no cambia aunque después cambie el costo del tipo: solo se corrige '
        'aquí, a mano.',
    ],
    'negocio:pagos_list': [
        'Todos los pagos recibidos. Filtra por rango de fechas.',
        'Para corregir un pago entra a su pedido y edítalo desde ahí.',
    ],
    'negocio:pago_edit': [
        'Corrige monto, método o notas de un pago.',
        'Cambiar un pago mueve el saldo del pedido y el total en caja.',
    ],
    'negocio:gastos_list': [
        'Historial de gastos del negocio con su categoría.',
        'Registra cada gasto en cuanto sale el dinero: se resta de la caja y de la ganancia neta.',
    ],
    'negocio:caja': [
        'Saldo de caja que calcula el sistema: lo cobrado menos los gastos más los ajustes.',
        'Para cuadrar, cuenta el efectivo real, escribe el TOTAL contado y un motivo. El sistema guarda '
        'la diferencia como ajuste, con quién y cuándo.',
        'El saldo inicial (el efectivo que había antes de usar el sistema) es simplemente el primer ajuste.',
    ],

    # ── NEGOCIO · IMPRESIÓN ─────────────────────────────────────────────────
    'negocio:etiquetas_list': [
        'Imprime etiquetas de productos. Busca, marca los que necesitas y pulsa «Imprimir seleccionados».',
        'La impresión va a la impresora de etiquetas conectada por USB.',
    ],

    # ── NEGOCIO · TIPOS Y CÓDIGOS ───────────────────────────────────────────
    'negocio:tipos_list': [
        'Un tipo de artículo agrupa lo que se vende por el bot y fija su costo. El bot reconoce el tipo '
        'por sus keywords en el texto de la venta.',
        'Arriba salen los textos tecleados que no encontraron tipo. Asígnales una keyword o, si ninguna '
        'cabe, el texto exacto con «Asignar directo» (un alias).',
        'Una venta nueva sin tipo ya no se graba: el bot la rechaza y pide el tipo en el grupo.',
    ],
    'negocio:tipo_create': [
        'Nuevo tipo: nombre, costo y keywords.',
        'Separa las keywords con COMAS, no con saltos de línea. Usa palabras específicas: '
        'una keyword corta puede coincidir con ventas de otro tipo.',
    ],
    'negocio:tipo_edit': [
        'Edita nombre, costo o keywords del tipo.',
        'El costo nuevo aplica solo a las ventas que se graben desde ahora. Las ya registradas conservan '
        'el costo que tenían.',
    ],
    'negocio:tipo_delete': [
        'Confirma si quieres eliminar este tipo de artículo.',
        'Se borran también sus alias, y los códigos de descuento ligados a él pasan a aplicar a todo. '
        'Las ventas ya grabadas conservan su costo; las nuevas con ese texto se rechazarán hasta asignarles otro tipo.',
    ],
    'negocio:codigos_list': [
        'Códigos de descuento: dónde valen (negocio, web o ambos), a qué aplican (todo, un tipo o una '
        'categoría web), cuántos usos llevan y cuándo vencen.',
        'Un código vencido o sin usos disponibles deja de aceptarse solo.',
    ],
    'negocio:codigo_create': [
        'Nuevo código: «fijo» descuenta un monto del total; «por ítem» lo multiplica por cada artículo '
        'que entra en el alcance. Deja vacío el tipo o la categoría para que aplique a todo.',
        'Ponle fecha de vencimiento y límite de usos si es una promoción con fin. Si dejas el código '
        'vacío se genera uno solo.',
    ],
    'negocio:codigo_edit': [
        'Edita el descuento, la vigencia o el límite de usos del código.',
        'Desactívalo si quieres pausarlo sin perder su historial de usos.',
    ],
    'negocio:codigo_delete': [
        'Confirma si quieres eliminar este código de descuento.',
        'Si solo quieres pausarlo, mejor desactívalo desde editar.',
    ],
}

# Pantallas imprimibles: no usan el layout del panel, así que no tienen dónde
# mostrar la ayuda.
EXCLUDED_VIEWS = {
    'panel:order_ticket',
    'negocio:label_html',
    'negocio:etiquetas_print',
}


def get_help_cards(view_name, user):
    """Tarjetas de ayuda de la vista para este usuario.

    Vacío si no es staff, si apagó la ayuda o si la vista no tiene registro.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return []
    if not user.is_staff:
        return []
    profile = getattr(user, 'profile', None)
    if profile is not None and profile.help_dismissed:
        return []
    return list(HELP_REGISTRY.get(view_name, []))
