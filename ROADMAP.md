# ROADMAP — ApplyJob Bot

Plan de implementación completo. Las fases se ejecutan en orden de impacto.

---

## Estado actual

| Símbolo | Significado |
|---|---|
| ✅ | Completado |
| 🔄 | En progreso |
| ⏳ | Pendiente |

---

## ✅ Fase 1 — Estructura base y configuración

**Objetivo:** scaffolding del proyecto y sistema de configuración central.

- [x] Estructura de carpetas (`bot/`, `sessions/`, `errors/`, `logs/`, `data/`)
- [x] `bot/config.py` — `USER_PROFILE` + `SITE_CONFIG` con 4 portales
- [x] `bot/stealth_utils.py` — evasión, delays aleatorios, user-agent rotation, viewport random
- [x] `bot/form_filler.py` — autocompletado por `name`/`id`/`placeholder`/`aria-label`, file upload, yes/no questions
- [x] `bot/engine.py` — motor principal con navegación, paginación y logging CSV
- [x] `main.py` — CLI con `--portal`, `--max`, `--dry-run`, `--headless`, `--list-portals`
- [x] `requirements.txt` — dependencias fijadas

**Esfuerzo:** ~8h | **Riesgo:** bajo

---

## ✅ Fase 2 — Motor genérico operativo

**Objetivo:** el motor genérico cubre portales sin clase específica.

- [x] Estrategia `directa` — click → fill → submit en la misma página
- [x] Estrategia `modal` — click abre overlay → fill → multi-step advance
- [x] Estrategia `externa` — click redirige a otro sitio → log y skip
- [x] `playwright-stealth` integrado con fallback manual
- [x] `apply_stealth()` inyecta scripts que ocultan fingerprints de Playwright
- [x] Paginación automática por `selector_siguiente_pagina`
- [x] Screenshot automático en carpeta `errors/` ante cualquier fallo

**Esfuerzo:** ~5h | **Riesgo:** bajo

---

## ✅ Fase 3 — Deduplicación y estado persistente (Score: 100 ✅)

**Objetivo:** el bot no postula dos veces a la misma oferta.

- [x] `bot/state.py` — SQLite con tabla `applications` e índices por url/portal/fecha
- [x] `already_applied(url)` — consulta O(1) por URL
- [x] `save_application(url, portal, title, status)` — upsert atómico
- [x] `get_stats()` — agrupado por portal y estado con barras visuales
- [x] `get_recent(n)` — últimas N postulaciones
- [x] `get_errors(portal)` — filtro de errores para diagnóstico
- [x] `purge_old(days=90)` — limpieza de registros viejos de skipped/dry_run
- [x] `python main.py --stats` — comando CLI con barras visuales
- [x] `python main.py --purge --days 90` — CLI para limpieza
- [x] Motor actualizado: skip automático si la URL ya está en DB

**Esfuerzo:** ~3h | **Riesgo:** bajo

---

## ✅ Fase 4 — Portal específico: LinkedIn Easy Apply (Score: 100 ✅)

**Objetivo:** flujo nativo de LinkedIn multi-step con detección dinámica.

- [x] `bot/portals/base.py` — clase abstracta `BasePortal`
- [x] `bot/portals/linkedin.py` — `LinkedInPortal` completo:
  - [x] Identificación de jobs por `data-job-id` (panel sin navegación)
  - [x] Detección de Easy Apply vs botón externo
  - [x] Skip si ya postulado (banner "Already applied")
  - [x] Apertura y espera del modal
  - [x] Detección de pasos (`Step X of Y`)
  - [x] Skip automático si >6 pasos
  - [x] `fill_form()` en cada step
  - [x] Manejo de dropdowns con `NO_VALUES` para no seleccionar respuestas negativas
  - [x] Avance por Next → Review → Submit
  - [x] Detección de CAPTCHA → skip seguro
  - [x] Cierre seguro del modal si se descarta
  - [x] Retorna `(status, title)` tuple para logging completo
