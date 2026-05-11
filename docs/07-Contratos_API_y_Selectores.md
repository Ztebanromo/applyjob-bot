# 07 — Contratos de API y Selectores de Portal

> Documento 7 de 8 | Serie de documentación técnica exhaustiva  
> Audiencia: dev que necesita mantener o extender un portal existente.

> **Nota de alcance:** `applyjob-bot` no expone ninguna API HTTP propia — es un script CLI.  
> Este documento documenta los **contratos externos que el bot consume**: la API interna de Laborum,  
> los selectores CSS críticos de cada portal, y las estructuras de datos intercambiadas.

---

## 7.1 API Interna de Laborum

### Contexto

Laborum.cl es una SPA React que obtiene sus ofertas mediante una API REST interna.  
El bot la llama directamente usando `page.evaluate()` — ejecutando un `fetch()` dentro del contexto  
del navegador para que las cookies de sesión se incluyan automáticamente.

> **¿Por qué dentro del browser?**  
> La API está bajo el mismo dominio (`laborum.cl`). Llamarla desde Python directamente requeriría  
> replicar manualmente todas las cookies de sesión. Ejecutando el `fetch()` desde el browser,  
> el sistema de cookies del navegador lo hace automático. — `laborum.py:276`

---

### Endpoint de Búsqueda de Ofertas

```
POST /api/avisos/searchV2
Host: www.laborum.cl
```

**Parámetros de query string:**

| Parámetro | Tipo | Ejemplo | Descripción |
|---|---|---|---|
| `pageSize` | int | `50` | Ofertas por página. 50 es el máximo permitido. |
| `page` | int | `0` | Número de página (base 0). El bot itera de 0 a `API_MAX_PAGES-1` (15 páginas). |
| `sort` | string | `RELEVANTES` | Criterio de ordenamiento. El bot usa siempre `RELEVANTES`. |

**Headers requeridos:**

| Header | Valor | Propósito |
|---|---|---|
| `Content-Type` | `application/json` | Indica que el body es JSON |
| `Accept` | `application/json, text/plain, */*` | Acepta respuesta JSON |
| `x-site-id` | `BMCL` | Identificador del sitio Laborum Chile. Sin este header, la API devuelve error o resultados de otro país. |
| `credentials` | `include` | No es un header HTTP — es la opción `fetch` que incluye las cookies de sesión automáticamente. |

**Body (JSON):**

```json
{
  "filtros": [],
  "palabraClave": "desarrollador programador software"
}
```

| Campo | Tipo | Descripción |
|---|---|---|
| `filtros` | `array` | Lista de filtros activos (región, tipo contrato, etc.). El bot siempre envía `[]` (sin filtros). |
| `palabraClave` | `string` | Término de búsqueda. Definido en `LaborumPortal.SEARCH_KEYWORD` (`laborum.py:95`). |

**Respuesta exitosa (HTTP 200):**

```json
{
  "total": 1234,
  "content": [
    {
      "id": 1118281286,
      "titulo": "Desarrollador Python Junior",
      "empresa": "Acme Corp",
      "fechaPublicacion": "2024-05-15T10:30:00",
      "ubicacion": "Santiago"
    }
  ]
}
```

| Campo extraído | Ruta en JSON | Transformación aplicada | Dónde |
|---|---|---|---|
| `id` | `content[].id` | `String(j.id)` → se usa para construir URL | `laborum.py:301` |
| `titulo` | `content[].titulo` | Texto plano → se pasa a `_title_is_tech()` para filtro | `laborum.py:302` |

**URL canónica construida a partir del ID:**
```
https://www.laborum.cl/empleos/oferta-{id}.html
```

**Respuesta de error:**

```json
{ "error": "401", "items": [] }
```

El bot verifica `if 'error' in result` y hace `break` del loop de páginas. — `laborum.py:311`

**Condición de última página:**  
Si `len(items) < pageSize` (50), se asume que no hay más resultados y el loop termina. — `laborum.py:332`

