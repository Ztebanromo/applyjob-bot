# 09 — Deuda Tecnica y Bugs Conocidos

## 9.1 Hallazgos por Severidad

### Rojo Criticos

**[C01]** `bot/engine.py:183` — `run_startup_validation` usa siempre `SITE_CONFIG[portal_name]` aunque se pase `config_override`.
- **Problema:** Valida la config original, no la override. Si la URL de busqueda cambia por keyword atomico, la validacion no lo refleja.
- **Impacto:** Bajo en practica (la validacion es de USER_PROFILE, no de URL), pero inconsistente.
- **Fix:** Pasar `config` en lugar de `SITE_CONFIG[portal_name]` a `run_startup_validation`.

**[C02]** `main.py:183` — `portal_name if 'portal_name' in locals() else args.portal` (codigo original, ya corregido en v2).
- **Estado:** Resuelto. La variable residual fue eliminada.

### Amarillo Medios

**[M01]** `bot/form_filler.py:_load_kb()` — El KB se carga una vez y se cachea en `_PROFILE_KB` global. Si el archivo cambia en runtime, no se recarga.
- **Impacto:** Requiere reiniciar el bot para ver cambios en `profile_kb.json`.
- **Fix sugerido:** Verificar mtime del archivo antes de retornar cache, o aceptar el comportamiento como intencional.

**[M02]** `gui_server.py:run_bot_thread` — No soporta `--multi-keyword`. Al lanzar desde el dashboard, siempre usa la URL combinada.
- **Impacto:** La busqueda atomica solo es accesible via CLI.
- **Fix sugerido:** Agregar checkbox "Busqueda Atomica" en el dashboard y pasar `--multi-keyword` en el `cmd`.

**[M03]** `data/qa_cache.json` y `data/profile_kb.json` coexisten con logica similar pero sin integracion.
- **Impacto:** Dos fuentes de respuestas que pueden contradecirse.
- **Fix sugerido:** Unificar en `profile_kb.json` o hacer que `form_filler` consulte `qa_cache` como fallback.

### Verde Menores

**[V01]** `bot/config.py` — `build_config_for_keyword` esta definida despues de que se usa `SITE_CONFIG` como argumento, pero `SITE_CONFIG` aun no existe en ese punto del modulo. Funciona porque Python evalua las funciones al llamarlas, no al definirlas.
- **Impacto:** Ninguno en runtime. Confunde al leer el codigo.
- **Fix:** Mover `KEYWORD_GROUPS` y `build_config_for_keyword` al final del archivo, despues de `SITE_CONFIG`.

**[V02]** `templates/index.html:297` — La lista de portales esta hardcodeada en Jinja. Agregar un nuevo portal a `SITE_CONFIG` no actualiza automaticamente el dashboard.
- **Fix sugerido:** Pasar `SITE_CONFIG.keys()` como variable de contexto desde la ruta `/` en `gui_server.py`.

## 9.2 Cobertura de Tests

- **Tests automatizados:** Ninguno. No hay directorio `tests/` ni archivos `test_*.py`.
- **Flujos criticos sin test:**
  - Deduplicacion (already_applied)
  - Deteccion de modo IT/Bodega desde titulo
  - build_config_for_keyword genera URLs correctas
  - fill_form usa cover_letter del modo correcto
  - Rate limiter bloquea correctamente

## 9.3 Health Score

| Dimension | Puntos | Justificacion |
|---|---|---|
| Arquitectura | 16/20 | Capas claras, Strategy bien aplicado. Falta desacoplar validacion de config_override. |
| Seguridad | 14/20 | .env para secrets, no hay inyeccion SQL (parametros en SQLite). Sin HTTPS en dashboard local. |
| Calidad de codigo | 15/20 | Docstrings en funciones criticas, nombres descriptivos. Algunos globals mutables (KB cache). |
| Testing | 0/20 | Sin tests automatizados de ningun tipo. |
| Documentacion | 9/10 | 9 docs generados, ROADMAP y TROUBLESHOOTING presentes. |
| Deploy/Infra | 6/10 | run_jobs.bat para Windows. Sin Docker, sin CI/CD, sin systemd/pm2. |
| **TOTAL** | **60/100** | Proyecto funcional y bien estructurado. El 0 en testing es el gap mas critico. |
