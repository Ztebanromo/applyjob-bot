# Arquitectura del sistema

Descripción técnica de cómo está organizado el código y por qué.

---

## Estructura de carpetas

```
applyjob-bot/
│
├── bot/                        # Paquete principal
│   ├── config.py               # USER_PROFILE + SITE_CONFIG
│   ├── engine.py               # Motor principal: run_bot()
│   ├── state.py                # Persistencia SQLite
│   ├── form_filler.py          # Autocompletado de formularios
│   ├── stealth_utils.py        # Anti-detección
│   ├── retry.py                # Retry logic + Rate limiting
│   ├── validator.py            # Validación de configuración
│   ├── logger.py               # Logging centralizado
│   └── portals/
│       ├── base.py             # Clase abstracta BasePortal
│       ├── linkedin.py         # LinkedIn Easy Apply
│       └── __init__.py         # Registry de portales
│
├── sessions/                   # Sesiones de browser (gitignored)
├── errors/                     # Screenshots de errores (gitignored)
├── logs/                       # Logs y CSVs (gitignored)
├── data/                       # SQLite DB (gitignored)
│
├── main.py                     # Entry point CLI
├── requirements.txt
├── .env.example
│
└── docs/                       # Documentación
    ├── 01_getting_started.md
    ├── 02_configuration.md
    ├── 03_linkedin.md
    ├── 04_adding_portals.md
    ├── 05_architecture.md      ← este archivo
    └── 06_cli_reference.md
```

---

## Flujo de datos

```
main.py
  │
  ├── validate_config()         ← validator.py
  │
  └── run_bot(portal_name)      ← engine.py
        │
        ├── get_rate_limiter()  ← retry.py
        ├── launch_browser()    ← stealth_utils.py
        │
        └── por cada oferta:
              │
              ├── already_applied()   ← state.py (SQLite)
              ├── rate_limiter.acquire()  ← retry.py
              ├── with_retry(apply)   ← retry.py
              │     └── portal.apply_to_offer()  ← portals/linkedin.py
              │           └── fill_form()  ← form_filler.py
              │
              └── save_application()  ← state.py
                  _csv_log()          ← engine.py
```

---

## Módulos y responsabilidades

### `bot/config.py`
**Única fuente de verdad** para la configuración. Contiene `USER_PROFILE` y `SITE_CONFIG`. No tiene lógica — solo datos.

### `bot/engine.py`
El **orquestador**. Maneja el ciclo de vida completo:
- Validación → browser → navegación → loop de ofertas → paginación → logging
- No implementa lógica de postulación directamente: delega a los portales

### `bot/state.py`
**Capa de datos**. Todo lo que se guarda en SQLite pasa por aquí.
- Diseñado para ser importable desde cualquier módulo sin efectos secundarios
- La DB se crea automáticamente si no existe

### `bot/form_filler.py`
**Autocompletado genérico**. Funciona en cualquier formulario HTML sin saber nada del portal específico.
- Detecta campos por atributos, no por selectores hardcodeados
- No falla si no reconoce un campo: simplemente lo ignora

### `bot/stealth_utils.py`
**Anti-detección**. Centraliza todo lo relacionado con parecer humano:
- Delays con distribución aleatoria
- Movimiento de mouse con offset
- Scripts de evasión de fingerprinting

### `bot/retry.py`
**Resiliencia y control de velocidad**:
- `with_retry()`: reintenta solo errores transitorios (red), no errores lógicos
- `RateLimiter`: ventana deslizante, bloquea cuando se supera el límite
- Separado del engine para poder testearse independientemente

### `bot/validator.py`
**Fail-fast antes de gastar tiempo**. Si la configuración está incompleta, el bot falla inmediatamente con un mensaje claro en lugar de fallar a mitad de un run con un error críptico.

### `bot/logger.py`
**Logging centralizado**. Un solo punto de configuración. Cualquier módulo hace `logging.getLogger("applyjob.mi_modulo")` y hereda la configuración automáticamente.

### `bot/portals/base.py`
**Contrato de los portales**. Define la interfaz que todo portal debe cumplir:
- `get_offer_urls(page)` → lista de IDs/URLs
- `apply_to_offer(page, offer_id)` → `(status, title)`

### `bot/portals/linkedin.py`
**Implementación específica de LinkedIn**. Hereda de `BasePortal` e implementa la lógica del modal multi-step. Está aislada del motor para facilitar actualizaciones cuando LinkedIn cambia su HTML.

---

## Decisiones de diseño

### ¿Por qué SQLite y no solo CSV?
El CSV es para lectura humana. SQLite es para consultas rápidas (`already_applied` es O(1) por índice). Ambos coexisten porque tienen propósitos distintos.

### ¿Por qué `launch_persistent_context` y no `launch`?
`launch_persistent_context` guarda las cookies entre sesiones en `user_data_dir`. Sin esto, habría que iniciar sesión en LinkedIn en cada run.

### ¿Por qué el rate limiter en `retry.py` y no en `config.py`?
Porque el rate limiter tiene estado (timestamps). `config.py` es solo datos estáticos. Mezclarlos haría imposible testearlo.

### ¿Por qué `apply_to_offer` retorna `tuple[str, str]` en vez de solo `str`?
Para que el `engine.py` pueda guardar el título del puesto en los logs. Con solo el status, el historial tendría filas sin título para portales específicos.

---

## Extensión — cómo agregar una feature nueva

### Nuevo portal
→ Ver `docs/04_adding_portals.md`

### Nueva estrategia de postulación
1. Agregar función `_apply_nueva_estrategia()` en `engine.py`
2. Agregar el tipo al `if/elif` en `_process_offer_generic()`
3. Actualizar `validator.py` → `VALID_TIPOS`

### Nuevo campo de autocompletado
1. Agregar la clave a `USER_PROFILE` en `config.py`
2. Agregar los patrones de detección a `FIELD_PATTERNS` en `form_filler.py`

### Nuevo comando CLI
1. Agregar `parser.add_argument(...)` en `main.py`
2. Agregar la lógica como función separada antes de `main()`
3. Agregar el `if args.nuevo_comando:` en el cuerpo de `main()`
