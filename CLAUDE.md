# CLAUDE.md — applyjob-bot

Bot de postulación automática a empleos (Chile/LATAM). Automatiza búsqueda, filtrado y aplicación en portales de trabajo vía Playwright.

## Stack

| Capa | Tecnología |
|---|---|
| Automatización | Playwright 1.59 + playwright-stealth |
| GUI / API | Flask 3.1 + Flask-SocketIO (waitress en prod) |
| Python | 3.12 |
| Linter | ruff |
| Tests | pytest |

## Estructura clave

```
main.py              # Entrypoint CLI — orquesta engine por portal
gui_server.py        # Dashboard web (Flask) + endpoints REST
bot/
  engine.py          # Loop principal de postulación
  config.py          # Carga .env, parámetros globales
  form_filler.py     # Rellena formularios ATS
  keyword_optimizer.py # Puntúa/retira keywords por rendimiento
  validator.py       # Filtra ofertas por criterios
  retry.py           # Lógica de reintentos
  dedup.py           # Deduplicación de ofertas vistas
  state.py           # Estado en memoria (sin BD persistente)
  session_stats.py   # Estadísticas por sesión
  notifier.py        # Notificaciones de eventos
  qa_cache.py        # Cache de respuestas a preguntas ATS
  portals/
    linkedin.py      computrabajo.py  chiletrabajos.py
    laborum.py       trabajando.py    getonyboard.py
    indeed.py        infojobs_cl.py   remoteco.py
    remotive.py      weworkremotely.py
templates/
  index.html         # Dashboard SPA
data/                # keyword_stats.json, quick_links, etc.
sessions/            # Cookies de sesión por portal
errors/              # Screenshots de errores
logs/                # Logs por corrida
```

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows/Git Bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env            # rellenar credenciales
```

## Ejecución

```bash
# Bot CLI
python main.py

# Dashboard web
python gui_server.py            # http://localhost:5000

# Tests
python -m pytest tests/ -v

# Linter
python -m ruff check .
```

## Variables .env clave

| Variable | Notas |
|---|---|
| `LINKEDIN_EMAIL/PASSWORD` | Credenciales LinkedIn |
| `DASHBOARD_PASSWORD` | Protege dashboard (recomendado) |
| `MAX_APPLY_PER_RUN` | Límite de postulaciones por corrida |
| `HEADLESS` | `true` en prod, `false` para debug visual |
| `PROXY_*` | Configuración proxy opcional |

## Decisiones arquitectónicas

- **Sin BD persistente**: el estado de sesión vive en memoria (`bot/state.py`). Reiniciar el proceso limpia el estado.
- **`MIN_RUNS_TO_RETIRE = 3`**: una keyword necesita 3 corridas con `found=0` para ser retirada (evita falsos retiros).
- **Deduplicación**: `bot/dedup.py` evita postular dos veces a la misma oferta dentro de una sesión.
- **stealth**: playwright-stealth reduce detección de bot en portales.

## Convenciones

- Respuestas en español
- Sin comentarios en código salvo lógica no evidente
- Tests en `tests/` con `monkeypatch` para paths de datos (nunca tocar `data/` real en tests)