---

### Flujo completo de llamada API

```
laborum.py:362
  └─► _fetch_job_urls_via_api(page, keyword, max_pages=15)
        │
        ├─ [page 0..14] page.evaluate(fetch('/api/avisos/searchV2?pageSize=50&page=N'))
        │       │
        │       ├─ HTTP 200 → extraer items → filtrar con _title_is_tech()
        │       ├─ HTTP !200 → log WARNING + break
        │       ├─ items vacíos → break
        │       └─ len(items) < 50 → break (última página)
        │
        └─ retorna List[str] de URLs únicas (máx 750 candidatos escaneados)
```

---

## 7.2 Contratos de Selectores por Portal

Un **selector CSS** es el "contrato" entre el bot y la interfaz del portal. Si el portal cambia su HTML,  
el selector rompe. Esta sección documenta todos los selectores críticos con su propósito y fragilidad.

> **Convención:** `🔴 Frágil` = selector basado en clase dinámica (rompe con cada deploy del portal).  
> `🟢 Estable` = selector basado en atributo semántico o texto visible.

---

### 7.2.1 Laborum (`bot/portals/laborum.py:46-81`)

| Selector Key | Selector CSS | Propósito | Estabilidad |
|---|---|---|---|
| `card` | `a[href*='/empleos/'][class*='sc-']` | Cards de oferta en el listing | 🟡 Medio — `href` estable, `class*='sc-'` frágil |
| `apply_btn` | `button:has-text('Postularme'), button:has-text('Postular'), a:has-text('Postularme'), button[class*='sc-enLHqu']` | Botón principal de postulación | 🟢 Estable (`has-text`) + 🔴 Frágil fallback clase |
| `job_title` | `h1` | Título del puesto | 🟢 Muy estable |
| `login_signal` | `input[type='email'], input[type='password'], [class*='login'], #ingresarNavBar, button:has-text('Ingresar')` | Detectar si el usuario no está logueado | 🟡 Medio |
| `form_signal` | `form, textarea, input[type='text'], .sc-fLcnxK` | Detectar formulario de screening | 🟡 Medio — `.sc-fLcnxK` frágil |
| `success_signal` | `div:has-text('postulación enviada'), div:has-text('Te postulaste'), h2:has-text('¡Ya te postulaste!')` | Confirmar postulación exitosa | 🟢 Estable (texto visible) |

**Selectores de formulario de screening** (`laborum.py:532-622`):

| Elemento | Selector | Propósito |
|---|---|---|
| Textarea de preguntas | `textarea` | Detectar y rellenar preguntas abiertas |
| Labels de preguntas | `label, p, span[class*='label'], div[class*='question']` | Extraer el texto de la pregunta |
| Botón siguiente/enviar | `button[type='submit'], button:has-text('Siguiente'), button:has-text('Continuar'), button:has-text('Enviar')` | Avanzar pasos del formulario |

---

### 7.2.2 LinkedIn (`bot/portals/linkedin.py:28-54`)

| Selector Key | Selector CSS | Propósito | Estabilidad |
|---|---|---|---|
| `job_card` | `li[data-occludable-job-id], li.scaffold-layout__list-item` | Cards de oferta en panel izquierdo | 🟢 Estable (`data-occludable-job-id` es atributo de datos) |
| `job_card_link` | `a.job-card-list__title--link, a.job-card-container__link` | Link dentro de la card | 🟡 Medio — clases semánticas pero pueden cambiar |
| `easy_apply_btn` | `button.jobs-apply-button--top-card, button[aria-label*='Easy Apply'], button[aria-label*='Solicitud sencilla']` | Botón de Easy Apply | 🟢 Estable (`aria-label` es accesibilidad) |
| `modal` | `div.jobs-easy-apply-modal, div[data-test-modal-id='easy-apply-modal'], div[role='dialog']` | Contenedor del modal multi-step | 🟢 Estable (`role='dialog'`) |
| `step_indicator` | `span.t-14.t-black--light` | "Step X of Y" para contar pasos | 🔴 Frágil — clases utilitarias de LinkedIn |
| `next_btn` | `button[aria-label='Continue to next step'], button[aria-label='Siguiente paso']` | Avanzar al siguiente paso | 🟢 Estable (`aria-label`) |
| `submit_btn` | `button[aria-label='Submit application'], button[aria-label='Enviar solicitud']` | Submit final | 🟢 Estable (`aria-label`) |
| `captcha_check` | `div.challenge-dialog, iframe[title*='security']` | Detectar CAPTCHA | 🟡 Medio |
| `already_applied` | `span.artdeco-inline-feedback__message` | Detectar "Ya postulaste" | 🟡 Medio |

