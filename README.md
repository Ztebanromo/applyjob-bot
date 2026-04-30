# 🚀 ApplyJob-Bot: La Revolución de las Postulaciones Automáticas

[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-Dashboard-009688.svg)](https://fastapi.tiangolo.com/)
[![Stealth](https://img.shields.io/badge/Stealth-Anti--Detection-red.svg)](https://github.com/berstend/puppeteer-extra/tree/master/packages/playwright-stealth)

**ApplyJob-Bot** es una solución de ingeniería avanzada diseñada para automatizar la búsqueda y postulación a empleos en portales líderes como **LinkedIn, Indeed, Computrabajo y GetOnBrd**. Blindado con técnicas de sigilo y potenciado con una interfaz de usuario moderna.

---

## ✨ Características Premium

### 🖥️ Dashboard de Monitoreo (Glassmorphism)
Una interfaz web local construida con **FastAPI** que permite:
*   Visualizar métricas de éxito y errores en tiempo real con gráficos dinámicos (`Chart.js`).
*   Configurar tu perfil personal y parámetros de búsqueda sin tocar el código.
*   **Importación Inteligente de CV**: Sube tu archivo PDF directamente desde el navegador.

### 🧠 Cerebro de IA para Formularios
Olvídate de las preguntas de "screening". El bot utiliza una lógica de concordancia semántica para responder preguntas complejas como:
*   *"¿Por qué eres el candidato ideal?"*
*   *"Háblanos de tu experiencia en X tecnología."*
*   *"Pretensiones salariales."*

### 📱 Notificaciones Push al Móvil
Recibe un resumen detallado de cada sesión de postulación directamente en tu teléfono a través de **ntfy.sh**, **Discord** o **Email**. ¡Mantente informado mientras haces otras cosas!

### 🛡️ Blindaje Anti-Detección (Stealth)
Implementa técnicas avanzadas de Fase 8 para evitar bloqueos:
*   **Ruido en Canvas y WebGL Fingerprinting**.
*   Simulación de comportamiento humano (velocidad de escritura variable, movimientos de mouse erráticos).
*   Rotación de User-Agents y spoofing de permisos del navegador.

---

## 🚀 Guía de Inicio Rápido

1. **Clonar el repositorio**:
   ```bash
   git clone https://github.com/Ztebanromo/applyjob-bot.git
   cd applyjob-bot
   ```

2. **Instalar dependencias**:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```

3. **Configurar el entorno**:
   Crea un archivo `.env` basado en el `.env.example` y añade tus credenciales.

4. **Lanzar el Dashboard**:
   ```bash
   python dashboard/app.py
   ```
   Accede a `http://localhost:8000` para configurar tu perfil.

5. **Ejecutar el Bot**:
   ```bash
   python main.py --portal linkedin
   ```

---

## 📅 Roadmap de Desarrollo
- [x] **Fase 5**: Sistema de Notificaciones Multi-canal.
- [x] **Fase 8**: Hardening y Anti-detección avanzado.
- [x] **Fase 10**: IA para respuesta de preguntas complejas.
- [ ] **Fase 6**: Scheduler integrado en el Dashboard (En progreso).
- [ ] **Fase 7**: Soporte para Proxies rotativos.

---

## ⚖️ Licencia y Uso
Este bot ha sido creado con fines educativos y de eficiencia personal. Asegúrate de cumplir con los términos de servicio de los portales de empleo.

Hecho con ❤️ por [Ztebanromo](https://github.com/Ztebanromo)
