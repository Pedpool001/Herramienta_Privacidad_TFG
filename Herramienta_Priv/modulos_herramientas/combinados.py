"""
Módulo combinados — R12, R15, R16.

Estos tres requisitos necesitan datos de varias herramientas:
  R12 → Privacy Pioneer (MySQL) + webXray (3p_domains.csv)
  R15 → Privacy Pioneer (MySQL + reporte_auditoria.json) + PoliGraph (graph .yml)
  R16 → PoliGraph (graph .yml) + webXray (3p_domains.csv)

El módulo espera los eventos de sincronización que señalizan el fin de cada
herramienta y procesa cada requisito tan pronto como sus dependencias estén listas.
Orden de espera optimizado para minimizar tiempo de bloqueo:
  1. ev_poligraph + ev_webxray → R16
  2. ev_pp (webXray ya listo)  → R12
  3. (ambos ya listos)         → R15
"""

import logging
import threading
from pathlib import Path

from ._loader import ejecutar_analisis

log = logging.getLogger(__name__)


def ejecutar(
    output_dir: Path,
    resultados: dict,
    lock: threading.Lock,
    ev_pp: threading.Event,
    ev_webxray: threading.Event,
    ev_poligraph: threading.Event,
) -> None:
    """
    Evalúa R12, R15 y R16 esperando a las herramientas de las que dependen.

    Args:
        output_dir:   Directorio raíz del site audit (contiene subdirectorios
                      'poligraph' y 'webxray' con los outputs de cada tool).
        resultados:   Dict compartido entre hilos.
        lock:         Lock para escribir en resultados.
        ev_pp:        Evento que se activa cuando termina Privacy Pioneer.
        ev_webxray:   Evento que se activa cuando termina webXray.
        ev_poligraph: Evento que se activa cuando termina PoliGraph.
    """
    pg_dir  = output_dir / "poligraph"
    wx_dir  = output_dir / "webxray"

    # ── R16: PoliGraph + webXray ──────────────────────────────────────────────
    log.info("Combinados: esperando PoliGraph y webXray para R16…")
    ev_poligraph.wait()
    ev_webxray.wait()

    try:
        graph_yml = pg_dir / "graph-original.full.yml"
        csv_3p    = wx_dir / "3p_domains.csv"

        if not graph_yml.exists():
            raise FileNotFoundError(f"graph-original.full.yml no encontrado en {pg_dir}")

        args_r16 = [str(graph_yml)]
        if csv_3p.exists():
            args_r16.append(str(csv_3p))

        data = ejecutar_analisis("r16_correspondencia", *args_r16)
        with lock:
            resultados["R16"] = {
                "veredicto": data.get("veredicto", "ERROR"),
                "detalle":   data,
            }
    except Exception as e:
        log.error("r16_correspondencia falló: %s", e)
        with lock:
            resultados["R16"] = {"veredicto": "ERROR", "detalle": str(e)}

    # ── R12: Privacy Pioneer + webXray ────────────────────────────────────────
    log.info("Combinados: esperando Privacy Pioneer para R12…")
    ev_pp.wait()

    try:
        args_r12 = []
        if csv_3p.exists():
            args_r12 = [str(csv_3p)]

        data = ejecutar_analisis("r12_software_terceros", *args_r12)

        # data es {url_sitio: {estado: "FALLO"|"PASS", ...}} — sin wrapper "sitios"
        veredicto = "PASSED"
        for sitio_data in (data.values() if isinstance(data, dict) else []):
            if isinstance(sitio_data, dict):
                if sitio_data.get("estado") == "FALLO":
                    veredicto = "FAILED"
                    break

        with lock:
            resultados["R12"] = {"veredicto": veredicto, "detalle": data}
    except Exception as e:
        log.error("r12_software_terceros falló: %s", e)
        with lock:
            resultados["R12"] = {"veredicto": "ERROR", "detalle": str(e)}

    # ── R15: Privacy Pioneer + PoliGraph ─────────────────────────────────────
    # Ambos eventos ya activados; ejecutar directamente.
    try:
        graph_yml = pg_dir / "graph-original.full.yml"
        args_r15  = [str(graph_yml)] if graph_yml.exists() else []

        data = ejecutar_analisis("r15_responsables", *args_r15)

        # Resultado: dict anidado por sitio/fase
        # El veredicto global es el peor entre todas las fases de todos los sitios
        veredicto_global = "PASSED"
        for sitio_data in data.values() if isinstance(data, dict) else []:
            if isinstance(sitio_data, dict):
                for clave, val in sitio_data.items():
                    if isinstance(val, dict):
                        est = val.get("estado", "")
                        if est == "FAIL":
                            veredicto_global = "FAILED"
                        elif est in ("ADVERTENCIA", "WARNING") and veredicto_global == "PASSED":
                            veredicto_global = "WARNING"

        with lock:
            resultados["R15"] = {"veredicto": veredicto_global, "detalle": data}
    except Exception as e:
        log.error("r15_responsables falló: %s", e)
        with lock:
            resultados["R15"] = {"veredicto": "ERROR", "detalle": str(e)}
