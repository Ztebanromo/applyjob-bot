# ApplyJob Bot

Motor de automatización universal de postulaciones laborales. Python + Playwright. Aplica a empleos en múltiples portales con persistencia de sesión, deduplicación, rate limiting, retry automático y validación de configuración.

---

## Características

| Característica | Descripción |
|---|---|
| **Multi-portal** | LinkedIn, Indeed, Computrabajo, GetOnBrd — extensible a cualquier sitio |
| **LinkedIn Easy Apply** | Flujo multi-step completo: pasos, dropdowns, screening, skip de procesos largos |
| **Deduplicación** | SQLite evita postular dos veces entre runs + historial permanente |
| **Sesión persistente** | Las cookies se guardan por portal — una sola sesión manual, runs automáticos para siempre |
| **Rate limiting** | Máx 10/hora en LinkedIn, 15 en Indeed — configurable por portal |
| **Retry automático** | Reintenta 1 vez ante errores de red/timeout antes de marcar como error |
| **Validación al arrancar** | Verifica USER_PROFILE y selectores antes de abrir el browser |
| **Anti-detección** | playwright-stealth + delays aleatorios + mouse path natural |
| **Autocompletado** | Detecta campos por `name`/`id`/`placeholder`/`aria-label` |
| **Logging rotado** | `applyjob.log` con rotación diaria + CSV por día + SQLite |
| **Manejo de errores** | Screenshot automático + log completo + salta a la siguiente oferta |

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

# 4. Browser
playwright install chromium
```

---

## Configuración rápida

### 1. Completa tu perfil en `bot/config.py`

```python
USER_PROFILE = {
    "full_name":    "Juan Pérez",
    "email":        "juan@gmail.com",
    "phone":        "+54 9 11 1234 5678",
    "city":         "Buenos Aires, Argentina",
    "linkedin":     "https://linkedin.com/in/juanperez",
    "cv_path":      "C:/Users/Juan/Documents/CV_Juan_Perez.pdf",
    "salary":       "3000",
    "years_exp":    "3",
    "cover_letter": "Estoy muy interesado en esta posición...",
}
```

### 2. Variables de entorno (opcional)

```bash
cp .env.example .env
# Editar .env con tu editor
```

### 3. Validar antes de correr

```bash
python main.py --validate --portal linkedin
```

---

## Uso

```bash
# Ver portales disponibles
python main.py --list-portals

# Dry-run — navega sin postular (para verificar selectores)
python main.py --portal computrabajo --dry-run

# Postular en LinkedIn (máx 15 Easy Apply por run)
python main.py --portal linkedin

# Con límite custom
python main.py --portal indeed --max 20

# Sin ventana de browser
python main.py --portal computrabajo --headless

# Estadísticas de postulaciones
python main.py --stats

# Limpiar registros skipped/dry_run de más de 90 días
python main.py --purge --days 90
```

---

## LinkedIn: primer uso

LinkedIn requiere sesión activa. El **primer run** debe hacerse **sin `--headless`**:

```bash
python main.py --portal linkedin --dry-run
```

El browser abre. Iniciás sesión manualmente. La sesión queda guardada en `sessions/linkedin/` y se reutiliza en todos los runs futuros (incluyendo `--headless`).

> La URL de búsqueda debe incluir `f_AL=true` para filtrar solo Easy Apply:
> `https://www.linkedin.com/jobs/search/?keywords=Python+Developer&f_AL=true`

---

## Agregar un portal nuevo

1. Abrí DevTools en el portal (F12 → Inspector)
2. Identificá los selectores CSS de cada elemento
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
        "selector_siguiente_pagina": "a.next-page",  # None si no hay paginación
        "selector_titulo_oferta":    "h1.job-title",
        "max_offers_per_run":        15,
        "requires_login":            False,
    },
}
```

| `tipo_postulacion` | Cuándo usarlo |
|---|---|
| `directa` | Click → formulario en la misma página → submit |
| `modal` | Click → overlay/modal se abre → submit dentro del modal |
| `externa` | Click → redirige a otro sitio (se loguea como `external:url`) |

---

## Estructura del proyecto

```
applyjob-bot/
├── bot/
│   ├── config.py          # USER_PROFILE + SITE_CONFIG (editar aquí)
│   ├── engine.py          # Motor principal run_bot()
│   ├── state.py           # Deduplicación y estadísticas SQLite
│   ├── form_filler.py     # Autocompletado inteligente de formularios
│   ├── stealth_utils.py   # Anti-detección: delays, mouse, stealth scripts
│   ├── retry.py           # Retry decorator + RateLimiter por portal
│   ├── validator.py       # Validación de config al arrancar
│   ├── logger.py          # Logging centralizado con rotación diaria
│   └── portals/
│       ├── base.py        # Clase abstracta BasePortal
│       └── linkedin.py    # LinkedIn Easy Apply — flujo multi-step completo
├── sessions/              # Sesiones de browser por portal (cookies)
├── errors/                # Screenshots automáticos de errores
├── logs/                  # CSVs diarios + applyjob.log (rotación 30 días)
├── data/                  # applyjob.db — historial SQLite
├── main.py                # CLI
├── requirements.txt
├── .env.example
├── README.md
├── ROADMAP.md
└── TROUBLESHOOTING.md
```

---

## Rate Limiting

El bot controla automáticamente el ritmo de postulaciones para evitar detección:

| Portal | Límite por hora |
|---|---|
| LinkedIn | 10 / hora |
| Indeed | 15 / hora |
| Computrabajo | 25 / hora |
| GetOnBrd | 20 / hora |

Cuando se llega al límite, el bot **espera** en lugar de fallar. Verás en el log:
```
[WARNING] Rate limit: 10/10 acciones en la última hora. Esperando 1847 segundos…
```

Para ajustar: editar `RATE_LIMITS` en `bot/retry.py`.

---

## Logs y estadísticas

### Ver estadísticas

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

  computrabajo
    applied                      14  ██████████████
```

### Archivos de log

| Archivo | Contenido |
|---|---|
| `logs/applied_YYYY-MM-DD.csv` | Registro diario legible por humanos |
| `logs/applyjob.log` | Log rotado — rotación diaria, retención 30 días |
| `data/applyjob.db` | SQLite con historial completo para deduplicación |
| `errors/*.png` | Screenshots de ofertas que fallaron |

---

## Manejo de errores

Cuando un selector no se encuentra o una acción falla:
1. Se loguea el error con contexto completo
2. Se toma un screenshot en `errors/portal_contexto_timestamp.png`
3. Si el error es de red → se reintenta 1 vez automáticamente
4. El error se registra en SQLite y CSV con `status = "error: ..."`
5. El bot salta a la siguiente oferta **sin detenerse**

---

## Documentación adicional

- **[ROADMAP.md](ROADMAP.md)** — Plan de 11 fases, estado y esfuerzo estimado
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Guía de diagnóstico y solución de problemas

---

## Consideraciones

- No uses `--headless` en el primer run de portales que requieren login
- No subas `sessions/` ni `data/` a git (están en `.gitignore`)
- LinkedIn detecta ráfagas: el rate limiter es tu primera línea de defensa
- LinkedIn Easy Apply salta automáticamente modales con >6 pasos
- Si ves muchos `skipped_captcha`: esperá 24h y reducí el max por run

---

## Licencia

MIT
