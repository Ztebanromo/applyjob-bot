# LinkedIn Easy Apply — Guía completa

LinkedIn tiene el flujo más complejo de todos los portales. Esta guía cubre todo lo que necesitás saber.

---

## Primer uso: iniciar sesión

LinkedIn requiere sesión activa. El **primer run siempre debe hacerse sin `--headless`**:

```bash
python main.py --portal linkedin --dry-run
```

1. El browser de Chromium abre automáticamente
2. Iniciás sesión en LinkedIn manualmente (usuario + contraseña)
3. La sesión queda guardada en `sessions/linkedin/`
4. Cerrás el script (Ctrl+C)
5. A partir de ahora, todos los runs (incluyendo `--headless`) usan esa sesión

La sesión dura semanas o meses. Solo hay que renovarla si LinkedIn cierra la sesión por inactividad prolongada.

---

## URL de búsqueda con Easy Apply

El filtro más importante es `f_AL=true` — filtra exclusivamente empleos con Easy Apply:

```
https://www.linkedin.com/jobs/search/?keywords=Python+Developer&location=Remote&f_AL=true
```

Sin ese parámetro, el bot encontrará empleos con aplicación externa y los saltará como `skipped_no_easy_apply`.

### Cómo construir la URL con filtros

1. Ve a LinkedIn Jobs en tu browser
2. Aplica todos los filtros que quieras (keywords, ubicación, nivel, fecha, etc.)
3. Activá el filtro **"Easy Apply"**
4. Copiá la URL completa de la barra de direcciones
5. Pegala en `bot/config.py` → `SITE_CONFIG["linkedin"]["url_busqueda"]`

---

## Flujo interno del bot

```
Para cada job_id en la página:
    1. Click en la card del job → panel derecho carga
    2. Verificar: ¿ya postulé? (banner "Applied") → skip
    3. Verificar: ¿tiene Easy Apply? → si no, skip
    4. Click en "Easy Apply" → modal se abre
    5. Detectar CAPTCHA → si hay, cerrar y skip
    6. Por cada paso del modal (máx 6):
        a. Leer "Step X of Y"
        b. Si total_steps > 6 → cerrar y skip (proceso muy largo)
        c. Llenar campos del paso actual
        d. Responder dropdowns de screening
        e. Click Next / Review / Submit
    7. Si Submit exitoso → status = "applied"
```

---

## Estados posibles de LinkedIn

| Status | Significado | Acción recomendada |
|---|---|---|
| `applied` | Postulación enviada con éxito | — |
| `skipped_already_applied` | Ya habías postulado a este job | Normal, ignorar |
| `skipped_no_easy_apply` | El job no tiene Easy Apply | Revisar URL de búsqueda |
| `skipped_complex_N_steps` | Modal tiene más de 6 pasos | Normal para jobs con formularios largos |
| `skipped_captcha` | LinkedIn detectó comportamiento automatizado | Esperá 24h, bajá el max |
| `error: modal_timeout` | El modal no abrió en 8 segundos | Selector puede haber cambiado |
| `error: card_not_loaded` | La card no respondió al click | Error puntual, no preocuparse |
| `error: max_steps_exceeded` | Se agotaron los intentos de avanzar | Revisar si hay campo requerido sin llenar |

---

## Preguntas de screening

LinkedIn frecuentemente agrega preguntas antes de dejar aplicar:

**Dropdowns** (ej. "¿Estás autorizado para trabajar en este país?"):
- El bot busca primero valores afirmativos: `yes`, `sí`, `authorized`, etc.
- Si no encuentra uno, elige el primer valor que no sea `no` / `not authorized`
- Lista completa en `bot/portals/linkedin.py` → `YES_VALUES` y `NO_VALUES`

**Radios** (ej. "¿Tienes experiencia con Python?"):
- El bot selecciona `Yes` / `Sí` cuando están disponibles

**Campos de texto** (ej. "¿Cuántos años de experiencia tenés?"):
- Detectados por `aria-label` o `placeholder`
- Rellenados con `USER_PROFILE["years_exp"]`

---

## Selectores — qué hacer cuando cambian

LinkedIn actualiza su HTML con frecuencia. Si el bot deja de funcionar:

1. Abrí LinkedIn Jobs en tu browser
2. F12 → Inspector
3. Inspeccioná el elemento que falló
4. Actualizá el selector en `bot/portals/linkedin.py` → `SEL`

```python
SEL = {
    "job_card":       "li.jobs-search-results__list-item",  # ← actualizar si cambia
    "easy_apply_btn": "button.jobs-apply-button--top-card", # ← el más propenso a cambiar
    "modal":          "div.jobs-easy-apply-modal",
    # ...
}
```

El selector más propenso a cambiar es `easy_apply_btn`. LinkedIn lo renombra periódicamente.

---

## Límites recomendados

| Escenario | max_offers_per_run | Riesgo |
|---|---|---|
| Cuenta nueva (<1 mes) | 5 | Bajo |
| Cuenta normal | 10-15 | Bajo |
| Cuenta con historial | 15 | Bajo |
| Querés ir rápido | 20+ | Alto — puede disparar CAPTCHA |

El rate limiter ya controla 10/hora automáticamente. `max_offers_per_run` es el tope total del run, independientemente del tiempo.