**Extracción de Job ID** (`linkedin.py:129`):
```python
# El ID se extrae del atributo de la card
job_id = card.get_attribute("data-occludable-job-id")
# URL canónica:
url = f"https://www.linkedin.com/jobs/view/{job_id}/"
```

**Parseo del indicador de pasos** (`linkedin.py:271`):
```python
# Texto: "Step 2 of 4" o "Paso 2 de 4"
m = re.search(r'(\d+)\s+of\s+(\d+)|(\d+)\s+de\s+(\d+)', text)
current = int(m.group(1) or m.group(3))
total   = int(m.group(2) or m.group(4))
```

---

### 7.2.3 Indeed (`bot/portals/indeed.py:28-80`)

| Selector Key | Selector CSS | Propósito | Estabilidad |
|---|---|---|---|
| `card` | `div.job_seen_beacon` | Card de oferta | 🟡 Medio |
| `card_title_link` | `h2.jobTitle a[data-jk], a.jcs-JobTitle[data-jk]` | Link de la card con Job Key | 🟢 Estable (`data-jk` es clave semántica) |
| `easy_apply_btn` | `button#indeedApplyButton, .ia-IndeedApplyButton` | Botón Easy Apply de Indeed | 🟢 Estable (`id` es muy estable) |
| `cloudflare_challenge` | `div#challenge-running, iframe[src*='challenges.cloudflare']` | Detectar bloqueo Cloudflare | 🟡 Medio — Cloudflare puede cambiar |
| `next_page` | `a[data-testid='pagination-page-next']` | Paginación | 🟢 Estable (`data-testid`) |

**Extracción de Job Key** (`indeed.py:163`):
```python
job_key = link.get_attribute("data-jk")
url = f"https://cl.indeed.com/viewjob?jk={job_key}"
```

**Detección Cloudflare** (`indeed.py:92-136`):
```python
# Si detecta el challenge, espera hasta 120 segundos a resolución manual
page.wait_for_selector("div#challenge-running", timeout=2000)
# → log.warning("Cloudflare detectado — resuelve el captcha manualmente")
# → time.sleep(120)  # espera humana
```

---

### 7.2.4 GetOnBoard (`bot/portals/getonyboard.py`)

| Selector Key | Selector CSS | Propósito | Estabilidad |
|---|---|---|---|
| `job_card` | `a.gb-results-list__item` | Link directo a la oferta | 🟡 Medio — clase con prefijo `gb-` |
| `apply_btn` | `a#apply_bottom, a.js-go-to-apply` | Botón de postulación | 🟡 Medio |
| `job_title` | `h1.gb-landing-cover__title` | Título | 🟡 Medio |

**Tipo de postulación:** `externa` — GetOnBoard redirige a un formulario externo o al ATS del empleador. El bot registra la URL como `external_apply` sin completar el formulario. — `getonyboard.py`

---

## 7.3 Estructuras de Datos Internas

### Perfil de usuario (`bot/config.py:10-31`)

Estructura `USER_PROFILE` — se pasa como `profile: dict` a cada portal:

