"""
Empaqueta imágenes locales buenas para reemplazar las corruptas en el servidor.

El servidor guarda archivos con sufijo aleatorio Django (RYL-CAP-3311_0_eYfLmTn.jpg)
pero localmente están como RYL-CAP-3311_0.jpg.
Match por base-name (SKU + índice), sin sufijo.

El arcname en el tar usa el nombre del servidor para que al extraer quede en el lugar correcto.
"""
import re
import tarfile
import pathlib

media = pathlib.Path('config/media/products')

with open('corrupted_list.txt', encoding='utf-8') as f:
    server_names = [l.strip() for l in f if l.strip()]

# Construir índice local: "RYL-CAP-3311_0" → Path
local_index: dict[str, pathlib.Path] = {}
for p in media.iterdir():
    if not p.is_file() or p.stat().st_size <= 500:
        continue
    # Extraer base sin sufijo: RYL-CAP-3311_0_eYfLmTn.jpg → RYL-CAP-3311_0
    m = re.match(r'^(RYL-\w+-\d+_\d+)', p.stem)
    if m:
        base = m.group(1)
        local_index.setdefault(base, p)  # primer match gana

ok = skip = 0
with tarfile.open('good_images.tar.gz', 'w:gz') as tar:
    for server_fname in server_names:
        m = re.match(r'^(RYL-\w+-\d+_\d+)', pathlib.Path(server_fname).stem)
        if not m:
            skip += 1
            continue
        base = m.group(1)
        local_path = local_index.get(base)
        if local_path:
            # arcname = nombre del servidor → el tar reemplaza el archivo correcto
            tar.add(local_path, arcname=server_fname)
            ok += 1
        else:
            skip += 1

print(f'Empaquetados: {ok} | Sin match local: {skip}')
