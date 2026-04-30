# 🌌 ApplyJob-Bot | The Ultimate Job Application Automator

<p align="center">
  <img src="https://img.shields.io/badge/Release-v1.0.0--stable-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Security-Stealth--Hardened-red?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Intelligence-AI--Powered-purple?style=for-the-badge" />
</p>

---

## 🎭 La Nueva Era de la Búsqueda de Empleo

**ApplyJob-Bot** no es un simple script; es un agente autónomo de ingeniería de software diseñado para dominar el mercado laboral. Mientras otros pasan horas haciendo clic, tu bot está analizando, decidiendo y postulando por ti con una precisión del 99%.

### 🏛️ Arquitectura de Producción
El sistema está construido bajo una arquitectura modular de **Portal Handlers**, permitiendo una escalabilidad infinita:
*   **Engine Core**: Gestiona el ciclo de vida del navegador y la persistencia de estados.
*   **Stealth Layer**: Inyección de scripts dinámicos para evadir los sistemas anti-bot más agresivos.
*   **Intelligence Layer**: Motor deductivo para el autocompletado de preguntas complejas basado en tu trayectoria.
*   **Dashboard Glassmorphism**: Centro de control visual de última generación.

---

## 🔥 Funcionalidades de Élite

### 💎 Dashboard Estético
Una interfaz de usuario futurista que te permite:
- **Monitorear en Vivo**: Mira cómo suben tus estadísticas de aplicación con gráficos en tiempo real.
- **Configuración Zero-Code**: Edita tu perfil, cambia de puesto o ubicación sin tocar una sola línea de código.
- **Importación Drop-Zone**: Arrastra tu CV y el bot lo tendrá listo para todas las plataformas.

### 🛡️ Sigilo Grado Militar
El bot utiliza técnicas de **Canvas Noise** y **WebGL Spoofing** para generar una identidad única en cada sesión, haciendo casi imposible que LinkedIn o Indeed detecten la automatización.

### 📱 Notificaciones Push
Recibe alertas instantáneas en tu móvil (vía ntfy.sh o Discord) cuando el bot termine una sesión. Incluye resumen de éxito, errores y enlaces directos a las ofertas procesadas.

---

## 🛠️ Instalación y Despegue

### Requisitos Previos
- Python 3.10 o superior.
- Git.

### Instalación Rápida
```bash
# Clonar el proyecto
git clone https://github.com/Ztebanromo/applyjob-bot.git

# Entrar al directorio
cd applyjob-bot

# Instalar el ecosistema
pip install -r requirements.txt
playwright install chromium
```

### Ejecución
1. **Lanza el Dashboard**: `python dashboard/app.py`
2. **Configura tus datos**: Ve a `http://localhost:8000`
3. **Inicia el Motor**: `python main.py --portal indeed`

---

## 🆘 Solución de Problemas (Troubleshooting)

| Problema | Causa Probable | Solución |
| :--- | :--- | :--- |
| **Error 404** | Cambio en la URL del portal | Actualiza el bot o ajusta el `SITE_CONFIG` en `config.py`. |
| **Página de Login** | Sesión expirada | Ejecuta sin modo `--headless` y logueate una vez; el bot recordará la sesión. |
| **CAPTCHA detectado** | Actividad muy alta | Reduce la velocidad o aumenta los delays en `stealth_utils.py`. |

---

## 📜 Licencia
Este proyecto es una herramienta de productividad. El autor no se hace responsable del uso indebido de la misma en plataformas externas.

---
<p align="center">
  <b>Desarrollado con precisión por <a href="https://github.com/Ztebanromo">Ztebanromo</a></b>
</p>
