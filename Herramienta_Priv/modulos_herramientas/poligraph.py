"""
Módulo PoliGraph — R1, R5, R14, R19.

Ejecuta el pipeline de cuatro pasos de PoliGraph-er sobre la URL de la política
de privacidad dada (entorno conda 'poligraph') y evalúa los requisitos de
análisis de texto de la política.

Pipeline:
  1. html_crawler   — descarga y limpia el HTML de la política
  2. init_document  — tokeniza y aplica NLP (con traducción automática si no es inglés)
  3. run_annotators — anota entidades y relaciones
  4. build_graph    — construye el grafo de privacidad

Los ficheros de salida (graph-original.full.yml, readability.json,
accessibility_tree.json) quedan en output_dir y los scripts de análisis
los leen desde ahí.
"""

import logging
import subprocess
import threading
from pathlib import Path

from ._loader import ejecutar_analisis, TFG_DIR

log = logging.getLogger(__name__)

POLIGRAPH_DIR = TFG_DIR / "PoliGraph"

PIPELINE_TIMEOUT = 600  # 10 min: NLP pesado


def _conda_run(*cmd, cwd=None, timeout=None) -> subprocess.CompletedProcess:
    """Ejecuta un comando dentro del entorno conda 'poligraph'."""
    full = ["conda", "run", "--no-capture-output", "-n", "poligraph"] + list(cmd)
    return subprocess.run(
        full, cwd=str(cwd or POLIGRAPH_DIR),
        capture_output=True, text=True, timeout=timeout,
    )


def ejecutar(url_politica: str, output_dir: Path,
             resultados: dict, lock: threading.Lock) -> None:
    """
    Ejecuta el pipeline de PoliGraph y evalúa R1, R5, R14, R19.

    Args:
        url_politica: URL de la política de privacidad (salida de buscador_politica).
        output_dir:   Directorio de salida para los ficheros de PoliGraph.
        resultados:   Dict compartido entre hilos.
        lock:         Lock para escribir en resultados.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    requisitos_poligraph = ["R1", "R5", "R14", "R19"]

    # ── Paso 1: html_crawler ──────────────────────────────────────────────────
    log.info("PoliGraph html_crawler para %s", url_politica)
    try:
        r = _conda_run(
            "python", "-m", "poligrapher.scripts.html_crawler",
            url_politica, str(output_dir),
            timeout=120,
        )
        if r.returncode != 0:
            raise RuntimeError(f"html_crawler: {r.stderr[-800:]}")
    except Exception as e:
        log.error("PoliGraph html_crawler falló: %s", e)
        with lock:
            for req in requisitos_poligraph:
                resultados[req] = {"veredicto": "ERROR", "detalle": str(e)}
        return

    # ── Paso 2: init_document ─────────────────────────────────────────────────
    log.info("PoliGraph init_document…")
    try:
        r = _conda_run(
            "python", "-m", "poligrapher.scripts.init_document", str(output_dir),
            timeout=PIPELINE_TIMEOUT,
        )
        if r.returncode != 0:
            raise RuntimeError(f"init_document: {r.stderr[-300:]}")
    except Exception as e:
        log.error("PoliGraph init_document falló: %s", e)
        with lock:
            for req in requisitos_poligraph:
                resultados[req] = {"veredicto": "ERROR", "detalle": str(e)}
        return

    # ── Paso 3: run_annotators ────────────────────────────────────────────────
    log.info("PoliGraph run_annotators…")
    try:
        r = _conda_run(
            "python", "-m", "poligrapher.scripts.run_annotators", str(output_dir),
            timeout=PIPELINE_TIMEOUT,
        )
        if r.returncode != 0:
            raise RuntimeError(f"run_annotators: {r.stderr[-300:]}")
    except Exception as e:
        log.error("PoliGraph run_annotators falló: %s", e)
        with lock:
            for req in requisitos_poligraph:
                resultados[req] = {"veredicto": "ERROR", "detalle": str(e)}
        return

    # ── Paso 4: build_graph ───────────────────────────────────────────────────
    log.info("PoliGraph build_graph…")
    try:
        r = _conda_run(
            "python", "-m", "poligrapher.scripts.build_graph", str(output_dir),
            timeout=120,
        )
        if r.returncode != 0:
            raise RuntimeError(f"build_graph: {r.stderr[-300:]}")
    except Exception as e:
        log.error("PoliGraph build_graph falló: %s", e)
        with lock:
            for req in requisitos_poligraph:
                resultados[req] = {"veredicto": "ERROR", "detalle": str(e)}
        return

    log.info("Pipeline PoliGraph completado en %s", output_dir)

    # ── Análisis de requisitos ────────────────────────────────────────────────
    # R1: árbol de accesibilidad (accessibility_tree.json)
    for req, script in [
        ("R1",  "r1_capas"),
        ("R5",  "r5_revocabilidad"),
        ("R14", "r14_lenguaje"),
        ("R19", "r19_dpo"),
    ]:
        try:
            data = ejecutar_analisis(script, str(output_dir))
            with lock:
                resultados[req] = {
                    "veredicto": data.get("veredicto", "ERROR"),
                    "detalle":   data,
                }
        except Exception as e:
            log.error("%s falló: %s", script, e)
            with lock:
                resultados[req] = {"veredicto": "ERROR", "detalle": str(e)}
