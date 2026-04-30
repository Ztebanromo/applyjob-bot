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

- [x] Estructura de carpetas (`bot/`, `sessions/`, `errors/`, `logs/`)
- [x] `bot/config.py` — `USER_PROFILE` + `SITE_CONFIG` con 4 portales (LinkedIn, Indeed, Computrabajo, GetOnBrd)
- [x] `bot/stealth_utils.py` — evasión, delays aleatorios, user-agent rotation, viewport random
- [x] `bot/form_filler.py` — autocompletado por `name`/`id`/`placeholder`/`aria-label`, file upload, yes/no questions
- [x] `bot/engine.py` — motor principal con navegación, paginación y logging CSV
- [x] `main.py` — CLI con `--portal`, `--max`, `--dry-run`, `--headless`, `--list-portals`
- [x] `requirements.txt` — dependencias fijadas
- [x] Entorno virtual + Chromium instalados

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

## ✅ Fase 3 — Deduplicación y estado persistente

**Objetivo:** el bot no postula dos veces a la misma oferta.

- [x] `bot/state.py` — SQLite con tabla `applications`
- [x] `already_applied(url)` — consulta O(1) por URL
- [x] `save_application(url, portal, title, status)` — upsert atómico
- [x] `get_stats()` — agrupado por portal y status
- [x] `print_stats()` — output de consola formateado
- [x] `python main.py --stats` — comando CLI para ver historial
- [x] Motor actualizado: skip automático si la URL ya está en DB

**Esfuerzo:** ~2h | **Riesgo:** bajo

---

## ✅ Fase 4 — Portal específico: LinkedIn Easy Apply

**Objetivo:** flujo nativo de LinkedIn multi-step con detección dinámica.

- [x] `bot/portals/base.py` — clase abstracta `BasePortal` con `apply_to_offer()` y `get_offer_urls()`
- [x] `bot/portals/linkedin.py` — `LinkedInPortal` completo:
  - [x] Identificación de jobs por `data-job-id` (panel sin navegación)
  - [x] Detección de Easy Apply vs botón externo
  - [x] Skip si ya postulado (banner "Already applied")
  - [x] Apertura y espera del modal
  - [x] Detección de pasos (`Step X of Y`)
  - [x] Skip automático si >6 pasos (proceso muy largo)
  - [x] `fill_form()` en cada step
  - [x] Manejo de dropdowns de screening
  - [x] Avance por Next → Review → Submit
  - [x] Detección de CAPTCHA → skip seguro
  - [x] Cierre seguro del modal si se descarta
- [x] `bot/portals/__init__.py` — registry de portales específicos
- [x] `engine.py` actualizado para usar portal registry

**Esfuerzo:** ~6h | **Riesgo:** alto (LinkedIn cambia selectores frecuentemente)

---

## ⏳ Fase 5 — Sistema de notificaciones

**Objetivo:** saber qué pasó sin revisar el CSV manualmente.

- [ ] `bot/notifier.py`
- [ ] Canal Email (smtplib) — resumen al finalizar cada run
- [ ] Canal Webhook (POST JSON) — compatible con Discord, Slack, ntfy.sh
- [ ] Variables en `.env`: `NOTIFY_EMAIL`, `NOTIFY_WEBHOOK`
- [ ] Notificación de error crítico si el run falla en <3 ofertas
- [ ] Resumen diario opcional por email

**Esfuerzo:** ~2h | **Riesgo:** bajo

---

## ⏳ Fase 6 — Scheduler autónomo

**Objetivo:** el bot corre solo sin intervención manual.

- [ ] `bot/scheduler.py` — usa `schedule` lib para runs periódicos
- [ ] Config en `.env`: `SCHEDULE_PORTALS`, `SCHEDULE_TIMES`
- [ ] `python main.py --daemon` — loop infinito con horarios
- [ ] `python main.py --install-task` — registra en Windows Task Scheduler
- [ ] Manejo de señales SIGINT/SIGTERM para shutdown limpio
- [ ] Log de uptime y próxima ejecución

**Esfuerzo:** ~2h | **Riesgo:** bajo

---

## ⏳ Fase 7 — Dashboard web local

