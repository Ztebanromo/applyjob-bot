# ApplyJob Bot

Motor de automatización universal de postulaciones laborales. **Python + Playwright**. Aplica a empleos en múltiples portales con persistencia de sesión, deduplicación, rate limiting, retry automático y validación de configuración.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-1.44-green)](https://playwright.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## Características

| Característica | Descripción |
|---|---|
| **Multi-portal** | LinkedIn, Indeed, Computrabajo, GetOnBrd — extensible a cualquier sitio con selectores CSS |
| **LinkedIn Easy Apply** | Flujo multi-step completo: pasos, dropdowns, screening, skip de procesos largos (>6 pasos) |
| **Deduplicación** | SQLite evita postular dos veces entre runs + historial permanente con estadísticas |
| **Sesión persistente** | Las cookies se guardan por portal — una sola sesión manual, runs automáticos para siempre |
| **Rate limiting** | Máx 10/hora en LinkedIn, 15 en Indeed — ventana deslizante, espera en lugar de fallar |
| **Retry automático** | Reintenta 1 vez ante errores de red/timeout antes de marcar como error |
| **Validación al arrancar** | Verifica USER_PROFILE y selectores antes de abrir el browser — falla rápido con mensaje claro |
| **Anti-detección** | playwright-stealth + delays aleatorios + mouse path natural + scripts de evasión |
| **Autocompletado** | Detecta campos por `name`/`id`/`placeholder`/`aria-label`/`data-testid` |
| **Chrome auto-detectado** | Usa Chrome del sistema si Playwright Chromium no está descargado |
| **Logging rotado** | `applyjob.log` con rotación diaria + CSV por día + SQLite |
| **Manejo de errores** | Screenshot automático + log completo + salta a la siguiente oferta sin detener el run |

---

## Instalación

```bash
# 1. Clonar
git clone https://github.com/Ztebanromo/applyjob-bot.git
cd applyjob-bot

# 2. Entorno virtual
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / Mac
source .venv/bin/activate

# 3. Dependencias
pip install -r requirements.txt

# 4. Browser (opcional — si falla, el bot usa Chrome del sistema automáticamente)
playwright install chromium
```

> **Nota:** si tenés Chrome instalado en el sistema (`C:\Program Files\Google\Chrome\Application\chrome.exe`),
> el bot lo detecta automáticamente y no necesitás descargar Chromium.

---

## Configuración rápida

### 1. Completá tu perfil en `bot/config.py`

```python
USER_PROFILE = {
    # Obligatorios
    "full_name":    "Juan Pérez",
    "first_name":  "Juan",
    "last_name":   "Pérez",
    "email":        "juan@gmail.com",
    "phone":        "+54 9 11 1234 5678",

    # Recomendados
    "city":         "Buenos Aires, Argentina",
    "linkedin":     "https://linkedin.com/in/juanperez",
    "portfolio":    "https://juanperez.dev",  # opcional
    "cv_path":      "C:/Users/Juan/Documents/CV_Juan_Perez.pdf",  # ruta absoluta al PDF

    # Datos laborales (usados en preguntas de screening)
    "salary":       "3000",       # pretensión salarial
    "years_exp":    "3",          # años de experiencia
    "cover_letter": "Estoy muy interesado en esta posición y creo que mi experiencia...",
}
```

### 2. Actualizá la URL de búsqueda de LinkedIn

En `bot/config.py` → `SITE_CONFIG["linkedin"]["url_busqueda"]`:

1. Andá a LinkedIn Jobs en tu browser
2. Aplicá tus filtros (keywords, ubicación, nivel, etc.)
3. Activá el filtro **"Easy Apply"** (agrega `f_AL=true` a la URL)
4. Copiá la URL completa y pegala en el config

```python
"url_busqueda": "https://www.linkedin.com/jobs/search/?keywords=Python+Developer&location=Remote&f_AL=true"
```

### 3. Variables de entorno (opcional)

```bash
cp .env.example .env
# Editar .env con tu editor favorito
```

---

## Uso

```bash
# Ver portales disponibles
python main.py --list-portals

# Validar configuración sin abrir el browser
python main.py --validate --portal linkedin

# Dry-run — navega sin postular (ideal para el primer uso y verificar selectores)
python main.py --portal linkedin --dry-run

# Postular en LinkedIn (máx 15 Easy Apply por run)
python main.py --portal linkedin

# Con límite custom
python main.py --portal linkedin --max 5

# Sin ventana de browser
python main.py --portal computrabajo --headless

# Ver estadísticas de postulaciones
python main.py --stats

# Limpiar registros skipped/dry_run de más de 90 días
python main.py --purge --days 90
```

Ver todos los comandos en [docs/06_cli_reference.md](docs/06_cli_reference.md).

---

## LinkedIn: primer uso paso a paso

LinkedIn requiere sesión activa. El **primer run SIEMPRE debe hacerse sin `--headless`**:

```bash
# Paso 1: Dry-run sin headless para iniciar sesión
python main.py --portal linkedin --dry-run

# El browser abre → iniciás sesión en LinkedIn manualmente
# La sesión queda guardada en sessions/linkedin/

# Paso 2: Validar que todo funciona
python main.py --validate --portal linkedin

# Paso 3: Run real con pocos para probar
python main.py --portal linkedin --max 3

# Paso 4: A partir de ahora podés usar --headless
python main.py --portal linkedin --max 15 --headless
```

> La URL debe incluir `f_AL=true` para filtrar solo Easy Apply.
> Sin ese parámetro el bot encontrará empleos externos y los saltará como `skipped_no_easy_apply`.

Ver la guía completa en [docs/03_linkedin.md](docs/03_linkedin.md).

---

## Agregar un portal nuevo

**En 5 minutos** con selectores CSS:

1. Abrí el portal en tu browser → F12 → Inspector
2. Identificá los selectores de: card de oferta, botón postular, botón "siguiente página", título del puesto
3. Agregá la entrada en `bot/config.py`:

```python
SITE_CONFIG = {
    "mi_portal": {
        # Obligatorios
        "url_busqueda":           "https://miportal.com/jobs?q=python",
        "selector_oferta":        "div.job-card",
        "selector_boton_aplicar": "button.apply-btn",
        "tipo_postulacion":       "directa",   # directa | modal | externa

        # Opcionales
        "selector_siguiente_pagina": "a.next-page",
        "selector_titulo_oferta":    "h1.job-title",
        "max_offers_per_run":        15,
        "requires_login":            False,
    },
}
```

| `tipo_postulacion` | Cuándo usarlo |
|---|---|
| `directa` | Click → formulario en la misma página → submit |
| `modal` | Click → overlay/modal → fill × N pasos → submit |
| `externa` | Click → redirige a otro sitio (se loguea como `external:url`) |

Ver la guía completa en [docs/04_adding_portals.md](docs/04_adding_portals.md).

---

## Estructura del proyecto

```
applyjob-bot/
│
├── bot/                        # Paquete principal
│   ├── config.py               # USER_PROFILE + SITE_CONFIG  ← editar aquí
│   ├── engine.py               # Orquestador: run_bot()
│   ├── state.py                # Persistencia SQLite + estadísticas
│   ├── form_filler.py          # Autocompletado genérico de formularios
│   ├── stealth_utils.py        # Anti-detección: delays, mouse, scripts
│   ├── retry.py                # with_retry() + RateLimiter por portal
│   ├── validator.py            # Validación fail-fast al arrancar
│   ├── logger.py               # Logging centralizado con rotación diaria
│   └── portals/
│       ├── base.py             # Clase abstracta BasePortal
│       ├── linkedin.py         # LinkedIn Easy Apply (flujo multi-step)
│       └── __init__.py         # Registry de portales
│
├── sessions/                   # Cookies de sesión por portal  (gitignored)
├── errors/                     # Screenshots de errores        (gitignored)
├── logs/                       # CSVs diarios + applyjob.log  (gitignored)
├── data/                       # applyjob.db — SQLite         (gitignored)
│
├── main.py                     # Entry point CLI
├── requirements.txt
├── .env.example
│
├── docs/                       # Documentación técnica
│   ├── 01_getting_started.md   # Instalación y primer run
│   ├── 02_configuration.md     # Referencia completa de configuración
│   ├── 03_linkedin.md          # Guía LinkedIn Easy Apply
│   ├── 04_adding_portals.md    # Cómo agregar portales nuevos
│   ├── 05_architecture.md      # Arquitectura y decisiones de diseño
│   └── 06_cli_reference.md     # Referencia completa de comandos CLI
│
├── README.md
├── ROADMAP.md
└── TROUBLESHOOTING.md
```

---

## Rate Limiting

El bot controla automáticamente el ritmo para evitar detección:

| Portal | Límite | Ventana |
|---|---|---|
| LinkedIn | 10 acciones | 60 minutos |
| Indeed | 15 acciones | 60 minutos |
| Computrabajo | 25 acciones | 60 minutos |
| GetOnBrd | 20 acciones | 60 minutos |

Cuando se llega al límite, el bot **espera** hasta que se libere un slot (ventana deslizante).
Para ajustar: editar `RATE_LIMITS` en `bot/retry.py`.

---

## Logs y estadísticas

### Ver estadísticas en consola

```bash
python main.py --stats
```

```
====================================================
  ApplyJob Stats  —  Total: 47
====================================================

  linkedin
    applied                      23  ███████████████████████
    skipped_no_easy_apply         8  ████████
    skipped_complex_7_steps       2  ██

  Últimas 5 postulaciones:
    [2024-05-15] linkedin    applied    Senior Python Dev
```

### Archivos generados

| Archivo | Contenido |
|---|---|
| `logs/applied_YYYY-MM-DD.csv` | Log diario legible por humanos |
| `logs/applyjob.log` | Log rotado — rotación diaria, retención 30 días |
| `data/applyjob.db` | SQLite con historial completo para deduplicación |
| `errors/*.png` | Screenshots automáticos de ofertas con error |

---

## Manejo de errores

Cuando un selector no se encuentra o una acción falla:

1. Se loguea el error con stack trace completo en `logs/applyjob.log`
2. Se toma un screenshot en `errors/portal_contexto_timestamp.png`
3. Si el error es de red → se reintenta 1 vez automáticamente
4. El resultado se registra en SQLite y CSV con `status = "error: ..."`
5. El bot **salta a la siguiente oferta** sin detener el run

Ver diagnóstico completo en [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## Documentación

| Documento | Contenido |
|---|---|
| [docs/01_getting_started.md](docs/01_getting_started.md) | Instalación, configuración inicial, primer run |
| [docs/02_configuration.md](docs/02_configuration.md) | Referencia completa USER_PROFILE y SITE_CONFIG |
| [docs/03_linkedin.md](docs/03_linkedin.md) | Guía LinkedIn Easy Apply: login, URL, flujo, estados |
| [docs/04_adding_portals.md](docs/04_adding_portals.md) | Cómo agregar portales nuevos (genérico o clase dedicada) |
| [docs/05_architecture.md](docs/05_architecture.md) | Arquitectura, flujo de datos, decisiones de diseño |
| [docs/06_cli_reference.md](docs/06_cli_reference.md) | Todos los comandos CLI con ejemplos y códigos de salida |
| [ROADMAP.md](ROADMAP.md) | Plan de 11 fases, estado actual y esfuerzo estimado |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Diagnóstico y solución de 12 escenarios de error comunes |

---

## Consideraciones de uso

- **No usar `--headless`** en el primer run de portales con `requires_login: True`
- **No subir `sessions/` ni `data/` a git** — están en `.gitignore`
- **LinkedIn detecta ráfagas**: el rate limiter es la primera línea de defensa
- **LinkedIn Easy Apply** salta automáticamente modales con más de 6 pasos
- Si aparecen muchos `skipped_captcha`: esperá 24h y reducí el `max` por run

---

## Roadmap

| Fase | Feature | Estado |
|---|---|---|
| F1–F4 | Motor base, stealth, deduplicación, LinkedIn | ✅ Completado |
| F5 | Retry + rate limiting + validación + logging | ✅ Completado |
| F6 | Notificaciones (email + webhook Discord/ntfy) | 🔲 Pendiente |
| F7 | Scheduler autónomo (daemon con horarios) | 🔲 Pendiente |
| F8 | Dashboard web HTML | 🔲 Pendiente |
| F9–F11 | Anti-detección avanzada, Indeed, Docker | 🔲 Pendiente |

Ver detalle completo en [ROADMAP.md](ROADMAP.md).

---

## Licencia

MIT
