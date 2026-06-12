"""
Módulo Blacklight — R6.

Lanza el colector Blacklight (Node.js) sobre la URL dada y ejecuta el script
de análisis de keylogging/monitorización encubierta (R6).
"""

import logging
import subprocess
import threading
from pathlib import Path

from ._loader import ejecutar_analisis, TFG_DIR

log = logging.getLogger(__name__)

BL_DIR    = TFG_DIR / "BL/blacklight-collector"
BL_RUNNER = BL_DIR / "blacklight_runner.js"

BL_TIMEOUT = 120


def ejecutar(url: str, output_dir: Path, resultados: dict, lock: threading.Lock,
             requisitos: set | None = None) -> None:
    """
    Ejecuta Blacklight para el sitio dado y evalúa R6.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    sel = set(requisitos) if requisitos else {"R6"}

    # 1. Lanzar Blacklight
    log.info("Lanzando Blacklight para %s → %s", url, output_dir)
    try:
        proc = subprocess.run(
            ["node", str(BL_RUNNER), url, str(output_dir)],
            cwd=str(BL_DIR),
            capture_output=True,
            text=True,
            timeout=BL_TIMEOUT,
        )
        if proc.returncode != 0:
            log.warning("Blacklight código %d: %s", proc.returncode, proc.stderr[-200:])
    except subprocess.TimeoutExpired:
        log.warning("Blacklight superó timeout (%ds)", BL_TIMEOUT)
    except Exception as e:
        log.error("Blacklight no se pudo lanzar: %s", e)
        with lock:
            resultados["R6"] = {"veredicto": "ERROR", "detalle": str(e)}
        return

    # 2. Verificar inspection.json
    inspection = output_dir / "inspection.json"
    if not inspection.exists():
        log.error("Blacklight no generó inspection.json en %s", output_dir)
        with lock:
            resultados["R6"] = {"veredicto": "ERROR", "detalle": "inspection.json no generado"}
        return

    # 3. Analizar R6
    if "R6" in sel:
        try:
            data = ejecutar_analisis("r6_keylogging", str(inspection))
            with lock:
                resultados["R6"] = {
                    "veredicto": data.get("veredicto", "ERROR"),
                    "detalle":   data,
                }
        except Exception as e:
            log.error("r6_keylogging falló: %s", e)
            with lock:
                resultados["R6"] = {"veredicto": "ERROR", "detalle": str(e)}
