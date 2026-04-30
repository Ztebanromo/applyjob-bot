# ApplyJob Bot

Motor de automatización universal de postulaciones laborales. Basado en Python + Playwright, aplica a empleos en múltiples portales usando selectores CSS configurables, persistencia de sesión, evasión anti-bot y deduplicación automática.

---

## Características

- **Multi-portal**: LinkedIn, Indeed, Computrabajo, GetOnBrd y cualquier sitio nuevo con solo agregar selectores
- **LinkedIn Easy Apply nativo**: flujo multi-step completo con detección de pasos, dropdowns y preguntas de screening
- **Deduplicación**: base de datos SQLite evita postular dos veces a la misma oferta entre runs
- **Sesión persistente**: las cookies se mantienen entre ejecuciones (no hace falta re-iniciar sesión)
- **Anti-detección**: playwright-stealth + delays aleatorios + mouse path natural
- **Autocompletado de formularios**: detecta campos por `name`, `id`, `placeholder`, `aria-label`
- **Manejo de errores**: screenshot automático + salto a la siguiente oferta sin detener el proceso
- **Logs dobles**: CSV legible por humanos + SQLite para queries

---

## Instalación rápida

```bash
# 1. Clonar el repo
git clone https://github.com/ztebanromoo/applyjob-bot.git
cd applyjob-bot

# 2. Crear entorno virtual e instalar dependencias
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux / Mac
source .venv/bin/activate

pip install -r requirements.txt

# 3. Instalar Chromium
playwright install chromium
```

---

## Configuración

### 1. Tu perfil de usuario

Edita `bot/config.py` → sección `USER_PROFILE`:

```python
USER_PROFILE = {
    "full_name":  "Tu Nombre Completo",
    "email":      "tuemail@gmail.com",
    "phone":      "+54 9 11 1234 5678",
    "city":       "Buenos Aires, Argentina",
    "linkedin":   "https://linkedin.com/in/tu-perfil",
    "cv_path":    "C:/Users/TuUsuario/Documents/CV.pdf",
    # ... ver config.py para la lista completa
}
```

### 2. Variables de entorno (opcional)

Copia `.env.example` a `.env` y completa:

```env
# Notificaciones por email (Fase 5)
NOTIFY_EMAIL=tuemail@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tuemail@gmail.com
SMTP_PASS=tu-app-password

# Webhook Discord/Slack/ntfy (Fase 5)
NOTIFY_WEBHOOK=https://ntfy.sh/tu-canal
```

---

## Uso

```bash
# Ver portales disponibles
python main.py --list-portals

# Dry-run (navega sin postular — para verificar selectores)
python main.py --portal computrabajo --dry-run

# Postular en LinkedIn (máx 15 Easy Apply)
python main.py --portal linkedin

# Postular en Indeed con límite custom
python main.py --portal indeed --max 20

# Sin ventana de browser
python main.py --portal computrabajo --headless

# Ver estadísticas de postulaciones
python main.py --stats
```

---

## LinkedIn: inicio de sesión

LinkedIn requiere sesión activa. El primer run debe hacerse **sin `--headless`**:

```bash
python main.py --portal linkedin --dry-run
```

El browser se abre. Iniciás sesión manualmente una vez. La sesión queda guardada en `sessions/linkedin/` y se reutiliza en todos los runs futuros.

---

## Agregar un portal nuevo

1. Abrí DevTools en el portal (F12 → Inspector)
2. Identifica los selectores CSS de cada elemento
3. Agregá una entrada en `bot/config.py`:

```python
SITE_CONFIG = {
    # ... portales existentes ...

    "mi_portal": {
        "url_busqueda":           "https://miportal.com/jobs?q=python",
        "selector_oferta":        "div.job-card",           # contenedor de cada oferta
        "selector_boton_aplicar": "button.apply-btn",       # botón de postulación
        "selector_siguiente_pagina": "a.next-page",         # paginación (None si no hay)
        "selector_titulo_oferta": "h1.job-title",           # para los logs
        "tipo_postulacion":       "directa",                # directa | modal | externa
        "max_offers_per_run":     15,
        "requires_login":         False,
    },
}
```

| `tipo_postulacion` | Descripción |
|---|---|
| `directa` | Click al botón → llenar form → submit en la misma página |
| `modal` | Click abre un overlay/modal → llenar → submit |
| `externa` | Click redirige a otro sitio → se loguea como `external` y se salta |

---

## Estructura del proyecto

```
applyjob-bot/
├── bot/
│   ├── config.py          # USER_PROFILE + SITE_CONFIG
│   ├── engine.py          # Motor principal run_bot()
│   ├── state.py           # Deduplicación SQLite
│   ├── form_filler.py     # Autocompletado de formularios
│   ├── stealth_utils.py   # Evasión anti-bot y delays
│   └── portals/
│       ├── base.py        # Clase abstracta BasePortal
│       └── linkedin.py    # LinkedIn Easy Apply flow completo
├── sessions/              # Sesiones de browser por portal (cookies)
├── errors/                # Screenshots de errores
├── logs/                  # CSVs diarios de postulaciones
├── data/                  # applyjob.db (SQLite)
├── main.py                # CLI
├── requirements.txt
└── .env.example
```

---

## Logs y estadísticas

### Ver estadísticas

```bash
python main.py --stats
```

```
==================================================
  ApplyJob Stats  —  Total: 47
==================================================

  linkedin
    applied              23
    skipped_no_easy_apply 8
    skipped_complex_7_steps 2

  computrabajo
    applied              14
```

### CSV diario

Los logs se guardan en `logs/applied_YYYY-MM-DD.csv`:

```
timestamp,portal,title,url,status,detail
2024-05-15T09:23:11,linkedin,Senior Python Developer,https://...,applied,
2024-05-15T09:25:44,linkedin,Backend Engineer,https://...,skipped_complex_7_steps,
```

---

## Manejo de errores

Cuando un selector no se encuentra o una acción falla:
1. Se toma un screenshot en `errors/portal_timestamp.png`
2. Se registra el error en el CSV y SQLite
3. El bot salta a la siguiente oferta **sin detenerse**

---

## Consideraciones importantes

- **No uses `--headless` en el primer run** de portales que requieren login
- **Respetar los límites** (`max_offers_per_run`) — postular >20/hora en LinkedIn aumenta el riesgo de detección
- **LinkedIn Easy Apply** salta automáticamente ofertas con >6 pasos (demasiado complejas)
- **El archivo `sessions/`** contiene tus cookies — no lo subas a git (está en `.gitignore`)
- **El archivo `data/`** contiene tu historial — tampoco lo subas

---

## Roadmap

Ver [ROADMAP.md](ROADMAP.md) para el plan completo de implementación, tareas completadas y pendientes.

---

## Licencia

MIT