**Objetivo:** visualizar el historial de postulaciones sin abrir archivos.

- [ ] `dashboard.py` — Flask/FastAPI mínimo (1 archivo)
- [ ] Tabla de postulaciones con filtros por portal/status/fecha
- [ ] Contadores por status (applied / error / skipped / dry_run)
- [ ] Botón "Exportar CSV"
- [ ] `python dashboard.py` → `http://localhost:8080`
- [ ] Sin framework de frontend (HTML + CSS vanilla, sin npm)

**Esfuerzo:** ~4h | **Riesgo:** bajo

---

## ⏳ Fase 8 — Hardening anti-detección

**Objetivo:** reducir el rate de bloqueos en LinkedIn e Indeed.

- [ ] Fingerprint de canvas/WebGL aleatorio en `stealth_utils.py`
- [ ] Límite de postulaciones por hora configurable (default: 10/h LinkedIn)
- [ ] Backoff exponencial ante CAPTCHA (espera y reintento)
- [ ] Detección de "suspicious activity" page → pausa de 24h + notificación
- [ ] Soporte para proxy rotativo opcional (config en `.env`)
- [ ] Variación de `Accept-Language` y `Accept-Encoding` headers

**Esfuerzo:** ~3h | **Riesgo:** medio

---

## ⏳ Fase 9 — Wizard de configuración

**Objetivo:** setup inicial sin tocar código.

- [ ] `python main.py --setup` — CLI interactivo que genera `USER_PROFILE`
- [ ] `python main.py --add-portal` — guía para agregar sitio nuevo con validación de selectores
- [ ] Validación de CV path y campos obligatorios al iniciar
- [ ] Mensaje de advertencia si `USER_PROFILE` tiene valores por defecto

**Esfuerzo:** ~2h | **Riesgo:** bajo

---

## ⏳ Fase 10 — Portal específico: Indeed

**Objetivo:** flujo nativo de Indeed Easy Apply (similar a LinkedIn pero diferente).

- [ ] `bot/portals/indeed.py` — `IndeedPortal`
- [ ] Detección de Indeed Apply vs external redirect
- [ ] Modal multi-step de Indeed
- [ ] Preguntas de screening de Indeed (muy comunes)
- [ ] Registro en portal registry

**Esfuerzo:** ~4h | **Riesgo:** alto

---

## ⏳ Fase 11 — Empaquetado y distribución

**Objetivo:** instalación con un solo comando.

- [ ] `Dockerfile` — imagen con Python + Playwright + Chromium
- [ ] `docker-compose.yml` — volúmenes para `sessions/`, `logs/`, `data/`
- [ ] `.env.example` completo con todas las variables documentadas
- [ ] `setup.bat` / `setup.sh` — instalación automática para usuarios finales
- [ ] `pyproject.toml` — para distribución vía pip (opcional)

**Esfuerzo:** ~3h | **Riesgo:** bajo

---

## Resumen de esfuerzo

| Fase | Estado | Esfuerzo |
|---|---|---|
| F1 — Estructura base | ✅ | ~8h |
| F2 — Motor genérico | ✅ | ~5h |
| F3 — Deduplicación SQLite | ✅ | ~2h |
| F4 — LinkedIn Easy Apply | ✅ | ~6h |
| F5 — Notificaciones | ⏳ | ~2h |
| F6 — Scheduler | ⏳ | ~2h |
| F7 — Dashboard | ⏳ | ~4h |
| F8 — Anti-detección | ⏳ | ~3h |
| F9 — Wizard config | ⏳ | ~2h |
| F10 — Indeed específico | ⏳ | ~4h |
| F11 — Empaquetado | ⏳ | ~3h |
| **Total** | | **~41h** |

---

## Criterio de priorización

```
Impacto inmediato (hacer primero):
  F5 Notificaciones  → sabes qué pasa sin revisar logs
  F6 Scheduler       → set-and-forget, sin correr manual
  F8 Anti-detección  → menor riesgo de ban

Valor a mediano plazo:
  F7 Dashboard       → visibilidad del historial
  F10 Indeed         → segundo portal más importante

Pulido final:
  F9 Wizard          → experiencia de usuario
  F11 Docker         → deployment reproducible
```
