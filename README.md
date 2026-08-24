# Cultura Zaragoza (Flask)

Webapp en Flask para consultar la agenda cultural de Zaragoza combinando:

- [Taquilla.com Zaragoza](https://www.taquilla.com/zaragoza)
- Agenda en iframe de Rock & Blues Café (SweetCaroline)
- [Sala Creedence](https://creedencesound.com/sala-creedence-conciertos-y-sesiones)
- [Zaragoza Cultura (Ayuntamiento)](https://www.zaragoza.es/sede/servicio/cultura/)
- [Centros Cívicos (Ayuntamiento)](https://www.zaragoza.es/sede/portal/centroscivicos/servicio/cultura/evento/list?idPortal=7) (solo temática música y teatro)
- [Conciertos.Club Zaragoza](https://conciertos.club/zaragoza) (agenda transversal de conciertos y salas)
- [La Lata de Bombillas](https://lalatadebombillas.es/) (The Events Calendar) y su [web de venta de entradas](https://entradas.lalatadebombillas.es/web/?menu=36&pagina=&siteID=latadebombillas) (enlaces a taquilla propia, Dice, Enterticket, etc.)

Todo con scraping y caché en disco.

En la interfaz puedes **marcar los eventos** que quieras y pulsar **Guardar** para descargar un **PDF** con cabecera de filtros y el listado ordenado por **fecha**, **lugar** y **título** (requiere `fpdf2`, ver `requirements.txt`).

## Requisitos

- Entorno virtual: `pruebasIA` en `~/.virtualenvs/pruebasIA`
- Python 3

## Instalación

```bash
# desde el directorio del proyecto
source ~/.virtualenvs/pruebasIA/bin/activate
pip install -r requirements.txt
```

Si al guardar el PDF ves un error de módulo `fpdf`, falta instalar dependencias: el paquete se llama **`fpdf2`** en pip (`pip install fpdf2`), aunque en Python se importa como `from fpdf import FPDF`.

## Ejecutar

```bash
source ~/.virtualenvs/pruebasIA/bin/activate
cd /home/belobal/workspace/cultura_zaragoza
python app.py
```

Abrir:
- `http://localhost:5000/` — web completa
- `http://localhost:5000/m` — **app móvil** (PWA): agenda a **7 días**, selección de **lugares** y **tipos** de evento

En el móvil puedes añadir la app a la pantalla de inicio (manifest + service worker).

## Despliegue e instalación Android (APK)

Ver **[DEPLOY.md](DEPLOY.md)**: hosting gratuito en Render (HTTPS) + empaquetado con PWABuilder.

## Endpoint JSON

- `GET /api/events` — mismos filtros que la web (`date_from`, `date_to`, `category`, `venue`, `q`, `centros`). `category` y `venue` admiten varios valores (repetidos o separados por comas).
- `GET /api/meta` — listado de categorías, salas y rango de la semana actual
- `GET /m` — app móvil / PWA
- `GET /.well-known/assetlinks.json` — Digital Asset Links (APK / TWA)

## Caché

Ficheros de eventos:

- `cache/events.json` (Taquilla)
- `cache/rockandbluescafe_events.json`
- `cache/lalata_events.json`
- `cache/zaragoza_cultura_events.json`
- `cache/creedence_events.json`
- `cache/centros_civicos_events.json`
- `cache/conciertos_club_events.json`
- `cache/venue_coords.json` (geocoding de salas)

Para forzar datos nuevos: espera al **TTL** (`EVENT_CACHE_TTL_SECONDS`), **reinicia** el servidor o borra manualmente los `*.json` de eventos en `cache/` (no hace falta tocar `venue_coords.json`).

- `EVENT_CACHE_TTL_SECONDS` (por defecto: 3600 segundos)
- `CENTROS_CIVICOS_HORIZON_DAYS` (por defecto: **90**; solo se incluyen actividades que se solapan con *hoy* → *hoy + N días*)
- `AGGREGATOR_MAX_WORKERS` (por defecto: **8**; hilos al combinar fuentes en paralelo)
- `AGGREGATOR_SEQUENTIAL=1` fuerza carga secuencial (solo depuración)

### Rendimiento

La primera petición tras arrancar el servidor puede tardar si alguna caché en disco ha caducado: se consultan **varias webs a la vez** (ahora en paralelo). Las siguientes peticiones usan caché en memoria y suelen ser rápidas. El mapa solo geocodifica las salas de los eventos **ya filtrados**; las coordenadas se reutilizan desde `cache/venue_coords.json`.

### La Lata: no salen conciertos

En **lalatadebombillas.es** el listado del calendario a veces solo muestra **eventos antiguos**; los próximos van en **[entradas.lalatadebombillas.es](https://entradas.lalatadebombillas.es/web/?menu=36&pagina=&siteID=latadebombillas)**. Si la caché solo tenía fechas pasadas, el filtro por defecto (desde hoy, 7 días) dejaba **0 resultados** hasta que caducaba la caché o se reiniciaba. El scraper ignora cachés que ya solo contienen fechas pasadas y vuelve a pedir datos.

