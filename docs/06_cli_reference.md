# Referencia CLI completa

Todos los comandos disponibles en `main.py`.

---

## Sintaxis general

```bash
python main.py [COMANDO] [OPCIONES]
```

---

## Comandos de información

### `--list-portals`
Lista todos los portales configurados con sus parámetros.

```bash
python main.py --list-portals
```

Salida:
```
Portales disponibles:

  linkedin           tipo=modal      max=15    requiere login
  indeed             tipo=modal      max=10    sin login
  computrabajo       tipo=directa    max=20    sin login
  getonyboard        tipo=externa    max=10    sin login
```

---

### `--stats`
Muestra estadísticas del historial de postulaciones desde la DB.

```bash
python main.py --stats
```

Salida:
```
====================================================
  ApplyJob Stats  —  Total: 47
====================================================

  linkedin
    applied                      23  ███████████████████████
    skipped_no_easy_apply         8  ████████

  Últimas 5 postulaciones:
    [2024-05-15] linkedin        applied              Senior Python Dev
```

---

## Comandos de operación

### `--portal` / `-p`
**Requerido para correr el bot.** Especifica el portal a usar.

```bash
python main.py --portal linkedin
python main.py -p indeed
```

Valores válidos: `linkedin`, `indeed`, `computrabajo`, `getonyboard` (o cualquier clave de `SITE_CONFIG`).

---

### `--max` / `-m`
Sobreescribe `max_offers_per_run` del config para esta ejecución.

```bash
python main.py --portal linkedin --max 5
python main.py -p computrabajo -m 30
```

---

### `--dry-run`
Navega y detecta ofertas pero **no postula**. Registra como `dry_run` en la DB.

Ideal para:
- Verificar que los selectores funcionan
- Probar sin riesgo antes de un run real
- Primer uso de un portal nuevo

```bash
python main.py --portal computrabajo --dry-run
```

---

### `--headless`
Corre el browser sin ventana visible.

```bash
python main.py --portal computrabajo --headless
```

⚠ No usar en el primer run de portales con `requires_login=True`. Necesitás ver el browser para iniciar sesión manualmente.

---

## Comandos de diagnóstico

### `--validate`
Valida la configuración de `USER_PROFILE` y del portal especificado **sin abrir el browser**.

```bash
python main.py --validate --portal linkedin
```

Salida si todo está bien:
```
✓ Configuración válida — puedes correr el bot.
```

Salida si falta algo:
```
✗ Error de configuración:
  USER_PROFILE incompleto:
    ✗ Campo obligatorio 'email' no configurado (valor actual: 'tuemail@gmail.com').
      Edita bot/config.py → USER_PROFILE.
```

---

### `--purge` + `--days`
Elimina registros `skipped_*` y `dry_run` más viejos que N días.
Los registros `applied` y `error` se conservan siempre.

```bash
# Eliminar registros de más de 90 días (default)
python main.py --purge

# Eliminar registros de más de 30 días
python main.py --purge --days 30
```

---

## Combinaciones frecuentes

```bash
# Verificar configuración antes de un run importante
python main.py --validate --portal linkedin && python main.py --portal linkedin

# Primera exploración de un portal nuevo
python main.py --portal mi_portal --dry-run --max 5

# Run de producción sin ventana
python main.py --portal linkedin --headless --max 15

# Run de prueba rápida
python main.py --portal computrabajo --max 3

# Mantenimiento semanal
python main.py --stats
python main.py --purge --days 60
```

---

## Códigos de salida

| Código | Significado |
|---|---|
| `0` | Éxito |
| `1` | Error de argumentos o configuración inválida |

---

## Variables de entorno relevantes

El bot no requiere variables de entorno para funcionar. Son todas opcionales:

| Variable | Usado en | Descripción |
|---|---|---|
| `NOTIFY_EMAIL` | F6 (pendiente) | Email para resúmenes |
| `NOTIFY_WEBHOOK` | F6 (pendiente) | Webhook Discord/Slack/ntfy |
| `SCHEDULE_PORTALS` | F7 (pendiente) | Portales para modo daemon |
| `SCHEDULE_TIMES` | F7 (pendiente) | Horarios de ejecución |
