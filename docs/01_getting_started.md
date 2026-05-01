# Guía de inicio rápido

Todo lo que necesitás para tener el bot corriendo en menos de 10 minutos.

---

## Requisitos previos

- Python 3.10 o superior
- Git
- Conexión a internet

Verificá tu versión de Python:
```bash
python --version
```

---

## Instalación paso a paso

### 1. Clonar el repositorio

```bash
git clone https://github.com/Ztebanromo/applyjob-bot.git
cd applyjob-bot
```

### 2. Crear el entorno virtual

```bash
python -m venv .venv
```

Activar:

```bash
# Windows (CMD / PowerShell)
.venv\Scripts\activate

# Windows (Git Bash)
source .venv/Scripts/activate

# Linux / Mac
source .venv/bin/activate
```

Sabrás que está activo cuando veas `(.venv)` al inicio de la línea de tu terminal.

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Instalar el browser (Chromium)

```bash
playwright install chromium
```

Descarga ~136 MB. Solo se hace una vez.

---

## Configuración mínima

Abrí `bot/config.py` y completá la sección `USER_PROFILE`:

```python
USER_PROFILE = {
    "full_name":  "Tu Nombre Completo",      # ← completar
    "email":      "tuemail@gmail.com",        # ← completar
    "phone":      "+54 9 11 1234 5678",       # ← completar
    "city":       "Buenos Aires, Argentina",  # ← completar
    "linkedin":   "https://linkedin.com/in/tu-perfil",
    "cv_path":    "C:/Users/TuUsuario/Documents/CV.pdf",
    # ... resto de campos
}
```

Los tres campos **obligatorios** son `full_name`, `email` y `phone`.
Sin ellos el bot no arranca (lo valida al inicio).

---

## Primera ejecución

### Validar la configuración

```bash
python main.py --validate --portal computrabajo
```

Si todo está bien, verás:
```
✓ Configuración válida — puedes correr el bot.
```

### Dry-run (sin postular)

Ideal para verificar que los selectores funcionan antes de postular:

```bash
python main.py --portal computrabajo --dry-run
```

El bot abre el browser, navega, detecta las ofertas y las registra como `dry_run` sin postular.

### Primera postulación real

```bash
python main.py --portal computrabajo
```

---

## Ver resultados

```bash
python main.py --stats
```

Los logs también están en:
- `logs/applied_YYYY-MM-DD.csv` — registro del día
- `logs/applyjob.log` — log completo con rotación diaria

---

## Próximos pasos

- [Configuración avanzada](02_configuration.md)
- [Uso de LinkedIn](03_linkedin.md)
- [Agregar un portal nuevo](04_adding_portals.md)
