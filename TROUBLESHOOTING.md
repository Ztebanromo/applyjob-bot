# Troubleshooting — ApplyJob Bot

Guía de diagnóstico y resolución de problemas comunes.

---

## Índice

1. [El bot no encuentra ofertas](#1-el-bot-no-encuentra-ofertas)
2. [LinkedIn: "Sin Easy Apply"](#2-linkedin-sin-easy-apply)
3. [LinkedIn: el modal no abre](#3-linkedin-el-modal-no-abre)
4. [Formulario no se llena](#4-formulario-no-se-llena)
5. [CAPTCHA detectado](#5-captcha-detectado)
6. [Error de sesión / requiere login](#6-error-de-sesión--requiere-login)
7. [Rate limit — el bot se pausa](#7-rate-limit--el-bot-se-pausa)
8. [Errores de red / timeout](#8-errores-de-red--timeout)
9. [Screenshot de error pero no sé qué pasó](#9-screenshot-de-error-pero-no-sé-qué-pasó)
10. [La DB tiene registros duplicados o incorrectos](#10-la-db-tiene-registros-duplicados-o-incorrectos)
11. [El bot aplica a trabajos que ya postulé](#11-el-bot-aplica-a-trabajos-que-ya-postulé)
12. [Diagnóstico general](#12-diagnóstico-general)

---

## 1. El bot no encuentra ofertas

**Síntoma:** log dice `Sin ofertas detectadas con selector: ...` y hay un screenshot en `errors/`.

**Causas y soluciones:**

### a) El selector CSS cambió
Los portales actualizan su HTML frecuentemente. El selector que funcionaba ayer puede no funcionar hoy.

**Cómo verificar:**
1. Abre la URL de búsqueda en tu browser
2. Abre DevTools → Inspector (F12)
3. Haz click derecho sobre una card de oferta → "Inspect"
4. Anota el selector real del contenedor
5. Actualiza `bot/config.py` → `selector_oferta`

```python
# Antes (selector viejo)
"selector_oferta": "li.jobs-search-results__list-item",

# Después (con nuevo selector)
"selector_oferta": "li.job-card-container",
```

### b) La página requiere scroll para cargar ofertas (lazy loading)
El bot hace scroll antes de buscar, pero algunos sitios necesitan más tiempo.

**Solución:** aumenta `human_scroll(steps=5)` en engine.py o agrega un `human_delay(3, 5)` extra.

### c) La sesión no está iniciada y el portal redirige a login
Verifica que el portal tenga sesión activa: corre sin `--headless` y comprueba si la URL resultante es la de búsqueda o la de login.

---

## 2. LinkedIn: "Sin Easy Apply"

**Síntoma:** status `skipped_no_easy_apply` en casi todas las ofertas.

**Causas:**

### a) La URL de búsqueda no tiene el filtro de Easy Apply
Asegúrate de que `url_busqueda` incluya `f_AL=true`:
```
https://www.linkedin.com/jobs/search/?keywords=Python&f_AL=true
```
El parámetro `f_AL=true` filtra exclusivamente empleos con Easy Apply.

### b) LinkedIn cambió el selector del botón Easy Apply
Verifica en DevTools el aria-label actual del botón. LinkedIn suele cambiarlo entre "Easy Apply" e idiomas locales.

Actualiza en `bot/portals/linkedin.py` → diccionario `SEL`:
```python
"easy_apply_btn": "button[aria-label*='Easy Apply'], button[aria-label*='Aplicación sencilla']",
```

---

## 3. LinkedIn: el modal no abre

**Síntoma:** status `error: modal_timeout` + screenshot en `errors/`.

**Causas y soluciones:**

### a) El selector del modal cambió
```python
# En linkedin.py → SEL, verificar y actualizar:
"modal": "div.jobs-easy-apply-modal",
# Alternativas conocidas:
# "div[data-test-modal-id='easy-apply-modal']"
# "div.artdeco-modal"
```

### b) LinkedIn detectó comportamiento automatizado
Si el modal no abre pero el botón existe, LinkedIn puede estar bloqueando silenciosamente.
- Espera 24 horas antes del próximo run
- Considera aumentar los delays en `stealth_utils.py`
- Verifica que playwright-stealth esté instalado: `.venv/Scripts/pip list | grep stealth`

---

## 4. Formulario no se llena

**Síntoma:** el bot hace click en Apply pero los campos quedan vacíos.

### a) Verificar que USER_PROFILE esté completo
```bash
python main.py --validate --portal linkedin
```
Si muestra warnings de campos vacíos, complétalos en `bot/config.py`.

### b) El campo usa un atributo no reconocido
`form_filler.py` detecta campos por `name`, `id`, `placeholder`, `aria-label`.
Si un campo usa `data-field` o similar, no se detecta.

**Cómo diagnosticar:**
1. Corre con `--dry-run` para abrir el formulario sin postular
2. En DevTools, inspecciona el input problemático
3. Agrega el atributo a `FIELD_PATTERNS` en `form_filler.py`:

```python
FIELD_PATTERNS = {
    "phone": ["phone", "telefono", "tel", "mobile", "mi_nuevo_atributo"],
    ...
}
```

### c) El campo ya tiene contenido pre-llenado
Por diseño, el bot no sobreescribe campos que ya tienen contenido.
Si el pre-llenado es incorrecto, despeja el campo manualmente o edita la lógica en `fill_text_fields`.

---

## 5. CAPTCHA detectado

**Síntoma:** status `skipped_captcha` en muchas ofertas.

**Qué hacer:**
1. **Espera 24-48 horas** antes del próximo run — es la señal más efectiva
2. Reduce `max_offers_per_run` a 5-8 en `config.py`
3. Verifica que playwright-stealth esté activo:
   ```bash
   .venv/Scripts/python -c "import playwright_stealth; print('OK')"
   ```
4. Si persiste, considerá usar un proxy residencial (F8, no implementado aún)

**Nota:** el bot detecta el CAPTCHA y cierra el modal sin enviar — nunca postula con datos incompletos.

---

## 6. Error de sesión / requiere login

**Síntoma:** el bot navega pero llega a la página de login en vez de búsqueda.

**Solución:**
```bash
# Correr sin headless para poder iniciar sesión manualmente
python main.py --portal linkedin --dry-run
```
Una vez logueado, la sesión queda guardada en `sessions/linkedin/`.
Los próximos runs (incluyendo `--headless`) usarán la sesión guardada.

**Dónde se guardan las sesiones:**
```
sessions/
├── linkedin/       ← cookies de LinkedIn
├── indeed/         ← cookies de Indeed
└── computrabajo/   ← cookies de Computrabajo
```

> ⚠ No borres `sessions/` o tendrás que volver a iniciar sesión.

---

## 7. Rate limit — el bot se pausa

**Síntoma:** log dice `Rate limit: X/Y acciones en la última hora. Esperando Ns`.

**Esto es normal y esperado.** El rate limiter protege tu cuenta de ser detectada como bot.

Límites configurados por portal:
| Portal | Límite |
|---|---|
| LinkedIn | 10/hora |
| Indeed | 15/hora |
| Computrabajo | 25/hora |
| GetOnBrd | 20/hora |

**Si querés ajustar el límite** (⚠ aumentarlo incrementa riesgo de ban):
```python
# En bot/retry.py → RATE_LIMITS:
RATE_LIMITS = {
    "linkedin": RateLimiter(max_actions=12, window_minutes=60),  # subido de 10
    ...
}
```

---

## 8. Errores de red / timeout

**Síntoma:** status `error: Timeout 30000ms exceeded` o similar.

El bot reintenta automáticamente 1 vez ante errores de red. Si el reintento también falla:

- Verifica tu conexión a internet
- El portal puede estar caído temporalmente
- Aumenta el timeout en `engine.py`:
  ```python
  page.goto(url, wait_until="domcontentloaded", timeout=60_000)  # 60s en vez de 30s
  ```

---

## 9. Screenshot de error pero no sé qué pasó

Los screenshots se guardan en `errors/` con formato `portal_contexto_timestamp.png`.

**Para ver qué pasó:**
1. Abre el screenshot correspondiente
2. Busca el timestamp en el log del día:
   ```
   logs/applyjob.log
   ```
3. Busca el log en `logs/applied_YYYY-MM-DD.csv`

**Para más detalle en los logs**, corre con DEBUG temporalmente:
```python
# En main.py, cambia:
configure_logging(level=logging.DEBUG)
```

---

## 10. La DB tiene registros duplicados o incorrectos

**Ver todos los registros:**
```bash
python main.py --stats
```

**Borrar un registro específico para re-intentar:**
```python
# Abrir Python REPL:
from bot.state import _conn
with _conn() as con:
    con.execute("DELETE FROM applications WHERE url = 'https://...'")
    con.commit()
```

**Purgar registros viejos de skipped/dry_run:**
```bash
python main.py --purge --days 30
```

---

## 11. El bot aplica a trabajos que ya postulé

**Causa:** la URL en la DB es diferente a la URL actual (ej. LinkedIn agrega parámetros de tracking).

**Verificar:**
```python
from bot.state import get_recent
for r in get_recent(20):
    print(r['url'])
```

Si las URLs tienen parámetros variables (`?trackingId=...`), hay que normalizar antes de guardar. Por ahora, limpiar y re-correr con `--dry-run` para repoblar con URLs normalizadas.

---

## 12. Diagnóstico general

### Secuencia de diagnóstico recomendada

```bash
# 1. Verificar configuración
python main.py --validate --portal <portal>

# 2. Dry-run para ver si detecta ofertas sin postular
python main.py --portal <portal> --dry-run

# 3. Ver qué quedó en los logs
python main.py --stats

# 4. Si hay errores, revisar screenshots
ls errors/

# 5. Revisar log completo del día
type logs\applyjob.log   # Windows
cat logs/applyjob.log    # Linux/Mac
```

### Verificar dependencias

```bash
.venv/Scripts/python -c "
import playwright, playwright_stealth, dotenv
print('playwright:', playwright.__version__)
print('playwright-stealth: OK')
print('python-dotenv: OK')
"
```

### Reinstalar Chromium si hay crash del browser

```bash
.venv/Scripts/playwright install chromium
```
