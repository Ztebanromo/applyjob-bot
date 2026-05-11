# 3.1 Diccionario de Componentes (Core)

## bot/engine.py — `run_bot`
**Propósito**: Orquestador central del ciclo de vida de una sesión de postulación.
**Patrón aplicado**: Control Loop / Facade.

| Método | Parámetros | Retorno | Lógica resumida |
| :--- | :--- | :--- | :--- |
| `run_bot()` | `portal_name`, `dry_run`, `headless` | `None` | Inicializa Playwright, carga el handler, itera ofertas y registra resultados. |

---

## bot/portals/base.py — `BasePortal`
**Propósito**: Interfaz abstracta que define el contrato para todos los portales.
**Patrón aplicado**: Abstract Base Class (ABC).

| Método | Parámetros | Retorno | Lógica resumida |
| :--- | :--- | :--- | :--- |
| `get_offer_urls()` | `page: Page` | `list[str]` | Extrae identificadores de ofertas de la página de búsqueda. |
| `apply_to_offer()` | `page: Page, id: str` | `tuple[str, str]` | Ejecuta el flujo de postulación dentro de una oferta específica. |

---

## bot/form_filler.py — `fill_form`
**Propósito**: Motor inteligente de detección y llenado de campos de formulario.
**Patrón aplicado**: Data Mapper / Input Strategy.

| Método | Parámetros | Retorno | Lógica resumida |
| :--- | :--- | :--- | :--- |
| `fill_form()` | `page: Page, profile: dict` | `None` | Detecta inputs/selects visibles y los mapea con el perfil del usuario. |

---

## gui_server.py — `run_bot_thread`
**Propósito**: Gestiona la ejecución asíncrona del bot para no bloquear el dashboard.
**Patrón aplicado**: Worker Thread / Process Manager.

| Método | Parámetros | Retorno | Lógica resumida |
| :--- | :--- | :--- | :--- |
| `run_bot_thread()` | `portals: list` | `None` | Lanza subprocesos de `main.py` y redirige logs a una variable global. |

---

## 3.2 Casos Borde y Puntos Críticos

1.  **Manejo de CV**: Si `USER_CV_PATH` no es absoluto o no existe, el bot falla silenciosamente en el momento de la carga. (Detectado en `bot/form_filler.py`)
2.  **Selectores Obsoletos**: Si un portal cambia su DOM, el bot entra en `no_offers` o `panel_timeout`. (Detectado en `bot/engine.py`)
3.  **Cloudflare**: Indeed y LinkedIn pueden bloquear la navegación si detectan patrones de scraping no-humanos. (Gestionado en `bot/stealth_utils.py`)
