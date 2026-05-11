# Deuda Técnica y Bugs Conocidos — ApplyJob Bot

> Documento 7 de 7 | Serie de documentación técnica exhaustiva  
> Objetivo: registrar de forma sistemática los problemas conocidos, su impacto y las correcciones sugeridas.

---

## 8.1 Hallazgos por Severidad

### 🔴 Críticos — Pueden causar pérdida de datos, falsos positivos en postulaciones, o crashes del sistema

---

**ID: BUG-001**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `bot/engine.py:163` + `bot/retry.py:163` |
| Problema | El browser puede cerrarse durante el sleep del RateLimiter |
| Evidencia (fragmento) | `time.sleep(wait_secs)` en `retry.py:163` — sleep bloqueante de hasta 3600s. Si el proceso de Chrome muere durante este sleep (timeout del sistema, usuario cierra la ventana), la siguiente interacción con `page` lanza `playwright._impl._api_types.Error: Target page, context or browser has been closed` |
| Impacto | El bot termina abruptamente con stack trace. El browser no se cierra limpiamente. La sesión puede quedar corrupta en `sessions/`. |
| Fix sugerido | Verificar `browser.is_connected()` o envolver el sleep en chunks de 30s con verificación del browser entre cada chunk. Agregar `browser.close()` en el manejador de excepciones general. |

```python
# Código actual (retry.py:163):
time.sleep(wait_secs)

# Fix sugerido:
chunk = 30.0
elapsed = 0.0
while elapsed < wait_secs:
    sleep_now = min(chunk, wait_secs - elapsed)
    time.sleep(sleep_now)
    elapsed += sleep_now
    # El caller debería verificar conexión del browser aquí
```

---

**ID: BUG-002**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `bot/retry.py:134-137` |
| Problema | El RateLimiter no persiste su estado entre runs |
| Evidencia (fragmento) | `self._timestamps: deque = deque()` — inicializado vacío en `__init__`. Cada instancia del bot empieza con contador en 0. |
| Impacto | Si el bot se ejecuta dos veces en la misma hora, puede hacer 25+25=50 postulaciones en LinkedIn en menos de 60 minutos. LinkedIn detecta ráfagas y puede banear la cuenta. |
| Fix sugerido | Persistir los timestamps en SQLite (tabla `rate_limiter_log`) o en un archivo JSON en `data/`, y cargarlos al inicializar `RateLimiter`. |

```python
# Estado actual:
def __init__(self, max_actions: int = 10, window_minutes: int = 60):
    self.max_actions = max_actions
    self.window = timedelta(minutes=window_minutes)
    self._timestamps: deque = deque()   # SIEMPRE vacío al iniciar

# Fix sugerido (esquema):
def __init__(self, ...):
    ...
    self._timestamps = self._load_timestamps()  # carga desde DB/JSON
```

---

**ID: BUG-003**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `bot/portals/laborum.py:507-514` |
| Problema | `form_submitted = True` se establece sin confirmación visual de éxito |
| Evidencia (fragmento) | |

```python
# laborum.py:507-514
if form_submitted:
    log.info("Formulario enviado — sin confirmación visual, marcando como applied")
    return "applied", title
```

| Impacto | Si el formulario tiene un error de validación (ej: campo obligatorio no rellenado), Laborum puede rechazarlo y mostrar un mensaje de error, pero el bot ya hizo click en submit y marcará la oferta como `applied` cuando en realidad no se postulió. Falso positivo que impide re-intentar. |
| Fix sugerido | Verificar `_check_success()` ANTES de asumir éxito. Si no hay confirmación visual, retornar `"filled_no_submit"` en lugar de `"applied"`. |

```python
# Fix sugerido:
if form_submitted:
    human_delay(2.0, 4.0)
    if self._check_success(page):
        return "applied", title
    else:
        log.warning("  Formulario enviado pero sin confirmación visual")
        return "filled_no_submit", title
```

---

### 🟡 Medios — Degradan la confiabilidad o la seguridad del sistema, pero no causan fallos inmediatos

---

