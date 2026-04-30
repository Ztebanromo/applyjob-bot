import os
import sys
from pathlib import Path
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import shutil

# Añadir el root del proyecto al path para importar bot.state
sys.path.append(str(Path(__file__).parent.parent))
from bot.state import get_stats, get_recent

app = FastAPI(title="ApplyJob Dashboard")

# Rutas de archivos estáticos y templates
BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/errors", StaticFiles(directory=str(BASE_DIR.parent / "errors")), name="errors")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    try:
        stats = get_stats()
        recent = get_recent(limit=50)
        
        # Formatear datos para el gráfico de torta
        total_applied = 0
        total_errors  = 0
        total_skipped = 0
        
        for portal_stats in stats["by_portal"].values():
            total_applied += portal_stats.get("applied", 0)
            total_errors  += portal_stats.get("error", 0)
            for status, count in portal_stats.items():
                if "skip" in status.lower():
                    total_skipped += count
        
        chart_data = {
            "labels": ["Aplicados", "Errores", "Saltados"],
            "data_values": [total_applied, total_errors, total_skipped]
        }

        # Importar config dinámicamente
        import importlib
        import bot.config as bot_cfg
        importlib.reload(bot_cfg)
        
        user_profile = bot_cfg.USER_PROFILE
        search_config = bot_cfg.SEARCH_CONFIG

        import json
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "stats": stats,
                "recent": recent,
                "chart_json": json.dumps(chart_data),
                "profile": user_profile,
                "search": search_config
            }
        )
    except Exception as e:
        import traceback
        print(f"ERROR EN DASHBOARD: {e}")
        traceback.print_exc()
        return HTMLResponse(content=f"<h1>Error 500</h1><pre>{e}</pre>", status_code=500)

@app.post("/api/config")
async def save_config(request: Request):
    data = await request.json()
    from bot.config import CONFIG_FILE, DATA_DIR
    DATA_DIR.mkdir(exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        import json
        json.dump(data, f, indent=4, ensure_ascii=False)
    
    # Recargar módulo de configuración
    import importlib
    import bot.config
    importlib.reload(bot.config)
    
    return {"status": "ok"}

@app.get("/api/stats")
async def api_stats():
    return get_stats()

@app.get("/api/recent")
async def api_recent(limit: int = 50):
    return get_recent(limit)

@app.post("/api/upload-cv")
async def upload_cv(file: UploadFile = File(...)):
    try:
        data_dir = Path(__file__).parent.parent / "data"
        data_dir.mkdir(exist_ok=True)
        file_path = data_dir / "resume.pdf"
        
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        # Actualizar config para usar este archivo
        config_path = data_dir / "user_config.json"
        import json
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        else:
            config = {"profile": {}, "search": {}}
            
        config["profile"]["cv_path"] = str(file_path.absolute())
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
            
        return {"status": "success", "path": str(file_path)}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
