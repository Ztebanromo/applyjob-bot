# 1.1 Propósito del Proyecto

El proyecto **ApplyJob Bot** es un sistema de automatización de grado profesional diseñado para optimizar y masificar el proceso de búsqueda de empleo. Resuelve el problema de la naturaleza repetitiva y agotadora de postular a vacantes en múltiples portales, permitiendo al usuario configurar su perfil una sola vez y dejar que el bot navegue, filtre y aplique a las ofertas de forma autónoma.

**Actores del Sistema:**
- **Usuario Final**: Persona en búsqueda activa de empleo que proporciona su CV y criterios de búsqueda.
- **Portales de Empleo (Target)**: LinkedIn, Indeed, Chiletrabajos, Computrabajo, Laborum, GetOnBoard.
- **Administrador (Modo Maestro)**: Perfil técnico que supervisa la ejecución global desde el Dashboard.

**Valor de Negocio Principal:**
Reducción drástica del "Time-to-Apply". Lo que a un humano le toma horas de navegación manual, el bot lo ejecuta en minutos, operando con técnicas de sigilo para evitar detecciones y manteniendo un registro persistente de cada postulación.

---

## 1.2 Stack Tecnológico

### Playwright (v1.44.0)
- **¿Qué es?**: Una biblioteca de automatización de navegadores moderna y de alto rendimiento.
- **Uso en el Proyecto**: Es el motor principal que controla el navegador (Chromium), permitiendo interactuar con el DOM de los portales de empleo como si fuera un humano.
- **Uso Específico**: `bot/engine.py:1`

### Flask (versión no especificada en manifest, detectada en runtime)
- **¿Qué es?**: Un micro-framework web para Python.
- **Uso en el Proyecto**: Proporciona el servidor que sirve el Dashboard y los endpoints de control del bot.
- **Uso Específico**: `gui_server.py:5`

### Playwright-Stealth (v1.0.6)
- **¿Qué es?**: Un plugin diseñado para ocultar las señales de automatización del navegador.
- **Uso en el Proyecto**: Evita que los portales (especialmente Indeed y LinkedIn) detecten al bot como un software automatizado, eludiendo bloqueos básicos.
- **Uso Específico**: `bot/stealth_utils.py:1`

### SQLite (Integrado en Python)
- **¿Qué es?**: Un motor de base de datos relacional ligero y sin servidor.
- **Uso en el Proyecto**: Guarda el historial de postulaciones para evitar duplicados y generar estadísticas.
- **Uso Específico**: `bot/state.py:1`

---

## 1.3 Glosario Exhaustivo

**[Portal Handler]** — Clase especializada que encapsula los selectores y la lógica de navegación de un sitio web específico → Permite al bot "entender" la estructura única de cada portal de empleo → `bot/portals/base.py:21`

**[Easy Apply]** — Proceso de postulación simplificado dentro de un portal → El bot busca estos botones para completar la aplicación sin salir del sitio original → `bot/portals/linkedin.py:48`

**[Stealth Hooks]** — Técnicas de inyección de código y retardos aleatorios → Simulan el comportamiento humano (movimiento de mouse, tiempos de lectura) para no ser baneado → `bot/stealth_utils.py:22`

**[Master Mode]** — Modo de ejecución secuencial global → Orquesta el bot para que recorra todos los portales configurados uno tras otro de forma desatendida → `main.py:147`

**[Job JK / ID]** — Identificador único de una vacante en Indeed/LinkedIn → Se usa para rastrear ofertas en la base de datos y evitar postulaciones dobles → `bot/portals/indeed.py:14`

**[Headless Mode]** — Ejecución del navegador sin interfaz gráfica → Se usa para ahorrar recursos y ejecutar el bot en servidores, aunque este proyecto prioriza el modo visible para supervisión de login → `main.py:102`
