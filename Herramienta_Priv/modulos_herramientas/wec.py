"""
Módulo WEC (Website Evidence Collector) — R10, R17, R18.

Lanza el colector de evidencias WEC sobre la URL dada y ejecuta los scripts
de análisis de persistencia de cookies (R10) y seguridad de comunicaciones
(R17 redirección HTTPS, R18 Content Security Policy).
"""

import logging
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlparse

from ._loader import ejecutar_analisis, TFG_DIR

log = logging.getLogger(__name__)

WEC_DIR  = TFG_DIR / "WEC/website-evidence-collector"
WEC_BIN  = WEC_DIR / "build/bin/website-evidence-collector.js"

WEC_TIMEOUT = 120  # segundos


def ejecutar(url: str, output_dir: Path, resultados: dict, lock: threading.Lock) -> None:
    """
    Ejecuta WEC para el sitio dado y evalúa R10, R17 y R18.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Lanzar WEC
    log.info("Lanzando WEC para %s → %s", url, output_dir)
    try:
        proc = subprocess.run(
            ["node", str(WEC_BIN), "collect", url, "--output", str(output_dir)],
            capture_output=True,
            text=True,
            timeout=WEC_TIMEOUT,
        )
        if proc.returncode != 0:
            log.warning("WEC terminó con código %d: %s", proc.returncode, proc.stderr[-200:])
    except subprocess.TimeoutExpired:
        log.warning("WEC superó timeout (%ds)", WEC_TIMEOUT)
    except Exception as e:
        log.error("WEC no se pudo lanzar: %s", e)
        with lock:
            for r in ["R10", "R17", "R18"]:
                resultados[r] = {"veredicto": "ERROR", "detalle": str(e)}
        return

    # Verificar que se generó requests.har
    har_path = output_dir / "requests.har"
    if not har_path.exists():
        log.error("WEC no generó requests.har en %s", output_dir)
        with lock:
            for r in ["R10", "R17", "R18"]:
                resultados[r] = {"veredicto": "ERROR", "detalle": "requests.har no generado"}
        return

    # 2. Analizar R17 y R18 (a partir del HAR)
    try:
        data_17_18 = ejecutar_analisis("r17_r18_seguridad", str(output_dir))
        with lock:
            resultados["R17"] = {
                "veredicto": data_17_18.get("r17", {}).get("veredicto", "ERROR"),
                "detalle":   data_17_18.get("r17", {}),
            }
            resultados["R18"] = {
                "veredicto": data_17_18.get("r18", {}).get("veredicto", "ERROR"),
                "detalle":   data_17_18.get("r18", {}),
            }
    except Exception as e:
        log.error("r17_r18_seguridad falló: %s", e)
        with lock:
            for r in ["R17", "R18"]:
                resultados[r] = {"veredicto": "ERROR", "detalle": str(e)}

    # 3. Analizar R10 (cookies.yml del output de WEC)
    cookies_yml = output_dir / "cookies.yml"
    if not cookies_yml.exists():
        log.warning("WEC no generó cookies.yml — R10 no evaluable")
        with lock:
            resultados["R10"] = {"veredicto": "NO_EVALUABLE", "detalle": "cookies.yml no encontrado"}
        return

    try:
        data_10 = ejecutar_analisis("r10_persistencia", str(output_dir))
        with lock:
            resultados["R10"] = {
                "veredicto": data_10.get("veredicto", "ERROR"),
                "detalle":   data_10,
            }
    except Exception as e:
        log.error("r10_persistencia falló: %s", e)
        with lock:
            resultados["R10"] = {"veredicto": "ERROR", "detalle": str(e)}
