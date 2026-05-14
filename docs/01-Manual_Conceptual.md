# 01 — Manual Conceptual

> Version: 2.0 — Actualizado tras implementacion de Evolved Master Mode.

---

## 1.1 Proposito del Proyecto

**ApplyJob Bot** automatiza el proceso de busqueda y postulacion a empleos en portales chilenos. El bot abre un navegador real, navega por los portales como lo haria una persona, detecta los formularios, los llena con los datos del usuario (adaptados al tipo de cargo) y hace click en enviar.

**Actores del sistema:**
- **Usuario final:** Ignacio Romo, buscador de empleo junior en TI y/o Bodega.
- **Portales:** Indeed, Computrabajo, Laborum, ChileTrabajos, LinkedIn, GetOnBrd.
- **Dashboard web:** Interfaz HTML/JS para controlar el bot en tiempo real.

**Valor:** Cubre 5 portales con busquedas atomicas separadas (IT y Bodega) y responde formularios con el perfil correcto para cada tipo de cargo.

---

## 1.2 Stack Tecnologico

| Tecnologia | Version | Que hace en este proyecto | Archivo clave |
|---|---|---|---|
| Python | 3.11+ | Lenguaje base | todos |
| Playwright | 1.44.0 | Controla el browser real (Chrome/Chromium) | bot/engine.py |
| playwright-stealth | 1.0.6 | Oculta que es un bot (desactiva navigator.webdriver) | bot/stealth_utils.py |
| Flask | runtime | Servidor HTTP para el dashboard | gui_server.py |
| Flask-SocketIO | runtime | Streaming en tiempo real de logs al browser | gui_server.py:19 |
| SQLite | builtin | Deduplicacion de ofertas + historial | data/applyjob.db |
| python-dotenv | 1.0.1 | Variables de entorno desde .env | bot/config.py:6 |
| pypdf | 4.2.0 | Extrae texto del CV en PDF | bot/profile_manager.py |

---

## 1.3 Glosario

**Portal** — Sitio web de empleo. Clave de SITE_CONFIG en 

**Oferta** — Publicacion individual. Se identifica por URL, registrada en SQLite.

**Estrategia de postulacion** —  (misma pagina),  (popup),  (redirige). 

**KEYWORD_GROUPS** — Lista de pares (keyword, modo) para busqueda atomica. 

**profile_mode** — it o bodega: selecciona cover_letter y respuestas del profile_kb.json. 

**profile_kb.json** — Base de conocimiento con respuestas por modo. 

**Busqueda Atomica** — Cada keyword se busca de forma independiente con --multi-keyword. 

**Sesion persistente** — Playwright guarda cookies en sessions/<portal>/. El bot recuerda logins.

**Rate Limiter** — Limita postulaciones por hora por portal. 

**Deduplicacion** — Si la URL ya esta en SQLite como applied, se omite. 

**Stealth** — User-agent aleatorio, viewport variable, delays con jitter. 

**Session Status** — Punto verde/gris en dashboard: portal tiene sesion guardada o no. 

**Master Mode** — Corre todos los portales seleccionados secuencialmente. 

**ATS** — Applicant Tracking System. Sistema de empresas para recibir postulaciones (Greenhouse, Lever, Workday).

**dry_run** — Navega y llena formularios sin hacer click en Enviar. 
