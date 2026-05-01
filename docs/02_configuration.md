# Configuración avanzada

Referencia completa de todas las opciones de configuración disponibles.

---

## USER_PROFILE — Perfil del usuario

Ubicación: `bot/config.py`

```python
USER_PROFILE = {
    # ── Obligatorios ──────────────────────────────────────────────
    "full_name":    "Juan Pérez",
    "email":        "juan@gmail.com",
    "phone":        "+54 9 11 1234 5678",

    # ── Nombre separado (para portales que piden nombre y apellido por separado)
    "first_name":   "Juan",
    "last_name":    "Pérez",

    # ── Ubicación y redes ──────────────────────────────────────────
    "city":         "Buenos Aires, Argentina",
    "linkedin":     "https://linkedin.com/in/juanperez",
    "portfolio":    "https://juanperez.dev",

    # ── CV — ruta absoluta al archivo PDF en tu computadora
    "cv_path":      "C:/Users/Juan/Documents/CV_Juan_Perez.pdf",

    # ── Datos laborales ────────────────────────────────────────────
    "salary":       "3000",       # pretensión salarial (número)
    "years_exp":    "3",          # años de experiencia
    "cover_letter": "Estoy muy interesado en esta posición y creo que...",
}
```

### Reglas de detección de campos

El autocompletador (`form_filler.py`) detecta campos por estos atributos HTML en orden:

| Atributo buscado | Ejemplo de valor reconocido |
|---|---|
| `name` | `name="phone"`, `name="telefono"` |
| `id` | `id="email-field"` |
| `placeholder` | `placeholder="Tu teléfono"` |
| `aria-label` | `aria-label="Ciudad de residencia"` |
| `data-testid` | `data-testid="city-input"` |

Para agregar patrones adicionales, editar `FIELD_PATTERNS` en `bot/form_filler.py`.

---

## SITE_CONFIG — Configuración por portal

### Campos obligatorios

| Campo | Tipo | Descripción |
|---|---|---|
| `url_busqueda` | `str` | URL de búsqueda con filtros ya aplicados |
| `selector_oferta` | `str` | Selector CSS del contenedor de cada oferta |
| `selector_boton_aplicar` | `str` | Selector del botón principal de postulación |
| `tipo_postulacion` | `str` | `"directa"` \| `"modal"` \| `"externa"` |

### Campos opcionales

| Campo | Tipo | Default | Descripción |
|---|---|---|---|
| `selector_siguiente_pagina` | `str\|None` | `None` | Selector para avanzar de página |
| `selector_titulo_oferta` | `str\|None` | `None` | Para extraer el título en los logs |
| `max_offers_per_run` | `int` | `10` | Límite de postulaciones por ejecución |
| `requires_login` | `bool` | `False` | Si `True`, requiere sesión activa antes del primer run |

### Tipos de postulación

| Tipo | Cuándo usarlo | Comportamiento |
|---|---|---|
| `directa` | El formulario está en la misma página | Click → fill → submit |
| `modal` | Click abre un overlay/popup | Click → esperar modal → fill × N → submit |
| `externa` | Click redirige a otro sitio | Click → registrar URL externa → cerrar |

---

## Variables de entorno (.env)

Copiá `.env.example` a `.env` y completá:

```bash
cp .env.example .env
```

```env
# Notificaciones (Fase 6 — pendiente)
NOTIFY_EMAIL=tuemail@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=tuemail@gmail.com
SMTP_PASS=tu-app-password

NOTIFY_WEBHOOK=https://ntfy.sh/tu-canal

# Scheduler (Fase 7 — pendiente)
SCHEDULE_PORTALS=linkedin,computrabajo
SCHEDULE_TIMES=09:00,14:00,18:00
```

El archivo `.env` se carga automáticamente al arrancar. Si no existe, se ignora sin error.

---

## Rate Limiting

Configurado en `bot/retry.py` → `RATE_LIMITS`:

```python
RATE_LIMITS = {
    "linkedin":     RateLimiter(max_actions=10, window_minutes=60),
    "indeed":       RateLimiter(max_actions=15, window_minutes=60),
    "computrabajo": RateLimiter(max_actions=25, window_minutes=60),
    "getonyboard":  RateLimiter(max_actions=20, window_minutes=60),
}
```

Para ajustar el límite de un portal:
```python
# Subir el límite de LinkedIn a 12/hora (aumenta riesgo leve)
"linkedin": RateLimiter(max_actions=12, window_minutes=60),
```

⚠ No subas LinkedIn por encima de 15/hora.

---

## Nivel de logging

Por defecto el bot loguea en nivel `INFO` en consola y `DEBUG` en archivo.

Para activar `DEBUG` en consola (útil para diagnosticar selectores):

```python
# En main.py, línea configure_logging():
configure_logging(level=logging.DEBUG)
```

---

## Retry automático

Configurado en `bot/retry.py` → `with_retry()`:

```python
# Default: 2 intentos, 5 segundos de espera entre intentos
with_retry(fn, attempts=2, delay=5.0)
```

Solo reintenta errores de red (timeout, connection reset). Errores permanentes (selector not found) no se reintentan.
