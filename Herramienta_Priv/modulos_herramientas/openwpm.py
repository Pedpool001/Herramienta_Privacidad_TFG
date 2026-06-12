"""
Módulo OpenWPM — R7, R8, R11.

Lanza un crawl de OpenWPM sobre la URL dada (entorno conda 'openwpm') y ejecuta
los scripts de análisis de fingerprinting (R7), storage de terceros (R8) y
desvinculación/cookie-sync (R11).
"""

import logging
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

from ._loader import ejecutar_analisis, TFG_DIR

log = logging.getLogger(__name__)

OPENWPM_DIR = TFG_DIR / "openWPM/OpenWPM"

OPENWPM_TIMEOUT = 300  # segundos (5 min para crawl de dos páginas)

# Script plantilla para el crawl de OpenWPM (se escribe en disco y se ejecuta)
_CRAWL_TEMPLATE = """\
from pathlib import Path
from openwpm.command_sequence import CommandSequence
from openwpm.commands.browser_commands import GetCommand
from openwpm.config import BrowserParams, ManagerParams
from openwpm.storage.sql_provider import SQLiteStorageProvider
from openwpm.task_manager import TaskManager

site     = {site!r}
data_dir = Path({data_dir!r})
data_dir.mkdir(parents=True, exist_ok=True)

manager_params = ManagerParams(num_browsers=1)
browser_params = [BrowserParams(display_mode="headless")]
bp = browser_params[0]
bp.http_instrument        = True
bp.cookie_instrument      = True
bp.navigation_instrument  = True
bp.js_instrument          = True
bp.dns_instrument         = True
bp.save_http_responses    = True

manager_params.data_directory = data_dir
manager_params.log_path       = data_dir / "openwpm.log"

with TaskManager(
    manager_params,
    browser_params,
    SQLiteStorageProvider(data_dir / "crawl-data.sqlite"),
    None,
) as manager:
    seq = CommandSequence(site, site_rank=0)
    seq.append_command(GetCommand(url=site, sleep=15), timeout=90)
    manager.execute_command_sequence(seq)
"""


def _dominio(url: str) -> str:
    return urlparse(url).netloc.lstrip("www.")


def ejecutar(url: str, output_dir: Path, resultados: dict, lock: threading.Lock,
             requisitos: set | None = None) -> None:
    """
    Lanza un crawl de OpenWPM y evalúa R7, R8 y R11.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    sel = set(requisitos) if requisitos else {"R7", "R8", "R11"}
    db_path = output_dir / "crawl-data.sqlite"

    # 1. Escribir script de crawl temporal
    crawl_script = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", dir=str(OPENWPM_DIR), delete=False, encoding="utf-8"
    )
    crawl_script.write(
        _CRAWL_TEMPLATE.format(site=url, data_dir=str(output_dir))
    )
    crawl_script.close()
    script_path = Path(crawl_script.name)

    log.info("Lanzando OpenWPM para %s → %s", url, output_dir)
    try:
        proc = subprocess.run(
            ["conda", "run", "-n", "openwpm", "python", str(script_path)],
            cwd=str(OPENWPM_DIR),
            capture_output=True,
            text=True,
            timeout=OPENWPM_TIMEOUT,
        )
        if proc.returncode != 0:
            log.warning("OpenWPM código %d: %s", proc.returncode, proc.stderr[-300:])
    except subprocess.TimeoutExpired:
        log.warning("OpenWPM superó timeout (%ds)", OPENWPM_TIMEOUT)
    except Exception as e:
        log.error("OpenWPM no se pudo lanzar: %s", e)
        with lock:
            for r in ["R7", "R8", "R11"]:
                resultados[r] = {"veredicto": "ERROR", "detalle": str(e)}
        return
    finally:
        script_path.unlink(missing_ok=True)

    if not db_path.exists():
        log.error("OpenWPM no generó crawl-data.sqlite en %s", output_dir)
        with lock:
            for r in ["R7", "R8", "R11"]:
                resultados[r] = {"veredicto": "ERROR", "detalle": "crawl-data.sqlite no generado"}
        return

    # 2. Análisis de R7, R8, R11
    for req, script in [
        ("R7",  "r7_fingerprinting"),
        ("R8",  "r8_storage_terceros"),
        ("R11", "r11_desvinculacion"),
    ]:
        if req not in sel:
            continue
        try:
            data = ejecutar_analisis(script, str(db_path))
            with lock:
                resultados[req] = {
                    "veredicto": data.get("veredicto", "ERROR"),
                    "detalle":   data,
                }
        except Exception as e:
            log.error("%s falló: %s", script, e)
            with lock:
                resultados[req] = {"veredicto": "ERROR", "detalle": str(e)}
