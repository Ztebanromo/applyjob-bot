"""
Clase base abstracta para portales específicos.
Cada portal hereda de aquí e implementa apply_to_offer().
"""
from abc import ABC, abstractmethod
from playwright.sync_api import Page


class BasePortal(ABC):

    def __init__(self, config: dict, profile: dict):
        self.config  = config
        self.profile = profile

    @abstractmethod
    def apply_to_offer(self, page: Page, offer_url: str) -> str:
        """
        Ejecuta el flujo de postulación para una oferta.
        Retorna un string de status: 'applied' | 'skipped_*' | 'error: ...'
        """

    def get_offer_urls(self, page: Page) -> list[str]:
        """
        Extrae URLs de ofertas de la página actual.
        Puede ser sobreescrito por cada portal para lógica específica.
        """
        urls = []
        elements = page.query_selector_all(self.config["selector_oferta"])
        for el in elements:
            try:
                href = el.get_attribute("href")
                if not href:
                    a = el.query_selector("a[href]")
                    href = a.get_attribute("href") if a else None
                if href:
                    if not href.startswith("http"):
                        base = page.url.split("/")[0] + "//" + page.url.split("/")[2]
                        href = base + href
                    if href not in urls:
                        urls.append(href)
            except Exception:
                continue
        return urls
