# 5.1 Estrategia de Manejo de Errores

El sistema utiliza una estrategia de **"Fail-Safe" con Logging Exhaustivo**. En lugar de detener toda la ejecución ante un error en una oferta individual, el bot captura la excepción, toma una captura de pantalla del error y continúa con la siguiente vacante.

- **Mecanismo Global**: `try-except` en el loop principal de `engine.py`.
- **Logging**: Los errores se registran en `logs/applyjob.log` y se envían al Dashboard.
- **Evidencia Visual**: `bot/stealth_utils.py:take_error_screenshot()` guarda una imagen del estado del navegador en `errors/` cuando algo falla.

---

## 5.2 Inventario de Puntos de Falla

| Severidad | Archivo:Línea | Escenario de falla | Comportamiento actual | Mejora Sugerida |
| :--- | :--- | :--- | :--- | :--- |
| 🔴 **Crítico** | `engine.py:596` | Página cerrada por el portal | Intenta recrear la página, pero puede perder el contexto del loop. | Re-iniciar el portal desde la página de búsqueda automáticamente. |
| 🟡 **Medio** | `indeed.py:135` | Timeout en Cloudflare | Aborta el portal actual y pasa al siguiente. | Notificación sonora o push al dashboard para intervención humana inmediata. |
| 🟢 **Menor** | `form_filler.py:42` | Selector de input no encontrado | Salta el campo y continúa. | Registro del selector faltante para actualización de config. |

---

## 5.3 Validaciones de Entrada

El proyecto implementa validaciones en `bot/validator.py`:
- Verifica que el archivo CV exista y sea un PDF válido.
- Valida que todos los campos requeridos en `.env` estén presentes.
- Comprueba la conectividad básica con los portales antes de iniciar la sesión de automatización.

---

## 5.4 Puntos Ciegos Identificados

1.  **Formularios con iFrames Complejos**: Algunos portales cargan el formulario de aplicación en un iFrame de otro dominio. El bot actual no siempre logra "saltar" dentro de estos iFrames si no están mapeados.
2.  **Límites de Rate Limiting**: Si el bot aplica a demasiadas ofertas en poco tiempo (ej: >50 por portal), la cuenta del usuario podría ser marcada como spam. Actualmente solo se controla vía `max_offers_per_run`.
3.  **Detección de Sesión Expirada**: Si el usuario es deslogueado durante la ejecución, el bot puede quedar atrapado en la página de login sin saber que debe pedir ayuda.
