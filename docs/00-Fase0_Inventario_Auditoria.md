# Fase 0 - Inventario de Auditoría del Repositorio

Fecha de ejecución: 2026-05-12  
Prompt ejecutado: `PROMPT_AuditDoc_v3.md` v2.2

## Alcance

Este inventario cumple la Fase 0 del prompt de auditoría técnica. El repositorio contiene muchos artefactos generados por Playwright/Chrome, logs y capturas. Para que el mapa sea útil, se separa el inventario en:

- **Código y documentación fuente:** incluido en el árbol principal.
- **Artefactos generados:** `sessions/`, `logs/`, `errors/`, `.venv/`, `.git/`, `__pycache__/`.

Los artefactos generados existen y son relevantes para operación/debug, pero no representan código fuente mantenible.

## 0.1 Árbol Fuente Priorizado

```text
applyjob-bot/
|-- .env
|-- .env.example
|-- .gitignore
|-- LICENSE
|-- PROMPT_AuditDoc_v3.md
|-- README.md
|-- ROADMAP.md
|-- TROUBLESHOOTING.md
|-- gui_server.py
|-- import_cookies.py
|-- main.py
|-- requirements.txt
|-- run_jobs.bat
|-- bot/
|   |-- __init__.py
|   |-- config.py
|   |-- cv_parser.py
|   |-- engine.py
|   |-- form_filler.py
|   |-- logger.py
|   |-- profile_manager.py
|   |-- retry.py
|   |-- state.py
|   |-- stealth_utils.py
|   |-- validator.py
|   `-- portals/
|       |-- __init__.py
|       |-- base.py
|       |-- chiletrabajos.py
|       |-- computrabajo.py
|       |-- getonyboard.py
|       |-- indeed.py
|       |-- laborum.py
|       `-- linkedin.py
|-- data/
|   |-- applyjob.db
|   |-- profile_kb.json
|   `-- qa_cache.json
|-- docs/
|   |-- 01-Manual_Conceptual.md
|   |-- 02-Estructura_Arquitectonica.md
|   |-- 03-Diccionario_de_Componentes.md
|   |-- 04-Infraestructura_y_Dependencias.md
|   |-- 05-Manejo_de_Errores_y_Casos_Borde.md
|   |-- 06-Ecosistema_y_Stack_Tecnologico.md
|   |-- 07-Contratos_API_y_Selectores.md
|   |-- 07-Flujos_de_Comunicacion_e_Integraciones.md
|   |-- 08-Deuda_Tecnica_y_Bugs_Conocidos.md
|   |-- 08-Estado_de_Integridad_y_Seguridad.md
|   |-- 09-Mejoras_y_Futuro.md
|   |-- archive/ApplyJob_Bot_Informe_Tecnico.docx
|   `-- legacy/*.md
|-- research/
|   |-- inspect_ct.py
|   |-- inspect_laborum.py
|   |-- inspect_portals.py
|   |-- inspect2.py
|   |-- inspect3_indeed.py
|   |-- inspect4_final.py
|   |-- inspect5_laborum.py
|   |-- inspect6_laborum2.py
|   |-- inspect7_laborum_api.py
|   |-- inspect8_laborum_jobs.py
|   `-- profile_editor.html
|-- scratch/
|   `-- test_laborum_logic.py
`-- templates/
    `-- index.html
```

## 0.2 Conteo por Tipo

Conteo fuente, excluyendo `.git/`, `.venv/`, `__pycache__/`, `sessions/`, `errors/` y `logs/`.

| Extension | Cantidad | Uso principal |
|---|---:|---|
| .py | 33 | Backend, CLI, motor Playwright, portales, scripts de investigación |
| .md | 23 | Documentación, roadmap, troubleshooting, prompt de auditoría |
| .html | 2 | Dashboard Flask y editor/investigación |
| .json | 2 | Base de conocimiento de perfil y caché QA |
| .db | 1 | SQLite operativo |
| .bat | 1 | Ejecución local Windows |
| .env / .example | 2 | Configuración local y ejemplo |

## 0.3 Stack Detectado

| Tecnología | Versión detectada | Evidencia | Rol |
|---|---:|---|---|
| Python | 3.10+ documentado | `README.md` | Lenguaje principal |
| Playwright | 1.44.0 | `requirements.txt:1` | Automatización browser |
| playwright-stealth | 1.0.6 | `requirements.txt:2` | Evasión anti-bot |
| python-dotenv | 1.0.1 | `requirements.txt:3` | Variables de entorno |
| pypdf | 4.2.0 | `requirements.txt:4` | Lectura de CV PDF |
| python-docx | 1.1.2 | `requirements.txt:5` | Lectura de CV DOCX |
| Flask | instalado en `.venv`, importado | `gui_server.py:9` | Dashboard HTTP |
| Flask-SocketIO | instalado en `.venv`, importado | `gui_server.py:10` | Logs y estado en tiempo real |
| SQLite | stdlib `sqlite3` | `bot/state.py:22` | Deduplicación e historial |