| Campo | Tipo | Ejemplo | Usado en |
|---|---|---|---|
| `full_name` | `str` | `"Ignacio Romo"` | `form_filler.py` — inputs de nombre |
| `first_name` | `str` | `"Ignacio"` | `form_filler.py` |
| `last_name` | `str` | `"Romo"` | `form_filler.py` |
| `email` | `str` | `"user@gmail.com"` | `form_filler.py` |
| `phone` | `str` | `"+56 9 3420 0859"` | `form_filler.py` |
| `city` | `str` | `"Región Metropolitana de Santiago, Chile"` | `form_filler.py` |
| `linkedin` | `str` | URL LinkedIn | `form_filler.py` |
| `portfolio` | `str` | URL GitHub | `form_filler.py` |
| `cv_path` | `str` | Ruta absoluta al PDF | `form_filler.py:set_input_files()` |
| `salary` | `str` | `"850.000"` | `laborum.py:_fill_laborum_screening()` |
| `years_exp` | `str` | `"0"` | `form_filler.py` |
| `cover_letter` | `str` | Texto largo | `laborum.py:_fill_laborum_screening()` fallback |
| `availability` | `str` | `"Inmediata"` | `form_filler.py` |
| `english_level` | `str` | `"Básico"` | `form_filler.py` |
| `work_mode` | `str` | `"Sí"` | `form_filler.py` |
| `laborum_email` | `str` | email | `engine.py:auto_login_laborum()` |
| `laborum_password` | `str` | contraseña | `engine.py:auto_login_laborum()` |

---

### Registro de postulación (`bot/state.py` — tabla `applications`)

Estructura guardada en SQLite y emitida en el CSV diario:

| Campo DB | Tipo SQL | Ejemplo | Descripción |
|---|---|---|---|
| `id` | `INTEGER PRIMARY KEY` | `42` | Auto-increment |
| `url` | `TEXT UNIQUE` | `https://laborum.cl/empleos/oferta-123.html` | Clave de deduplicación — `UNIQUE` garantiza un registro por oferta |
| `portal` | `TEXT` | `"laborum"` | Nombre del portal origen |
| `title` | `TEXT` | `"Desarrollador Python"` | Título de la oferta (truncado a 80 chars) |
| `status` | `TEXT` | `"applied"` | Resultado del intento (ver tabla de estados) |
| `detail` | `TEXT` | `""` | Información adicional (ej: número de pasos del modal) |
| `created_at` | `TIMESTAMP` | `2024-05-15 14:32:01` | Primera vez que se procesó |
| `updated_at` | `TIMESTAMP` | `2024-05-15 14:32:01` | Última actualización de status |

**Tabla completa de valores de `status`:**

| Status | Quién lo asigna | ¿Bloquea reintentos? | Descripción |
|---|---|---|---|
| `applied` | Portal | ✅ Sí | Postulación enviada exitosamente |
| `external_apply` | Portal | ✅ Sí | Redirige a sitio externo — no se completó en el portal |
| `dry_run` | Engine | ❌ No | Simulación — bot no envió el formulario |
| `skipped_already_applied` | State | ✅ Sí | Ya existía en DB como `applied` |
| `skipped_no_easy_apply` | LinkedIn | ✅ Sí | La oferta no tiene botón Easy Apply |
| `skipped_captcha` | LinkedIn | ✅ Sí | Detectó CAPTCHA durante el modal |
| `skipped_complex_{N}_steps` | LinkedIn | ✅ Sí | Modal con más de 6 pasos |
| `error: no_apply_button` | Portal | ❌ No | Botón de postulación no encontrado |
| `error: {ExceptionType}` | Engine | ❌ No | Excepción capturada durante el flujo |

> **Regla de deduplicación** (`state.py:71-99`):  
> Solo bloquean reintentos los statuses `"applied"` y `"skipped_already_applied"`.  
> Todos los `error:*` y `dry_run` se pueden reintentar en el siguiente run.

---

