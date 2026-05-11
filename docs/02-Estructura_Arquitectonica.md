# 2.1 Patrón Arquitectónico

El sistema utiliza un patrón de **Estrategia (Strategy)** combinado con una arquitectura de **Capas (Layered Architecture)**.

- **Definición**: El patrón Estrategia permite definir una familia de algoritmos (handlers de portales), encapsular cada uno y hacerlos intercambiables. Esto permite que el motor central (`engine.py`) sea agnóstico a si está navegando en LinkedIn o en Indeed.
- **Evidencia**:
  - `bot/portals/base.py`: Clase base que define el contrato (`get_offer_urls`, `apply_to_offer`).
  - `bot/portals/linkedin.py`, `bot/portals/indeed.py`: Implementaciones concretas.
- **Desviaciones**: 
  - 🟡 Se detecta un acoplamiento ligero en `engine.py` con lógica específica para LinkedIn/Indeed en el manejo de sesiones, lo que rompe parcialmente la abstracción pura.

---

## 2.2 Mapa de Capas/Carpetas

| Capa / Carpeta | Responsabilidad | Regla de Dependencia |
| :--- | :--- | :--- |
| **Raíz (/)** | Orquestación y Punto de Entrada | Puede importar de todas las capas inferiores. |
| **bot/engine** | Motor de navegación y flujo de control | Importa de `portals` y `utils`. No debe importar del servidor Flask. |
| **bot/portals** | Adaptadores específicos para cada sitio web | Solo deben importar de `base.py` y utilidades comunes. |
| **bot/utils** | Funciones de soporte (stealth, filling, stats) | No deben tener dependencias circulares. |
| **templates** | Interfaz de usuario (Frontend) | Se comunica vía REST API con `gui_server.py`. |

---

## 2.3 Flujo de Datos End-to-End

```mermaid
sequenceDiagram
    participant U as Usuario (Browser)
    participant S as gui_server.py (Flask)
    participant M as main.py (Subprocess)
    participant E as bot/engine.py
    participant H as bot/portals (Handler)
    participant P as Playwright (Chromium)

    U->>S: POST /start_bot {portals: ["indeed"]}
    S->>M: spawn process: main.py --portal indeed
    M->>E: run_bot(portal_name="indeed")
    E->>H: init IndeedPortal()
    E->>P: launch_browser()
    loop Por cada oferta
        E->>H: get_offer_urls(page)
        H-->>E: list[ids]
        E->>H: apply_to_offer(page, id)
        H->>P: fill_form / click_apply
        P-->>H: success/fail
        H-->>E: status, title
        E->>M: print log to stdout
        M-->>S: capture line from pipe
        S-->>U: GET /logs (poll)
    end
```

---

## 2.4 Diagrama de Dependencias entre Capas

```mermaid
graph TD
    A[gui_server.py] -->|Ejecuta| B[main.py]
    B -->|Invoca| C[bot/engine.py]
    C -->|Instancia| D[bot/portals/*.py]
    D -->|Hereda de| E[bot/portals/base.py]
    C -->|Usa| F[bot/stealth_utils.py]
    C -->|Usa| G[bot/form_filler.py]
    C -->|Persiste en| H[bot/state.py]
    D -->|Usa| F
```

> [!NOTE]
> No se detectan dependencias circulares críticas entre los módulos core de Python.