## 0.4 Patrón Arquitectónico Dominante

El proyecto es un **monolito modular por capas livianas**:

- `main.py` es el punto de entrada CLI.
- `gui_server.py` expone una interfaz HTTP/WebSocket local para operar el bot.
- `bot/engine.py` orquesta navegación, login, deduplicación, rate limit y postulación.
- `bot/portals/` contiene adaptadores por portal sobre `BasePortal`.
- `bot/state.py` encapsula persistencia SQLite.
- `bot/form_filler.py`, `bot/retry.py`, `bot/stealth_utils.py` son servicios utilitarios compartidos.

No se detecta Clean Architecture estricta ni contenedor de inyección de dependencias. Hay separación funcional, pero con configuración global y llamadas directas entre módulos.

## 0.5 Conteos Técnicos

| Categoría | Cantidad | Archivos clave |
|---|---:|---|
| Archivos Python fuente | 33 | `main.py`, `gui_server.py`, `bot/*.py`, `bot/portals/*.py` |
| Clases | 10 | `BotState`, `RateLimiter`, `BasePortal`, 6 portales, `ConfigError` |
| Funciones/métodos | 194 | Motor, parsers, fillers, validadores, endpoints |
| Endpoints HTTP Flask | 4 | `/`, `/api/config`, `/api/parse_cv`, `/save_config` en `gui_server.py:317`, `gui_server.py:321`, `gui_server.py:336`, `gui_server.py:367` |
| Eventos SocketIO | 3 | `connect`, `start_master`, `stop_master` en `gui_server.py:391`, `gui_server.py:397`, `gui_server.py:421` |
| Modelos de datos persistentes | 1 tabla SQLite | `applications` en `bot/state.py:39` |
| Migraciones | 0 | No hay sistema de migraciones; la tabla se crea por código |
| Tests detectados | 1 archivo scratch | `scratch/test_laborum_logic.py` |
| Docker/CI | 0 | No se detectan `Dockerfile`, `docker-compose.yml` ni workflows CI |

## 0.6 Artefactos Generados Detectados

| Directorio | Tipo | Uso |
|---|---|---|
| `sessions/` | Perfiles persistentes Chrome/Playwright | Mantener login por portal |
| `logs/` | Logs rotados y CSV | Trazabilidad de ejecuciones |
| `errors/` | Screenshots PNG | Debug de fallas por portal |
| `.venv/` | Entorno virtual Python | Dependencias locales |
| `__pycache__/` | Bytecode Python | Generado por runtime |

## 0.7 Hallazgos Iniciales

| Categoría | Hallazgo | Evidencia |
|---|---|---|
| Privacidad | El dashboard fue ajustado para no persistir CV ni datos personales; solo conserva configuración operativa. | `gui_server.py:170`, `gui_server.py:174`, `gui_server.py:385` |
| Runtime dashboard | El dashboard usa SocketIO para estado/logs en tiempo real. | `gui_server.py:391`, `templates/index.html:849` |
| CV temporal | El parseo de CV usa archivo temporal y lo elimina al terminar. | `gui_server.py:349` |
| Búsqueda | Existe modo multi-keyword en CLI, pero el dashboard todavía inicia `main.py --portal` por portal. | `main.py:118`, `gui_server.py:265` |
| Persistencia | SQLite se inicializa desde código, sin migraciones formales. | `bot/state.py:36`, `bot/state.py:39` |

## 0.8 Siguiente Paso del Prompt

Según `PROMPT_AuditDoc_v3.md`, después de esta Fase 0 corresponde generar o actualizar, en orden:

1. `docs/01-Manual_Conceptual.md`
2. `docs/02-Estructura_Arquitectonica.md`
3. `docs/03-Diccionario_de_Componentes.md`
4. `docs/04-Infraestructura_y_Dependencias.md`
5. `docs/05-Manejo_de_Errores_y_Casos_Borde.md`
6. `docs/06-Ecosistema_y_Stack_Tecnologico.md`
7. `docs/07-Flujos_de_Comunicación_e_Integraciones.md`
8. `docs/08-Contratos_API_y_Endpoints.md` si aplica
9. `docs/09-Deuda_Técnica_y_Bugs_Conocidos.md`

Nota: ya existen documentos 01-09, por lo que el trabajo siguiente debería ser actualización incremental, no regeneración ciega.
