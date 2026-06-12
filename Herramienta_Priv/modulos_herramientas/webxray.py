"""
Módulo webXray — recopilación de terceros.

webXray no produce requisitos propios; su output (3p_domains.csv) lo consumen
R12 y R16 en el módulo combinados.py. Este módulo solo realiza el crawl y
extrae el informe de dominios de terceros, copiando 3p_domains.csv al
output_dir para que combinados.py lo encuentre.

El crawl se lanza en el entorno virtual de webXray (venv_tfg).
"""

import logging
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Derivado relativamente: webxray.py → modulos_herramientas/ → Herramienta_Priv/ → TFG_DIR
TFG_DIR      = Path(__file__).resolve().parents[2]
WEBXRAY_DIR  = TFG_DIR / "webXray"
WEBXRAY_PY   = WEBXRAY_DIR / "venv_tfg/bin/python"
RUN_SCRIPT   = WEBXRAY_DIR / "webxray_headless.py"  # runner headless propio
PAGE_LISTS   = WEBXRAY_DIR / "page_lists"
REPORTS_DIR  = WEBXRAY_DIR / "reports"

WEBXRAY_TIMEOUT = 300


def _dominio_seguro(url: str) -> str:
    """Devuelve el dominio en formato válido para nombre de BD."""
    host = urlparse(url).netloc.lstrip("www.")
    return re.sub(r"[^a-z0-9_]", "_", host)[:30]


def recopilar(url: str, output_dir: Path) -> None:
    """
    Ejecuta el crawl de webXray y copia 3p_domains.csv al output_dir.

    Si webXray falla o no produce el CSV, output_dir queda vacío y
    combinados.py manejará el error cuando lo necesite.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    db_name = f"audit_{_dominio_seguro(url)}"

    # 1. Escribir lista de páginas temporal
    PAGE_LISTS.mkdir(exist_ok=True)
    page_file = PAGE_LISTS / f"{db_name}.txt"
    page_file.write_text(f"{url}\n", encoding="utf-8")

    log.info("Lanzando webXray para %s (BD: %s)", url, db_name)
    python = str(WEBXRAY_PY) if WEBXRAY_PY.exists() else "python3"

    try:
        # Colecta (headless, sin UI)
        subprocess.run(
            [python, str(RUN_SCRIPT), db_name, str(page_file)],
            cwd=str(WEBXRAY_DIR),
            capture_output=True,
            text=True,
            timeout=WEBXRAY_TIMEOUT,
        )
        # Análisis (genera informes en reports/{db_name}/)
        subprocess.run(
            [python, str(RUN_SCRIPT), "--analyze", db_name],
            cwd=str(WEBXRAY_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        log.warning("webXray superó timeout")
    except Exception as e:
        log.error("webXray no se pudo ejecutar: %s", e)
    finally:
        page_file.unlink(missing_ok=True)

    # 2. Copiar 3p_domains.csv al output_dir
    csv_origen = REPORTS_DIR / db_name / "3p_domains.csv"
    if csv_origen.exists():
        shutil.copy2(csv_origen, output_dir / "3p_domains.csv")
        log.info("3p_domains.csv copiado a %s", output_dir)
    else:
        log.warning("webXray no generó 3p_domains.csv (BD: %s)", db_name)
