# 4.1 Archivos de Configuración

| Archivo | Propósito | Claves Críticas | Impacto si falta |
| :--- | :--- | :--- | :--- |
| **.env** | Configuración de usuario y credenciales | `USER_PHONE`, `USER_CV_PATH`, `USER_MAX_OFFERS` | El bot no puede completar datos de contacto ni adjuntar el CV. |
| **requirements.txt** | Dependencias de Python | `playwright`, `flask`, `pypdf` | No se pueden instalar las bibliotecas necesarias. |
| **bot/config.py** | Selectores de portales y perfiles | `SITE_CONFIG`, `USER_PROFILE` | El bot no sabría dónde hacer click en cada portal. |

---

## 4.2 Dependencias Externas (pip)

| Paquete | Versión | Propósito | Uso en el Proyecto |
| :--- | :--- | :--- | :--- |
| **playwright** | 1.44.0 | Automatización de navegador | Motor de navegación en `engine.py`. |
| **playwright-stealth** | 1.0.6 | Evasión de detección | Hooks de sigilo en `stealth_utils.py`. |
| **flask** | *N/A* | Servidor Web | Dashboard en `gui_server.py`. |
| **pypdf** | 4.2.0 | Procesamiento de PDF | Lectura de CV para autocompletar perfil. |

---

## 4.3 Requisitos de Infraestructura

- **Base de Datos**: SQLite 3 (integrado). Archivo: `data/applications.db`.
- **Navegador**: Google Chrome o Chromium instalado (vía `playwright install chromium`).
- **Variables de Entorno**:
  - `USER_NAME`, `USER_EMAIL`, `USER_PHONE`: Datos de contacto básicos.
  - `USER_CV_PATH`: Ruta absoluta al archivo PDF del currículum.

---

## 4.4 Setup Local (Paso a Paso)

1. **Clonar repositorio** y navegar a la raíz.
2. **Crear entorno virtual**: `python -m venv .venv`.
3. **Activar entorno**: `.venv\Scripts\activate` (Windows).
4. **Instalar dependencias**: `pip install -r requirements.txt`.
5. **Instalar Browser**: `playwright install chromium`.
6. **Configurar .env**: Copiar `.env.example` a `.env` y rellenar datos.
7. **Lanzar Dashboard**: `python gui_server.py`.
