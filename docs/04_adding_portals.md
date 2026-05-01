# Cómo agregar un portal nuevo

El motor es genérico: cualquier portal se puede agregar en 5 minutos con selectores CSS.
Para comportamientos muy específicos (como LinkedIn), se puede crear una clase dedicada.

---

## Opción A: Portal genérico (5 minutos)

Ideal para la mayoría de los portales con flujo estándar.

### Paso 1: Identificar los selectores

Abrí el portal en tu browser y usá DevTools (F12 → Inspector).

Necesitás encontrar:

| Qué buscar | Dónde mirarlo |
|---|---|
| Contenedor de cada oferta | Click derecho en una card → Inspect |
| Botón de postulación | Click derecho en el botón → Inspect |
| Botón de siguiente página | Click derecho en "Siguiente" → Inspect |
| Título del puesto | Click derecho en el `<h1>` del título → Inspect |

**Consejo:** en la consola de DevTools, podés probar selectores con:
```javascript
document.querySelectorAll("tu.selector.aqui")
// Debe retornar los elementos que querés
```

### Paso 2: Agregar la entrada en config.py

```python
# En bot/config.py → SITE_CONFIG:
"mi_portal": {
    # Obligatorios
    "url_busqueda":           "https://miportal.com/jobs?q=python&remote=true",
    "selector_oferta":        "article.job-card",
    "selector_boton_aplicar": "button.btn-apply",
    "tipo_postulacion":       "directa",

    # Opcionales pero recomendados
    "selector_siguiente_pagina": "a[aria-label='Siguiente página']",
    "selector_titulo_oferta":    "h1.job-title",
    "max_offers_per_run":        15,
    "requires_login":            False,
},
```

### Paso 3: Probar con dry-run

```bash
python main.py --portal mi_portal --dry-run
```

Revisá el log y los screenshots en `errors/` para ver si los selectores funcionan.

### Paso 4: Validar y correr

```bash
python main.py --validate --portal mi_portal
python main.py --portal mi_portal --max 5
```

---

## Opción B: Portal con clase específica (para flujos complejos)

Usá esta opción cuando el portal tiene una UI muy particular, como LinkedIn.

### Paso 1: Crear el archivo del portal

```python
# bot/portals/mi_portal.py

import logging
from playwright.sync_api import Page
from .base import BasePortal
from ..stealth_utils import human_delay, human_click
from ..form_filler import fill_form

log = logging.getLogger("applyjob.mi_portal")


class MiPortalPortal(BasePortal):

    def get_offer_urls(self, page: Page) -> list[str]:
        """Extrae las URLs o IDs de las ofertas visibles."""
        urls = []
        elements = page.query_selector_all(self.config["selector_oferta"])
        for el in elements:
            try:
                href = el.get_attribute("href") or el.query_selector("a").get_attribute("href")
                if href and href not in urls:
                    urls.append(href)
            except Exception as exc:
                log.debug("Error leyendo href: %s", exc)
        return urls

    def apply_to_offer(self, page: Page, offer_url: str) -> tuple[str, str]:
        """
        Flujo completo de postulación.
        Retorna (status, title).
        """
        title = ""
        try:
            page.goto(offer_url, wait_until="domcontentloaded", timeout=30_000)
            human_delay(2.0, 4.0)

            # Extraer título
            try:
                title = page.text_content(self.config["selector_titulo_oferta"], timeout=3_000) or ""
                title = title.strip()[:80]
            except Exception:
                pass

            # Tu lógica específica aquí
            human_click(page, self.config["selector_boton_aplicar"])
            human_delay(2.0, 3.0)
            fill_form(page, self.profile)

            # Submit
            page.click("button[type='submit']")
            human_delay(2.0, 3.0)

            return "applied", title

        except Exception as exc:
            log.warning("[mi_portal] Error en %s: %s", offer_url, exc)
            return f"error: {exc}", title
```

### Paso 2: Registrar en el registry

```python
# bot/portals/__init__.py
from .linkedin import LinkedInPortal
from .mi_portal import MiPortalPortal

PORTAL_REGISTRY = {
    "linkedin":  LinkedInPortal,
    "mi_portal": MiPortalPortal,
}
```

### Paso 3: Agregar configuración

Igual que en la Opción A — agregar entrada en `SITE_CONFIG`.

---

## Referencia de selectores comunes

### LinkedIn (2024)
```python
"selector_oferta":        "li.jobs-search-results__list-item",
"selector_boton_aplicar": "button.jobs-apply-button--top-card",
```

### Indeed
```python
"selector_oferta":        "div.job_seen_beacon",
"selector_boton_aplicar": "button#indeedApplyButton",
```

### Computrabajo
```python
"selector_oferta":        "article.box_offer",
"selector_boton_aplicar": "a.btn_postular",
```

### GetOnBrd
```python
"selector_oferta":        "a.gb-results-list__item",
"selector_boton_aplicar": "a.btn-primary[href*='apply']",
```

---

## Troubleshooting de portales

Ver [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) → sección "El bot no encuentra ofertas".
