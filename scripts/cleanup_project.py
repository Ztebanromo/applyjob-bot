"""
Script de Limpieza y Optimización del Proyecto (ApplyJob Bot).
Permite liberar espacio borrando logs raíz, pycaches, capturas viejas y logs antiguos.
"""
import os
import shutil
import time
from pathlib import Path

# Configuración de retención de archivos por defecto
RETENTION_DAYS_ERRORS = 3  # Conservar las capturas de error de los últimos 3 días
RETENTION_DAYS_LOGS = 7    # Conservar los logs históricos de logs/ de los últimos 7 días


def get_dir_size(path: Path) -> int:
    """Calcula el tamaño total de un directorio en bytes."""
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += get_dir_size(Path(entry.path))
    except Exception:
        pass
    return total


def format_size(size_bytes: int) -> str:
    """Formatea bytes a KB, MB o GB para legibilidad."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} TB"


def clean_errors_dir(root_dir: Path, delete_all: bool = False) -> tuple[int, int]:
    """
    Purga capturas de pantalla viejas (.png) de la carpeta errors/.
    Retorna (cantidad_eliminados, bytes_liberados).
    """
    errors_dir = root_dir / "errors"
    if not errors_dir.exists() or not errors_dir.is_dir():
        print("  Directorio 'errors/' no encontrado. Saltando.")
        return 0, 0

    now = time.time()
    count = 0
    bytes_freed = 0

    print(f"\n  [1/4] Analizando capturas de error en 'errors/'...")
    for file_path in errors_dir.glob("*.png"):
        try:
            mtime = file_path.stat().st_mtime
            age_days = (now - mtime) / (24 * 3600)
            
            # Borrar si se pide borrar todo, o si supera el límite de días
            if delete_all or age_days > RETENTION_DAYS_ERRORS:
                file_size = file_path.stat().st_size
                file_path.unlink()
                count += 1
                bytes_freed += file_size
        except Exception as e:
            print(f"    ⚠ Error al borrar {file_path.name}: {e}")

    print(f"    ✓ Se eliminaron {count} imágenes. Espacio liberado: {format_size(bytes_freed)}")
    return count, bytes_freed


def clean_root_logs(root_dir: Path) -> tuple[int, int]:
    """
    Elimina archivos de log obsoletos en el directorio raíz.
    Retorna (cantidad_eliminados, bytes_liberados).
    """
    root_logs = [
        "logsgui_server.log",
        "logsserver.log",
        "server.log",
        "server_err.log"
    ]
    count = 0
    bytes_freed = 0

    print(f"\n  [2/4] Buscando logs obsoletos en la raíz...")
    for filename in root_logs:
        file_path = root_dir / filename
        if file_path.exists() and file_path.is_file():
            try:
                file_size = file_path.stat().st_size
                file_path.unlink()
                count += 1
                bytes_freed += file_size
                print(f"    - Eliminado log raíz: {filename} ({format_size(file_size)})")
            except Exception as e:
                print(f"    ⚠ Error al eliminar log raíz {filename}: {e}")
                
    if count == 0:
        print("    ✓ No se encontraron logs obsoletos en la raíz.")
    else:
        print(f"    ✓ Se eliminaron {count} archivos de log raíz. Espacio liberado: {format_size(bytes_freed)}")
    return count, bytes_freed


def clean_logs_dir(root_dir: Path) -> tuple[int, int]:
    """
    Elimina logs rotados en logs/ antiguos.
    Retorna (cantidad_eliminados, bytes_liberados).
    """
    logs_dir = root_dir / "logs"
    if not logs_dir.exists() or not logs_dir.is_dir():
        return 0, 0

    now = time.time()
    count = 0
    bytes_freed = 0

    print(f"\n  [3/4] Analizando logs históricos en 'logs/' (Límite: {RETENTION_DAYS_LOGS} días)...")
    for file_path in logs_dir.glob("*.log.*"):
        try:
            mtime = file_path.stat().st_mtime
            age_days = (now - mtime) / (24 * 3600)
            
            if age_days > RETENTION_DAYS_LOGS:
                file_size = file_path.stat().st_size
                file_path.unlink()
                count += 1
                bytes_freed += file_size
                print(f"    - Eliminado log antiguo: {file_path.name} ({format_size(file_size)})")
        except Exception as e:
            print(f"    ⚠ Error al borrar {file_path.name}: {e}")

    # También limpiar logs vacíos o temporales de ejecuciones
    for file_path in logs_dir.glob("run_*.log*"):
        try:
            mtime = file_path.stat().st_mtime
            age_days = (now - mtime) / (24 * 3600)
            if age_days > RETENTION_DAYS_LOGS:
                file_size = file_path.stat().st_size
                file_path.unlink()
                count += 1
                bytes_freed += file_size
                print(f"    - Eliminado log de ejecución: {file_path.name} ({format_size(file_size)})")
        except Exception as e:
            print(f"    ⚠ Error al borrar {file_path.name}: {e}")

    print(f"    ✓ Logs antiguos depurados. Se eliminaron {count} archivos. Espacio liberado: {format_size(bytes_freed)}")
    return count, bytes_freed


def clean_caches(root_dir: Path) -> int:
    """
    Busca y elimina directorios __pycache__, .pytest_cache y .ruff_cache.
    Retorna la cantidad de directorios eliminados.
    """
    caches = ["__pycache__", ".pytest_cache", ".ruff_cache"]
    count = 0
    
    print(f"\n  [4/4] Buscando directorios de caché de desarrollo...")
    
    # Recorrer de forma recursiva
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=False):
        for dirname in list(dirnames):
            if dirname in caches:
                full_path = Path(dirpath) / dirname
                try:
                    # No borrar el caché que esté dentro del entorno virtual .venv
                    if ".venv" in full_path.parts or "env" in full_path.parts:
                        continue
                    
                    size = get_dir_size(full_path)
                    shutil.rmtree(full_path)
                    count += 1
                    print(f"    - Eliminado directorio de caché: {full_path.relative_to(root_dir)} ({format_size(size)})")
                except Exception as e:
                    print(f"    ⚠ Error al eliminar {full_path}: {e}")
                    
    print(f"    ✓ Se eliminaron {count} carpetas de caché.")
    return count


def run_cleanup(delete_all_errors: bool = False) -> None:
    """Ejecuta el ciclo de limpieza principal."""
    root_dir = Path(__file__).parent.resolve()
    print("\n" + "=" * 60)
    print("  MOTO DE LIMPIEZA & OPTIMIZACIÓN — MODO EXPERTO")
    print("=" * 60)
    print(f"  Directorio raíz: {root_dir}")
    
    start_time = time.time()
    
    # Ejecutar tareas
    err_count, err_bytes = clean_errors_dir(root_dir, delete_all=delete_all_errors)
    root_log_count, root_log_bytes = clean_root_logs(root_dir)
    log_count, log_bytes = clean_logs_dir(root_dir)
    cache_count = clean_caches(root_dir)
    
    total_bytes = err_bytes + root_log_bytes + log_bytes
    total_files = err_count + root_log_count + log_count
    elapsed = time.time() - start_time
    
    print("\n" + "=" * 60)
    print("  RESUMEN DE LIMPIEZA COMPLETA")
    print("=" * 60)
    print(f"  ✓ Archivos eliminados: {total_files}")
    print(f"  ✓ Carpetas de caché purgadas: {cache_count}")
    print(f"  ✓ Espacio total recuperado: {format_size(total_bytes)}")
    print(f"  ✓ Tiempo transcurrido: {elapsed:.2f} segundos")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    # Si se corre directamente, preguntar o borrar todo
    import argparse
    parser = argparse.ArgumentParser(description="Limpiador experto de ApplyJob Bot")
    parser.add_argument("--all-errors", action="store_true", help="Borrar todas las capturas sin importar la fecha")
    args = parser.parse_args()
    
    run_cleanup(delete_all_errors=args.all_errors)
