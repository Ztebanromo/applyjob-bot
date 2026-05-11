# 8.1 Seguridad de Datos

### Gestión de Secretos
- **Método**: Archivo `.env`.
- **Riesgo**: Actualmente el `.env` no está encriptado. Si el repositorio se hace público, las credenciales y datos personales (teléfono, email) quedarían expuestos.
- **Medida**: Existe un `.gitignore` que previene la subida del `.env` real al control de versiones.

### Almacenamiento de CV
- El currículum se sube a la carpeta `uploads/`. No existe un proceso de limpieza automática de versiones antiguas, lo que podría consumir espacio en disco a largo plazo.

---

## 8.2 Integridad del Sistema

### Reporte de Bugs / Puntos de Fricción
1.  **Race Condition en Logs**: Si dos portales escribieran simultáneamente (no ocurre actualmente por el loop secuencial), la variable `output_log` podría corromperse.
2.  **Validación de CV**: Si el PDF está protegido por contraseña, el extractor de texto fallará, rompiendo la lógica de autocompletado.

---

## 8.3 Medidas de Sigilo (Anti-Bot)

El proyecto utiliza técnicas avanzadas para no ser detectado:
- **User-Agent Aleatorio**: Emula diferentes navegadores y sistemas operativos.
- **Movimientos de Mouse No-Lineales**: Evita trayectorias perfectas que delatan automatización.
- **Retardos Humanos**: Pausas aleatorias entre clicks y desplazamientos (Scroll).
- **Stealth Hooks**: Inyección de scripts para ocultar propiedades como `navigator.webdriver`.

---

## 8.4 Matriz de Riesgos

| Riesgo | Probabilidad | Impacto | Mitigación |
| :--- | :--- | :--- | :--- |
| Baneado de LinkedIn | Media | Alta | Aumentar retardos en `stealth_utils.py` y limitar aplicaciones diarias. |
| Falla en llenado de formulario | Alta | Media | El bot detecta el fallo, saca captura y pasa a la siguiente oferta. |
| Inyección de comandos vía API | Baja | Muy Alta | Validar estrictamente los inputs en `gui_server.py`. |