**ID: ISSUE-004**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `requirements.txt:1` |
| Problema | Versión de Playwright en `requirements.txt` no coincide con la instalada |
| Evidencia | `requirements.txt` declara `playwright==1.44.0` pero el sistema tiene `1.59.0` instalado. La API de `Stealth().apply_stealth_sync()` puede no existir en 1.44.0. |
| Impacto | Si alguien clona el proyecto e instala `pip install -r requirements.txt`, obtendrá 1.44.0, que puede ser incompatible con partes del código. El entorno de desarrollo y el de instalación limpia difieren. |
| Fix sugerido | Actualizar `requirements.txt` para que refleje la versión real instalada. Correr `pip freeze > requirements.txt` en el entorno actual. |

---

**ID: ISSUE-005**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `bot/config.py:11-31` |
| Problema | Datos personales reales hardcodeados como defaults en el código |
| Evidencia | |

```python
# config.py:11-14
"full_name":  os.getenv("USER_FULL_NAME", "Ignacio Romo"),
"email":      os.getenv("USER_EMAIL", "ygnacio1698@gmail.com"),
"phone":      os.getenv("USER_PHONE", "+56 9 3420 0859"),
"cv_path":    os.getenv("USER_CV_PATH", "C:/Users/ygnac/Downloads/files/cv-ignacio-romo.pdf"),
```

| Impacto | Si el repositorio fuera público (GitHub), el email, teléfono, nombre y ruta del CV quedarían expuestos en el historial de git. También crea riesgo de que alguien use accidentalmente los datos de otra persona. |
| Fix sugerido | Cambiar todos los defaults a strings vacíos o placeholders descriptivos. Usar `.env.example` con valores ficticios. Agregar `.env.example` al repositorio y `.env` a `.gitignore`. |

```python
# Fix sugerido:
"full_name":  os.getenv("USER_FULL_NAME", ""),    # sin default
"email":      os.getenv("USER_EMAIL", ""),
```

---

**ID: ISSUE-006**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `bot/portals/laborum.py:192-210` |
| Problema | Q&A cache sin mecanismo de corrección o invalidación |
| Evidencia | |

```python
# laborum.py:215-217
def _cache_answer(self, question_text: str, answer: str) -> None:
    if key not in self._qa_cache:   # NUNCA sobreescribe
        self._qa_cache[key] = answer
```

| Impacto | Una respuesta incorrecta (ej: salario mal formateado que causa error en el formulario de Laborum) queda grabada permanentemente. El bot la seguirá usando en todos los runs futuros. La única corrección posible es editar manualmente `data/qa_cache.json`. |
| Fix sugerido | Agregar un TTL (Time To Live) a cada respuesta. O al menos marcar respuestas que resultaron en error para excluirlas en runs futuros. |

---

**ID: ISSUE-007**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `bot/portals/linkedin.py:710-711` |
| Problema | CAPTCHA de LinkedIn cierra el modal pero continúa el run |
| Evidencia | |

```python
# linkedin.py:710-711
if page.query_selector(SEL["captcha_check"]):
    self._close_modal_safely(page)
    return "skipped_captcha", title
```

| Impacto | El bot detecta el CAPTCHA, omite la oferta, pero continúa con la siguiente. Si el CAPTCHA es para el dominio completo (no solo esa oferta), el bot seguirá intentando postular y todas las siguientes ofertas también fallarán con errores de CAPTCHA. |
| Fix sugerido | Al detectar CAPTCHA, pausar el run y notificar al usuario (similar a cómo `IndeedPortal._wait_cloudflare()` espera resolución manual). |

---

**ID: ISSUE-008**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `bot/portals/indeed.py:92-136` |
| Problema | `_wait_cloudflare()` solo se llama en `get_offer_urls()`, no en `apply_to_offer()` |
| Evidencia | La llamada a `_wait_cloudflare()` está solo en `get_offer_urls` (`indeed.py:144`). Si Cloudflare se activa durante la fase de aplicación, no se detecta. |
| Impacto | El bot puede fallar silenciosamente en todas las postulaciones de Indeed sin que quede claro que es un bloqueo de Cloudflare. |
| Fix sugerido | Agregar verificación de Cloudflare en `apply_to_offer()` antes de hacer click en el botón de aplicar. |

---

**ID: ISSUE-009**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `bot/stealth_utils.py:10-19` |
| Problema | User Agents hardcodeados con versiones de Chrome desactualizadas |
| Evidencia | |

```python
# stealth_utils.py:10-14
"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
"(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
```

