"""
Script de prueba para verificar el nivel de invisibilidad del bot.
Navega a sannysoft.com para chequear huellas digitales.
"""
from playwright.sync_api import sync_playwright
from bot.stealth_utils import apply_stealth, random_user_agent, random_viewport
import time

def run_invisibility_test():
    print("Iniciando test de invisibilidad...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=random_user_agent(),
            viewport=random_viewport(),
            locale="es-AR",
        )
        page = context.new_page()
        
        # Aplicar nuestras herramientas de sigilo
        apply_stealth(page)
        
        try:
            from playwright_stealth import stealth_sync
            stealth_sync(page)
            print("Playwright-stealth aplicado.")
        except ImportError:
            print("Usando solo stealth manual.")

        print("Navegando a sannysoft.com/pcp...")
        page.goto("https://bot.sannysoft.com/", wait_until="networkidle")
        
        print("Esperando 5 segundos para que carguen los tests...")
        time.sleep(5)
        
        screenshot_path = "errors/stealth_test.png"
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"Test completado. Captura guardada en: {screenshot_path}")
        
        browser.close()

if __name__ == "__main__":
    run_invisibility_test()
