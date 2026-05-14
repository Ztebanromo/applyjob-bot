# 02 — Estructura Arquitectonica

## 2.1 Patron Arquitectonico

**Nombre:** Layered Architecture + Strategy Pattern

**Definicion:** La arquitectura en capas organiza el codigo en niveles donde cada capa solo puede llamar a la capa inmediata inferior. El Strategy Pattern permite intercambiar algoritmos (estrategias de postulacion) sin modificar el cliente.

**Evidencia en el codigo:**
- Capa de entrada: `main.py` (CLI) y `gui_server.py` (Web)
- Capa de orquestacion: `bot/engine.py`
- Capa de estrategias: `bot/portals/*.py` + funciones `_apply_directa/modal/externa`
- Capa de datos: `bot/state.py` (SQLite) + `bot/config.py`
- Capa de utilidades: `bot/stealth_utils.py`, `bot/retry.py`, `bot/form_filler.py`

## 2.2 Mapa de Capas/Carpetas

| Directorio | Responsabilidad | Puede importar | Archivos clave |
|---|---|---|---|
| `/` (raiz) | Entry points y servidor web | `bot/` | `main.py`, `gui_server.py` |
| `bot/` | Logica de negocio | `bot/portals/`, stdlib | `engine.py`, `config.py`, `form_filler.py` |
| `bot/portals/` | Implementaciones por portal | `bot/` (base) | `linkedin.py`, `indeed.py`, `computrabajo.py` |
| `data/` | Persistencia y knowledge base | — | `applyjob.db`, `profile_kb.json`, `qa_cache.json` |
| `sessions/` | Estado de browsers | — | `{portal}/` dirs |
| `templates/` | UI del dashboard | — | `index.html` |
| `logs/` | CSV diario + log rotativo | — | `applied_YYYY-MM-DD.csv` |

## 2.3 Flujo de Datos End-to-End

```
Dashboard (index.html)
  -- socket.emit('start_master') -->
gui_server.py:run_bot_thread()
  -- subprocess.Popen(['python', 'main.py', '--portal', X]) -->
main.py:main()
  -- run_bot(portal, config_override, profile_mode) -->
bot/engine.py:run_bot()
  -- Playwright: navegar a url_busqueda -->
  -- extraer offer_urls (selector_oferta) -->
  -- por cada URL: already_applied()? skip -->
  -- rate_limiter.acquire() -->
  -- _process_offer_generic() -->
      -- _apply_directa/modal/externa() -->
          -- fill_form(page, profile, job_title) -->
              -- _load_kb() -> cover_letter por modo -->
              -- fill_text_fields(), fill_dropdowns() -->
  -- save_application() -> SQLite -->
  -- _csv_log() -> logs/applied_YYYY-MM-DD.csv -->
  -- stdout -> gui_server captura y emite via SocketIO -->
Dashboard: actualiza terminal + stats en tiempo real
```

## 2.4 Diagrama de Dependencias

```
main.py ──────────────┐
                       ├──> bot/engine.py
gui_server.py ─────────┘        ├──> bot/config.py
                                 ├──> bot/state.py
                                 ├──> bot/form_filler.py ──> data/profile_kb.json
                                 ├──> bot/retry.py
                                 ├──> bot/stealth_utils.py
                                 ├──> bot/validator.py
                                 └──> bot/portals/
                                          ├──> base.py
                                          ├──> linkedin.py
                                          ├──> indeed.py
                                          └──> computrabajo.py ...
```

No hay dependencias circulares detectadas.
