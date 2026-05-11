import os
import re
from pypdf import PdfReader
from bot.config import USER_PROFILE

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extrae todo el texto de un archivo PDF."""
    if not os.path.exists(pdf_path):
        return ""
    try:
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() + "\n"
        return text
    except Exception as e:
        print(f"Error leyendo PDF: {e}")
        return ""

def parse_cv_data(pdf_path: str) -> dict:
    """
    Intenta extraer datos básicos del CV para sugerir campos del perfil.
    Heurísticas simples: regex para email, teléfono, y búsqueda de palabras clave.
    """
    text = extract_text_from_pdf(pdf_path)
    if not text:
        return {}

    data = {}
    
    # Email
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    if email_match:
        data['email'] = email_match.group(0)

    # Teléfono (Heurística simple para Chile +56 9 ...)
    phone_match = re.search(r'(\+?56\s?9\s?\d{4}\s?\d{4})', text)
    if phone_match:
        data['phone'] = phone_match.group(0).replace(" ", "")
        # Extraer solo el número local para phone_number
        local_match = re.search(r'9\s?\d{4}\s?\d{4}', phone_match.group(0))
        if local_match:
            data['phone_number'] = local_match.group(0).replace(" ", "")

    # Años de experiencia (Busca números cerca de "años", "experiencia", etc.)
    exp_match = re.search(r'(\d+)\s*años?\s*de\s*experiencia', text, re.IGNORECASE)
    if exp_match:
        data['years_exp'] = exp_match.group(1)
    else:
        # Si menciona "estudiante", "egresado" o "junior" y no hay años, sugerimos 0
        if re.search(r'estudiante|egresado|practicante|junior', text, re.IGNORECASE):
            data['years_exp'] = "0"

    # LinkedIn
    linkedin_match = re.search(r'linkedin\.com/in/[\w-]+', text)
    if linkedin_match:
        data['linkedin'] = "https://www." + linkedin_match.group(0)

    return data

def run_setup_wizard():
    """Interfaz CLI para configurar el bot por primera vez."""
    print("\n" + "="*50)
    print("      SETUP WIZARD — APPLYJOB BOT (MASTER MODE)")
    print("="*50 + "\n")

    env_path = ".env"
    current_env = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    current_env[k] = v

    # 1. Ruta del CV
    cv_path = input(f"Ruta de tu CV (PDF) [{current_env.get('USER_CV_PATH', 'No definida')}]: ").strip()
    if not cv_path: cv_path = current_env.get('USER_CV_PATH', "")
    
    suggested_data = {}
    if cv_path and os.path.exists(cv_path):
        print(f"\nExtrayendo información de: {cv_path}...")
        suggested_data = parse_cv_data(cv_path)
        if suggested_data:
            print("Información sugerida encontrada en el CV.")
    
    # 2. Recopilar datos (usando sugerencias del CV si existen)
    fields = [
        ("USER_FULL_NAME", "Nombre Completo", suggested_data.get('email', current_env.get('USER_FULL_NAME', ''))), # No name extraction yet, use email as fallback for prompt
        ("USER_EMAIL", "Email", suggested_data.get('email', current_env.get('USER_EMAIL', ''))),
        ("USER_PHONE", "Teléfono", suggested_data.get('phone', current_env.get('USER_PHONE', ''))),
        ("USER_LINKEDIN", "LinkedIn URL", suggested_data.get('linkedin', current_env.get('USER_LINKEDIN', ''))),
        ("USER_YEARS_EXP", "Años de Experiencia", suggested_data.get('years_exp', current_env.get('USER_YEARS_EXP', '0'))),
        ("USER_SALARY", "Pretensión Salarial (ej: 850.000)", current_env.get('USER_SALARY', '850.000')),
        ("LABORUM_PASSWORD", "Password Laborum (para auto-login)", current_env.get('LABORUM_PASSWORD', '')),
    ]

    new_env = current_env.copy()
    new_env['USER_CV_PATH'] = cv_path

    for key, label, default in fields:
        val = input(f"{label} [{default}]: ").strip()
        new_env[key] = val if val else default

    # 3. Guardar en .env
    with open(env_path, "w", encoding="utf-8") as f:
        for k, v in new_env.items():
            f.write(f"{k}={v}\n")

    print(f"\n✓ Configuración guardada en {env_path}")
    print("Ahora puedes correr el bot con: python main.py --portal linkedin\n")