- [x] `bot/portals/__init__.py` — registry de portales específicos
- [x] Todos los `except: pass` reemplazados por `except exc: log.debug(exc)`

**Esfuerzo:** ~8h | **Riesgo:** alto (LinkedIn cambia selectores frecuentemente)

---

## ✅ Fase 5b — Retry y Rate Limiting (Score: 100 ✅)

**Objetivo:** resiliencia ante errores de red y protección contra detección por volumen.

- [x] `bot/retry.py`:
  - [x] `is_transient_error(exc)` — clasifica errores de red vs errores permanentes
  - [x] `with_retry(fn, attempts, delay)` — reintenta ante errores transitorios
  - [x] `@retryable` — decorador para funciones reintentables
  - [x] `RateLimiter` — ventana deslizante, bloquea cuando supera el límite
  - [x] `RATE_LIMITS` — configurados por portal (LinkedIn: 10/h, Indeed: 15/h, etc.)
  - [x] `get_rate_limiter(portal)` — acceso al limiter correcto
- [x] Motor integrado: `rate_limiter.acquire()` antes de cada postulación
- [x] Motor integrado: `with_retry()` en `goto()` para navegación robusta

**Esfuerzo:** ~3h | **Riesgo:** bajo

---

## ✅ Fase 5c — Logging centralizado (Score: 100 ✅)

**Objetivo:** observabilidad completa sin perder rendimiento.

- [x] `bot/logger.py`:
  - [x] `configure_logging(level)` — único punto de setup
  - [x] Handler de consola: INFO, formato corto con hora
  - [x] Handler de archivo: DEBUG, formato completo con módulo
  - [x] Rotación diaria con `TimedRotatingFileHandler`
  - [x] Retención de 30 días automática
  - [x] `get_logger(name)` — helper para módulos internos
- [x] `logging.basicConfig` eliminado de `engine.py` (era a nivel de módulo)
- [x] Todos los módulos usan `logging.getLogger("applyjob.modulo")`

**Esfuerzo:** ~2h | **Riesgo:** bajo

---

## ✅ Fase 5d — Validación de configuración (Score: 100 ✅)

**Objetivo:** detectar configuración incorrecta antes de abrir el browser.

- [x] `bot/validator.py`:
  - [x] `load_env()` — carga `.env` si existe, silencioso si no
  - [x] `validate_profile(profile)` — verifica campos obligatorios y valores de ejemplo
  - [x] `validate_portal_config(portal, config)` — verifica selectores y tipo válido
  - [x] `run_startup_validation()` — punto de entrada, lanza `ConfigError` si falla
  - [x] Warnings no bloqueantes para campos opcionales sin completar
- [x] `python main.py --validate --portal linkedin` — comando CLI de diagnóstico
- [x] Soporte `.env` con `python-dotenv`
- [x] `ConfigError` con mensajes claros sobre qué campo falta y cómo arreglarlo

**Esfuerzo:** ~2h | **Riesgo:** bajo

---

## ✅ Fase 5e — Documentación completa (Score: 100 ✅)

- [x] `README.md` completo: instalación, uso, estructura, rate limiting, logs, consideraciones
- [x] `ROADMAP.md` actualizado con todas las fases y estados
- [x] `TROUBLESHOOTING.md`: 12 escenarios de error documentados con causas y soluciones
- [x] Docstrings completos en todos los módulos: engine, state, retry, validator, logger, portals
- [x] `TROUBLESHOOTING.md` cubre: selectores rotos, CAPTCHA, sesiones, rate limit, DB, diagnóstico

---

## ⏳ Fase 6 — Notificaciones

**Objetivo:** saber qué pasó sin revisar el CSV manualmente.

- [ ] `bot/notifier.py`
- [ ] Canal Email (smtplib) — resumen al finalizar cada run
- [ ] Canal Webhook — compatible con Discord, Slack, ntfy.sh
- [ ] Variables en `.env`: `NOTIFY_EMAIL`, `NOTIFY_WEBHOOK`
- [ ] Notificación de error crítico si el run falla en <3 ofertas