### Q&A Cache (`data/qa_cache.json`)

Formato del archivo en disco:

```json
{
  "cuánto es tu pretensión de renta": "850000",
  "cuál es tu disponibilidad para incorporarte": "Inmediata",
  "nivel de inglés": "Básico. Puedo leer documentación técnica en inglés.",
  "por qué te interesa este cargo": "Soy Analista Programador recién egresado..."
}
```

**Algoritmo de normalización de clave** (`laborum.py:153-161`):
```python
def _normalize_question(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text.strip().lower())  # colapsar espacios
    normalized = normalized.strip("?¿.,:;!")                 # quitar puntuación extrema
    return normalized[:200]                                   # truncar a 200 chars
```

**Algoritmo de búsqueda** (`laborum.py:185-200`):
1. Busca clave exacta en el dict.
2. Si no hay exacta → busca si la clave normalizada es **substring** de alguna clave guardada, o viceversa.
3. Si no hay match → retorna `None` → el bot usa el fallback (salary o cover_letter).

---

## 7.4 Flujo de Autenticación por Portal

### Laborum — Auto-login (`engine.py:403-416`)

```
1. Navegar a https://www.laborum.cl/empleos.html
2. Verificar _check_is_login_page()
   ├─ Si NO hay login → continuar normalmente
   └─ Si HAY login:
       ├─ Si LABORUM_EMAIL y LABORUM_PASSWORD están configurados:
       │   └─ Rellenar form + submit → esperar 5s → re-verificar
       └─ Si NO están configurados (o auto-login falla):
           └─ Esperar hasta 300s (5 min) a login manual
               └─ Si timeout → lanzar TimeoutError → bot termina
```

### LinkedIn — Login manual requerido (`engine.py`)

LinkedIn no tiene auto-login implementado. El flujo es:
1. Primera ejecución: abrir `sessions/linkedin/` vacío → LinkedIn pide login.
2. El usuario inicia sesión manualmente en la ventana de Chrome que abre el bot.
3. Las cookies se guardan en `sessions/linkedin/Default/Cookies`.
4. Ejecuciones siguientes: Chrome carga el perfil guardado → ya está logueado.

### Indeed / GetOnBoard — Sin login requerido

`requires_login: False` en `SITE_CONFIG`. El bot no verifica ni intenta login. Algunas funciones  
(como "Solicitud sencilla de Indeed") pueden requerir cuenta, pero el bot lo detecta a nivel de botón ausente.

---

## 7.5 Tabla de Compatibilidad de Selectores con Cambios de Portal

Los portales actualizan su HTML periódicamente. Esta tabla indica la probabilidad de rotura y el impacto:

| Portal | Frecuencia de cambios HTML | Selector más frágil | Impacto si rompe | Síntoma observable |
|---|---|---|---|---|
| **Laborum** | Media (cada 1-3 meses) | `button[class*='sc-enLHqu']` (`laborum.py:55`) | No encuentra botón → `error: no_apply_button` | Todas las ofertas retornan `no_apply_button` |
| **LinkedIn** | Alta (cada 2-4 semanas) | `span.t-14.t-black--light` (step indicator) | No detecta pasos del modal → puede exceder MAX_MODAL_STEPS | Log: `step=0/0` en todos los pasos |
| **Indeed** | Baja-Media | `div.job_seen_beacon` | No extrae cards → lista de ofertas vacía | Log: "0 ofertas detectadas" |
| **GetOnBoard** | Baja | `a.gb-results-list__item` | No extrae links de ofertas | Lista vacía |

**Proceso de mantenimiento cuando un selector rompe:**

```
1. Ejecutar bot con --dry-run → observar el error en logs/
2. Abrir portal en Chrome con F12 (DevTools) → inspeccionar el elemento roto
3. Copiar el selector correcto
4. Actualizar el dict SEL = {...} en el portal correspondiente
5. Re-ejecutar con --dry-run para confirmar
```
