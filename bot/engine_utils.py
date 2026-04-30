"""
Utilidades y estrategias genéricas para el motor de postulación.
"""
import logging
from playwright.sync_api import Page
from .stealth_utils import human_delay, human_scroll, take_error_screenshot
from .form_filler import fill_form

log = logging.getLogger("applyjob.engine_utils")

def apply_directa(page: Page, config: dict, profile: dict) -> str:
    btn_sel = config["selector_boton_aplicar"]
    try:
        from .stealth_utils import human_click
        human_click(page, btn_sel)
        human_delay(2.0, 4.0)
        fill_form(page, profile)
        for submit_sel in [
            "button[type='submit']", "input[type='submit']",
            "button:has-text('Enviar')", "button:has-text('Submit')",
            "button:has-text('Apply')", "button:has-text('Postular')",
        ]:
            try:
                if page.query_selector(submit_sel):
                    human_click(page, submit_sel)
                    human_delay(2.0, 3.0)
                    return "applied"
            except Exception:
                continue
        return "filled_no_submit"
    except Exception as e:
        return f"error: {e}"

def apply_modal(page: Page, config: dict, profile: dict) -> str:
    from .stealth_utils import human_click
    btn_sel = config["selector_boton_aplicar"]
    try:
        human_click(page, btn_sel)
        human_delay(2.0, 4.0)
        fill_form(page, profile)
        for _ in range(5):
            for next_sel in [
                "button:has-text('Next')", "button:has-text('Siguiente')",
                "button:has-text('Continue')", "button:has-text('Continuar')",
                "button:has-text('Submit')", "button:has-text('Enviar')",
                "button:has-text('Apply')", "button:has-text('Postular')",
                "button[aria-label='Submit application']",
            ]:
                try:
                    btn = page.query_selector(next_sel)
                    if btn and btn.is_visible():
                        btn.click()
                        human_delay(1.5, 3.0)
                        fill_form(page, profile)
                        break
                except Exception:
                    continue
        return "applied"
    except Exception as e:
        return f"error: {e}"

def apply_externa(page: Page, config: dict) -> str:
    from .stealth_utils import human_click
    btn_sel = config["selector_boton_aplicar"]
    try:
        with page.context.expect_page() as new_page_info:
            human_click(page, btn_sel)
        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded")
        external_url = new_page.url
        new_page.close()
        return f"external: {external_url}"
    except Exception as e:
        return f"error_externa: {e}"

def process_offer_generic(
    page: Page, offer_url: str, config: dict, profile: dict
) -> tuple[str, str]:
    """Retorna (title, status)."""
    title = "unknown"
    try:
        page.goto(offer_url, wait_until="domcontentloaded", timeout=30_000)
        human_delay(2.0, 4.0)
        human_scroll(page, steps=2)

        title_sel = config.get("selector_titulo_oferta")
        if title_sel:
            try:
                title = (page.text_content(title_sel, timeout=3_000) or "").strip()[:80]
            except Exception:
                pass

        tipo = config.get("tipo_postulacion", "directa")
        if tipo == "directa":
            status = apply_directa(page, config, profile)
        elif tipo == "modal":
            status = apply_modal(page, config, profile)
        elif tipo == "externa":
            status = apply_externa(page, config)
        else:
            status = f"unknown_type:{tipo}"

        return title, status

    except Exception as e:
        take_error_screenshot(page, "generic", "offer_error")
        return title, f"error: {e}"