**Esfuerzo:** ~2h | **Riesgo:** bajo

---

## ⏳ Fase 7 — Scheduler autónomo

**Objetivo:** el bot corre solo sin intervención manual.

- [ ] `bot/scheduler.py` — usa `schedule` lib para runs periódicos
- [ ] Config en `.env`: `SCHEDULE_PORTALS`, `SCHEDULE_TIMES`
- [ ] `python main.py --daemon` — loop infinito con horarios
- [ ] `python main.py --install-task` — registra en Windows Task Scheduler
- [ ] Manejo de señales SIGINT/SIGTERM para shutdown limpio

**Esfuerzo:** ~2h | **Riesgo:** bajo

---

## ⏳ Fase 8 — Dashboard web local

**Objetivo:** visualizar el historial sin abrir archivos.

- [ ] `dashboard.py` — Flask mínimo (1 archivo)
- [ ] Tabla de postulaciones con filtros por portal/status/fecha
- [ ] Contadores por status
- [ ] Botón "Exportar CSV"
- [ ] `python dashboard.py` → `http://localhost:8080`

**Esfuerzo:** ~4h | **Riesgo:** bajo

---

## ⏳ Fase 9 — Hardening anti-detección

**Objetivo:** reducir el rate de bloqueos en LinkedIn e Indeed.

- [ ] Fingerprint de canvas/WebGL aleatorio
- [ ] Detección de "suspicious activity" page → pausa de 24h + notificación
- [ ] Soporte para proxy rotativo (config en `.env`)
- [ ] Variación de headers `Accept-Language` / `Accept-Encoding`

**Esfuerzo:** ~3h | **Riesgo:** medio

---

## ⏳ Fase 10 — Wizard de configuración

- [ ] `python main.py --setup` — CLI interactivo que genera `USER_PROFILE`
- [ ] `python main.py --add-portal` — guía para agregar sitio nuevo con validación de selectores
- [ ] Validación de CV path y campos al arrancar con sugerencias claras

**Esfuerzo:** ~2h | **Riesgo:** bajo

---

## ⏳ Fase 11 — Portal específico: Indeed

- [ ] `bot/portals/indeed.py` — `IndeedPortal`
- [ ] Detección de Indeed Apply vs external redirect
- [ ] Modal multi-step de Indeed
- [ ] Preguntas de screening de Indeed

**Esfuerzo:** ~4h | **Riesgo:** alto

---

## ⏳ Fase 12 — Empaquetado y distribución

- [ ] `Dockerfile`
- [ ] `docker-compose.yml` con volúmenes para `sessions/`, `logs/`, `data/`
- [ ] `setup.bat` / `setup.sh` — instalación con un click

**Esfuerzo:** ~3h | **Riesgo:** bajo

---

## Resumen de esfuerzo

| Fase | Estado | Esfuerzo |
|---|---|---|
| F1 — Estructura base | ✅ | ~8h |
| F2 — Motor genérico | ✅ | ~5h |
| F3 — Deduplicación SQLite | ✅ | ~3h |
| F4 — LinkedIn Easy Apply | ✅ | ~8h |
| F5b — Retry + Rate Limiting | ✅ | ~3h |
| F5c — Logging centralizado | ✅ | ~2h |
| F5d — Validación config | ✅ | ~2h |
| F5e — Documentación completa | ✅ | ~3h |
| F6 — Notificaciones | ⏳ | ~2h |
| F7 — Scheduler | ⏳ | ~2h |
| F8 — Dashboard | ⏳ | ~4h |
| F9 — Anti-detección avanzada | ⏳ | ~3h |
| F10 — Wizard config | ⏳ | ~2h |
| F11 — Indeed específico | ⏳ | ~4h |
| F12 — Docker/distribución | ⏳ | ~3h |
| **Completado** | **8/15 fases** | **~34h** |
| **Pendiente** | **7/15 fases** | **~20h** |
