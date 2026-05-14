# 07 — Flujos de Comunicacion e Integraciones

## 7.1 Inventario de Conexiones

| ID | Tipo | Origen | Destino | Mecanismo | Archivo |
|---|---|---|---|---|---|
| C01 | Interna | gui_server.py | main.py | subprocess.Popen (stdout pipe) | gui_server.py:114 |
| C02 | Interna | engine.py | form_filler.py | llamada directa fill_form() | engine.py:124,163 |
| C03 | Interna | engine.py | state.py | already_applied(), save_application() | engine.py:590,628 |
| C04 | Interna | engine.py | retry.py | rate_limiter.acquire() | engine.py:637 |
| C05 | WebSocket | index.html | gui_server.py | SocketIO /bot namespace | gui_server.py:19 |
| C06 | Externa | engine.py | Portal web | Playwright HTTP/HTTPS | engine.py:550 |
| C07 | FileSystem | engine.py | sessions/<portal>/ | Playwright user_data_dir | engine.py:536 |
| C08 | FileSystem | form_filler.py | data/profile_kb.json | json.load() | form_filler.py:_load_kb |
| C09 | FileSystem | engine.py | logs/applied_*.csv | csv.writer | engine.py:_csv_log |
| C10 | DB | state.py | data/applyjob.db | sqlite3 | state.py |

## 7.2 Flujo Principal: Busqueda Atomica desde Dashboard

```
sequenceDiagram
  participant UI as Dashboard (index.html)
  participant GS as gui_server.py
  participant MAIN as main.py
  participant ENG as engine.py
  participant FF as form_filler.py
  participant DB as SQLite
  participant WEB as Portal Web

  UI->>GS: socket.emit('start_master', {portals: ['indeed']})
  GS->>GS: run_bot_thread(['indeed']) en hilo
  GS->>MAIN: subprocess.Popen(['python','main.py','--portal','indeed','--multi-keyword'])
  MAIN->>ENG: run_bot_multi_keywords('indeed')
  loop Para cada keyword en KEYWORD_GROUPS
    ENG->>ENG: build_config_for_keyword('indeed', 'desarrollador junior')
    ENG->>ENG: run_bot('indeed', config_override, profile_mode='it')
    ENG->>WEB: Playwright navegar a url_busqueda
    WEB-->>ENG: HTML con ofertas
    loop Para cada oferta
      ENG->>DB: already_applied(url)?
      DB-->>ENG: False
      ENG->>WEB: navegar a oferta, detectar titulo
      ENG->>FF: fill_form(page, profile, job_title)
      FF->>FF: _load_kb() -> cover_letter IT
      FF-->>ENG: formulario llenado
      ENG->>WEB: click submit
      ENG->>DB: save_application(url, 'applied')
      ENG->>GS: stdout '[EXITO] ...'
      GS->>UI: socket.emit('new_log', msg)
      GS->>UI: socket.emit('update_stats', stats)
    end
  end
  ENG->>GS: stdout '>>> FINALIZANDO <<<'
  GS->>UI: socket.emit('bot_status', {status: 'finished'})
```

## 7.3 Comunicacion SocketIO

### Eventos servidor -> cliente

| Evento | Cuando | Payload |
|---|---|---|
| `bot_status` | Al conectar, al iniciar, al terminar | `{status, running, stats, logs}` |
| `new_log` | Cada linea de stdout del bot | `{message: str}` |
| `update_stats` | Al detectar [EXITO] o [FALLO] en logs | `{applied, errors, total}` |
| `portal_change` | Al detectar [PORTAL_ACTIVO] en logs | `{portal: str}` |
| `session_status` | Al conectar cliente | `{indeed: bool, linkedin: bool, ...}` |
| `captcha_required` | Al detectar [CAPTCHA] en logs | `{message: str}` |

### Eventos cliente -> servidor

| Evento | Cuando | Payload |
|---|---|---|
| `start_master` | Click "Iniciar" | `{portals: [str]}` |
| `stop_master` | Click "Detener" | — |

## 7.4 Resiliencia

| Conexion | Timeout | Retry | Fallback |
|---|---|---|---|
| Playwright goto() | 30s | 2 intentos (with_retry) | Screenshot de error |
| Rate limiter | — | Bloqueo hasta slot disponible | — |
| Login requerido | 20 min | Polling cada 3s | TimeoutError |
| subprocess portal | — | Siguiente portal tras excepcion | Log de error |
