# Cultura Zaragoza (Flask)

Webapp en Flask para consultar la agenda cultural de Zaragoza combinando:

- Agenda en iframe de Rock & Blues Café (SweetCaroline)
- [Sala Creedence](https://creedencesound.com/sala-creedence-conciertos-y-sesiones)
- [Zaragoza Cultura (Ayuntamiento)](https://www.zaragoza.es/sede/servicio/cultura/)
- [Conciertos.Club Zaragoza](https://conciertos.club/zaragoza) (agenda transversal de conciertos y salas)
- [La Lata de Bombillas](https://lalatadebombillas.es/) (The Events Calendar) y su [web de venta de entradas](https://entradas.lalatadebombillas.es/web/?menu=36&pagina=&siteID=latadebombillas) (enlaces a taquilla propia, Dice, Enterticket, etc.)
- [El Refugio del Crápula](https://www.elcrapula.es/) (programación de comedia, monólogos y música)

> **Nota:** [Taquilla.com](https://www.taquilla.com/) no se usa: sus listados no filtran bien por ciudad (mezclan fechas de otras localidades). Cualquier evento cuyo enlace sea `taquilla.com` se descarta.


## Instalación

```bash
# desde el directorio del proyecto
source ~/.virtualenvs/pruebasIA/bin/activate
pip install -r requirements.txt
```

## Despliegue e instalación Android (APK)

Ver **[DEPLOY.md](DEPLOY.md)**: hosting gratuito en Render (HTTPS) + empaquetado con PWABuilder.

## Endpoint JSON

- `GET /api/events` — mismos filtros que la web (`date_from`, `date_to`, `category`, `venue`, `q`). `category` y `venue` admiten varios valores (repetidos o separados por comas).
- `GET /api/meta` — listado de categorías, salas y rango de la semana actual
- `GET /m` — app móvil / PWA
- `GET /.well-known/assetlinks.json` — Digital Asset Links (APK / TWA)

## Caché

Ficheros de eventos:

- `cache/rockandbluescafe_events.json`
- `cache/lalata_events.json`
- `cache/zaragoza_cultura_events.json`
- `cache/creedence_events.json`
- `cache/conciertos_club_events.json`
- `cache/elcrapula_events.json`
- `cache/venue_coords.json` (geocoding de salas)


- `EVENT_CACHE_TTL_SECONDS` (por defecto: 3600 segundos)
- `CENTROS_CIVICOS_HORIZON_DAYS` (por defecto: **90**; solo se incluyen actividades que se solapan con *hoy* → *hoy + N días*)
- `AGGREGATOR_MAX_WORKERS` (por defecto: **8**; hilos al combinar fuentes en paralelo)
- `AGGREGATOR_SEQUENTIAL=1` fuerza carga secuencial (solo depuración)

### Rendimiento

La primera petición tras arrancar el servidor puede tardar si alguna caché en disco ha caducado: se consultan **varias webs a la vez** (ahora en paralelo). Las siguientes peticiones usan caché en memoria y suelen ser rápidas. El mapa solo geocodifica las salas de los eventos **ya filtrados**; las coordenadas se reutilizan desde `cache/venue_coords.json`.


