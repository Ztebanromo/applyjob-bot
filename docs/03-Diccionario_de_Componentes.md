# 03 — Diccionario de Componentes

## BotState — `gui_server.py:25`
**Proposito:** Estado global thread-safe del proceso del bot en el servidor web.
**Patron:** Singleton con Lock threading.

| Metodo | Parametros | Retorno | Logica | Efectos |
|---|---|---|---|---|
| `add_log(message)` | str | None | Agrega al buffer, emite SocketIO, detecta tags | SocketIO emit, stdout |
| `get_status()` | — | dict | Retorna snapshot del estado actual | lectura |
| `stop_process()` | — | bool | Mata el subprocess si existe | process.kill() |
| `clear_logs()` | — | None | Limpia buffer y stats | SocketIO emit |

---

## run_bot() — `bot/engine.py:499`
**Proposito:** Orquestador principal. Abre browser, itera ofertas, aplica estrategia.
**Patron:** Template Method (flujo fijo, pasos intercambiables por portal).

| Parametro | Tipo | Descripcion |
|---|---|---|
| `portal_name` | str | Clave de SITE_CONFIG |
| `dry_run` | bool | Sin submit final |
| `headless` | bool | Browser sin ventana |
| `config_override` | dict | URL alternativa (busqueda atomica) |
| `profile_mode` | str | "it" o "bodega" |

**Variables criticas:** `applied` (contador), `rate_limiter` (por portal), `session_dir` (path Playwright).

---

## run_bot_multi_keywords() — `bot/engine.py:459`
**Proposito:** Itera KEYWORD_GROUPS y llama run_bot() por cada termino.
**Flujo:** Para cada group en KEYWORD_GROUPS -> build_config_for_keyword() -> run_bot(config_override, profile_mode).

---

## fill_form() — `bot/form_filler.py:430`
**Proposito:** Detecta y rellena todos los campos del formulario activo en la pagina.
**Patron:** Chain of Responsibility (text -> dropdowns -> radio -> file).

| Sub-funcion | Que detecta | Logica especial |
|---|---|---|
| `fill_text_fields()` | inputs text/email/tel/number, textarea | Autocomplete para ciudad, limpieza de telefono |
| `fill_dropdowns()` | select | Prioriza Chile/+56, fallback afirmativo |
| `handle_yes_no_questions()` | radio, checkbox | Prioriza valores en YES_VALUES |
| `fill_file_upload()` | input[type=file] | Sube CV del path en profile |

**Nuevo en v2:** Carga `profile_kb.json` via `_load_kb()`, detecta modo por `profile["_mode"]` o infiere de `job_title`. Sobreescribe `cover_letter` con la version contextual.

---

## build_config_for_keyword() — `bot/config.py:34`
**Proposito:** Genera una copia de SITE_CONFIG[portal] con URL especifica para un keyword.
**Retorno:** dict con `url_busqueda` reemplazada. Agrega "junior sin experiencia" automaticamente.

---

## get_session_status() — `gui_server.py:113`
**Proposito:** Detecta que portales tienen sesion guardada (carpeta sessions/<portal> no vacia).
**Retorno:** `{"indeed": True, "linkedin": False, ...}`
**Uso:** Emitido via SocketIO evento `session_status` al conectar el dashboard.

---

## already_applied() — `bot/state.py`
**Proposito:** Deduplicacion. Retorna True si la URL ya fue procesada con status "applied" o "skipped_already_applied".
**Indice:** Columna `url` indexada en SQLite para busqueda O(log n).

---

## RateLimiter — `bot/retry.py`
**Proposito:** Ventana deslizante de acciones maximas por hora por portal.
**Logica:** `acquire()` bloquea con `time.sleep()` si se alcanzo el maximo. Ventana: `max_actions` en `window_minutes`.

| Portal | Limite |
|---|---|
| LinkedIn | 25/hora |
| Indeed | 15/hora |
| Computrabajo | 25/hora |
| Default | 15/hora |