| Impacto | Chrome 124 fue lanzado en abril 2024. Chrome actual es 124+ (2025: ~130+). Los portales pueden identificar User Agents de versiones muy antiguas como sospechosos. |
| Fix sugerido | Actualizar los User Agents a versiones más recientes. O mejor: leer el User Agent real del Chrome instalado en el sistema. |

---

### 🟢 Menores — Baja urgencia, no afectan funcionalidad principal

---

**ID: IMPROVEMENT-010**

| Campo | Detalle |
|---|---|
| Archivo:Línea | Todo el proyecto |
| Problema | Sin tests de ningún tipo |
| Evidencia | No existe directorio `tests/`, ningún archivo con `test_*.py` o `*_test.py`, ni `pytest`, `unittest`, ni `mock` en `requirements.txt`. |
| Impacto | Imposible detectar regresiones automáticamente cuando se actualizan selectores o se refactoriza código. Un cambio en `form_filler.py` puede romper silenciosamente Laborum. |
| Fix sugerido | Mínimo: tests unitarios para `RateLimiter`, `is_transient_error`, `validate_profile`, `_title_is_tech`, `_normalize_question`, `already_applied`. Usar `pytest` con mocks de Playwright. |

---

**ID: IMPROVEMENT-011**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `engine.py:503-509` |
| Problema | Sin Dockerfile ni instrucciones de deployment en servidor |
| Evidencia | No existe `Dockerfile`, `docker-compose.yml`, ni scripts de systemd/cron. |
| Impacto | El bot solo puede ejecutarse en el equipo local del usuario. No puede correr automáticamente a un horario en un servidor. |
| Fix sugerido | Agregar `Dockerfile` con Python 3.12 + dependencias. Agregar instrucción de cómo correr con `cron` o `Task Scheduler` de Windows. |

---

**ID: IMPROVEMENT-012**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `engine.py:88-98`, `logger.py:50-61` |
| Problema | CSV de postulaciones y .log en el mismo directorio `logs/` |
| Evidencia | `LOGS_DIR / f"applied_{today}.csv"` y `LOGS_DIR / "applyjob.log"` en el mismo directorio. |
| Impacto | Bajo. Los archivos CSV son legibles por humanos, los .log son más técnicos. Tenerlos juntos dificulta gestionar permisos separados o rotar solo uno. |
| Fix sugerido | Separar en `logs/app/` para los .log y `logs/reports/` para los CSV. |

---

**ID: IMPROVEMENT-013**

| Campo | Detalle |
|---|---|
| Archivo:Línea | `engine.py:299-329` |
| Problema | Lógica de login signals de portal en el engine en lugar de en cada portal |
| Evidencia | |

```python
# engine.py:299-329
_LOGIN_SIGNALS = {
    "linkedin": ["div.nav__button-secondary", ...],
    "laborum":  ["#ingresarNavBar", ...],
}
```

| Impacto | Si se agrega un portal nuevo, el desarrollador debe actualizar `engine.py` además de crear la clase del portal. Viola el principio de open/closed. |
| Fix sugerido | Mover `LOGIN_SIGNALS` y `LOGGED_IN_SIGNALS` como atributos de clase en cada portal. `LinkedInPortal.LOGIN_SIGNALS = [...]`. El engine los lee con `getattr(portal_handler, 'LOGIN_SIGNALS', [])`. |

---

**ID: IMPROVEMENT-014**

| Campo | Detalle |
|---|---|
| Archivo:Línea | Todo el proyecto |
| Problema | Sin CI/CD configurado |
| Evidencia | No existe `.github/workflows/`, `.gitlab-ci.yml`, ni ningún otro archivo de pipeline. |
| Impacto | Los cambios no pasan por ninguna validación automática antes de ser mergeados. |
| Fix sugerido | Mínimo: GitHub Actions que corra `python -m py_compile bot/**/*.py` (verifica sintaxis) y `python main.py --validate --portal linkedin` (valida configuración). |

---

## 8.2 Cobertura de Tests

**Estado real:** 0 tests. No existe ningún archivo de prueba en el proyecto.

**Flujos críticos sin cobertura (por orden de prioridad):**

