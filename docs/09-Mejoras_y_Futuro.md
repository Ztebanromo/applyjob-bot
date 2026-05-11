# 9.1 Deuda Técnica Identificada

1.  **Refactor de `gui_server.py`**: El manejo de logs y procesos en variables globales es frágil. Se recomienda migrar a un sistema de colas (ej: Redis/Celery) si se planea escalar a múltiples usuarios.
2.  **Abstracción de Selectores**: Actualmente muchos selectores están "hardcoded" en los portal handlers. Moverlos a archivos YAML/JSON facilitaría la actualización ante cambios en el DOM.
3.  **Modularización del Frontend**: El archivo `index.html` tiene más de 600 líneas mezclando CSS, HTML y JS. Separar en componentes facilitaría el mantenimiento.

---

## 9.2 Roadmap de Funcionalidades (Sugeridas)

### Corto Plazo (Q1)
- [ ] **Notificaciones Push**: Avisar al móvil cuando se requiere resolver un CAPTCHA.
- [ ] **Dashboard de Estadísticas**: Gráficos de éxito/fallo por portal y por día.
- [ ] **Editor de Selectores**: Interfaz para actualizar selectores sin tocar el código.

### Mediano Plazo (Q2)
- [ ] **Multi-perfil**: Permitir gestionar diferentes perfiles de búsqueda y CVs desde la misma cuenta.
- [ ] **AI Form Solver**: Integración con GPT-4 Vision para responder preguntas complejas de formularios.
- [ ] **Exportación de Reportes**: Generar un Excel/PDF con el resumen de las postulaciones del mes.

---

## 9.3 Conclusión de la Auditoría

El proyecto **ApplyJob Bot** demuestra una arquitectura sólida y bien pensada para su propósito actual. La separación de responsabilidades entre el motor de navegación y los adaptadores de portales garantiza extensibilidad. Sin embargo, para alcanzar un nivel "Enterprise", debe profesionalizar el manejo de sesiones concurrentes y la gestión de la configuración dinámica.