| # | Flujo | Módulo | Por qué es crítico |
|---|---|---|---|
| 1 | `already_applied()` retorna True/False correctamente | `state.py` | Si falla, el bot puede postular múltiples veces a la misma oferta |
| 2 | `validate_profile()` rechaza valores placeholder | `validator.py` | Si falla, el bot postula con datos incorrectos |
| 3 | `RateLimiter.acquire()` espera correctamente | `retry.py` | Si falla, el bot puede violar límites y ser baneado |
| 4 | `is_transient_error()` clasifica correctamente | `retry.py` | Si falla, errores permanentes se reintentan (loops infinitos) |
| 5 | `_title_is_tech()` filtra correctamente | `laborum.py` | Si falla, el bot postula a ofertas no-TI |
| 6 | `_normalize_question()` es idempotente | `laborum.py` | Si falla, el Q&A cache no funciona |
| 7 | `fill_text_fields()` detecta y rellena campos | `form_filler.py` | Flujo principal del bot |
| 8 | `purge_old()` elimina solo los status correctos | `state.py` | Si falla, puede eliminar registros `applied` |
| 9 | `_match_field()` con FIELD_PATTERNS | `form_filler.py` | Si falla, formularios quedan vacíos |
| 10 | `_apply_modal()` avanza pasos correctamente | `engine.py` | Flujo principal de LinkedIn |

**Herramientas recomendadas:**
- `pytest` para tests unitarios
- `unittest.mock` / `pytest-mock` para mockear Playwright `Page`
- `pytest-playwright` para tests de integración con browser real (más lentos, pero más fiables para detectar cambios en selectores)

---

## 8.3 Health Score

| Dimensión | Puntos /máx | Justificación |
|---|---|---|
| **Arquitectura** | 14/20 | Capas bien definidas, Strategy/Plugin pattern correcto. Penalización: lógica de portal en engine.py (LOGIN_SIGNALS), rate limiter sin persistencia, acoplamiento implícito USER_PROFILE en engine. |
| **Seguridad** | 8/20 | Datos personales hardcodeados en config.py (email, teléfono, ruta de CV). Sin .env.example en el repo. Credenciales de Laborum en texto plano si se usan. Sin validación de entrada de URLs externas. |
| **Calidad de código** | 14/20 | Docstrings exhaustivos, type hints consistentes, nombres descriptivos, manejo de errores por oferta individual. Penalización: funciones largas (apply_to_offer de LinkedIn: 185 líneas), duplicación de lógica de selector entre portales. |
| **Testing** | 0/20 | 0 tests de ningún tipo. No existe ni pytest en requirements.txt. |
| **Documentación** | 7/10 | Docstrings ricos en cada módulo/clase/función. Comentarios inline explicativos. Penalización: sin CHANGELOG, sin .env.example documentado, sin guía de contribución. |
| **Deploy/Infra** | 3/10 | Funciona en local con instrucciones en main.py. Sin Dockerfile, sin CI/CD, sin scripts de cron/systemd, requirements.txt desactualizado. |
| **TOTAL** | **46/100** | Bot funcional para uso personal local. Requiere hardening significativo para uso en producción o equipo. |

---

### Resumen de Acciones Priorizadas

| Prioridad | ID | Acción | Esfuerzo estimado |
|---|---|---|---|
| 🔴 P1 | BUG-003 | Fix falso positivo `form_submitted` en Laborum | 30 min |
| 🔴 P1 | BUG-002 | Persistir timestamps del RateLimiter en SQLite | 2 h |
| 🟡 P2 | ISSUE-005 | Eliminar datos personales hardcodeados de config.py | 1 h |
| 🟡 P2 | ISSUE-004 | Sincronizar requirements.txt con entorno real | 15 min |
| 🟡 P2 | ISSUE-006 | Agregar invalidación al Q&A cache | 1 h |
| 🟡 P2 | ISSUE-007 | Pausar run al detectar CAPTCHA en LinkedIn | 2 h |
| 🟢 P3 | IMPROVEMENT-010 | Agregar tests unitarios mínimos (10 casos) | 4 h |
| 🟢 P3 | IMPROVEMENT-011 | Agregar Dockerfile y cron/Task Scheduler | 2 h |
| 🟢 P3 | IMPROVEMENT-013 | Mover LOGIN_SIGNALS a cada clase de portal | 1 h |
| 🟢 P3 | BUG-001 | Sleep chunkeado con verificación del browser | 1 h |

---
