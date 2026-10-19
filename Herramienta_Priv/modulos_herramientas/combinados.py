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
import re
import threading
from pathlib import Path
from urllib.parse import urlparse

from ._loader import ejecutar_analisis, dir_analisis


def _captura_vacia(dominio: str) -> bool:
    """True si MySQL no tiene NINGUNA fila para el dominio: Privacy Pioneer no
    capturó tráfico (fallo de captura tras anti-bot, o sitio sin tráfico).
    Permite que R12 marque NO_EVALUABLE en vez de un falso PASSED, igual que ya
    hacen R2/R3/R9."""
    if not dominio:
        return False
    try:
        import os
        import mysql.connector
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "localhost"),
            user=os.getenv("MYSQL_USER", "pioneer"),
            password=os.getenv("MYSQL_PASSWORD", "abc"),
            database=os.getenv("MYSQL_DATABASE", "analysis"),
        )
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM entries WHERE rootUrl LIKE %s",
                    (f"%{dominio}%",))
        n = cur.fetchone()[0]
        conn.close()
        return int(n) == 0
    except Exception:
        return False     # ante la duda, no bloquear el veredicto

log = logging.getLogger(__name__)


def ejecutar(
    output_dir: Path,
    resultados: dict,
    lock: threading.Lock,
    ev_pp: threading.Event,
    ev_webxray: threading.Event,
    ev_poligraph: threading.Event,
    requisitos: set | None = None,
    url: str = "",
) -> None:
    """
    Evalúa R12, R15 y R16 esperando a las herramientas de las que dependen.

    Args:
        output_dir:   Directorio raíz del site audit.
        resultados:   Dict compartido entre hilos.
        lock:         Lock para escribir en resultados.
        ev_pp:        Evento que se activa cuando termina Privacy Pioneer.
        ev_webxray:   Evento que se activa cuando termina webXray.
        ev_poligraph: Evento que se activa cuando termina PoliGraph.
        requisitos:   Conjunto de requisitos seleccionados. None = todos.
    """
    sel     = set(requisitos) if requisitos else {"R12", "R15", "R16"}
    pg_dir  = output_dir / "poligraph"
    wx_dir  = output_dir / "webxray"
    # Dominio del sitio auditado, para filtrar la consulta de R12 a MySQL. Sin
    # este filtro, r12 consulta TODAS las filas de `entries` (que acumula datos
    # de todos los sitios auditados) y reporta violaciones de OTROS sitios como
    # si fueran del actual (contaminación cruzada).
    _netloc = urlparse(url).netloc if url else ""
    dominio = _netloc[4:] if _netloc.startswith("www.") else _netloc

    # Siempre esperamos los eventos para no bloquear indefinidamente el orden
    # de ejecución (los eventos se activan inmediatamente si la tool no corre).
    log.info("Combinados: esperando PoliGraph y webXray…")
    ev_poligraph.wait()
    ev_webxray.wait()

    graph_yml = pg_dir / "graph-original.full.yml"
    csv_3p    = wx_dir / "3p_domains.csv"

    # ── R16: PoliGraph + webXray ──────────────────────────────────────────────
    if "R16" in sel:
        try:
            if not graph_yml.exists():
                log.warning("graph-original.full.yml no disponible — R16 NO_EVALUABLE")
                with lock:
                    resultados["R16"] = {
                        "veredicto": "NO_EVALUABLE",
                        "detalle": "Grafo PoliGraph no disponible (html_crawler falló o fue bloqueado)",
                    }
            else:
                args_r16 = [str(graph_yml)]
                if csv_3p.exists():
                    args_r16.append(str(csv_3p))
                data = ejecutar_analisis("r16_correspondencia", *args_r16,
                                          salida_dir=dir_analisis(output_dir, es_raiz=True))
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
    log.info("Combinados: esperando Privacy Pioneer…")
    ev_pp.wait()

    if "R12" in sel:
        try:
            # El filtro de dominio es imprescindible: sin él r12 consulta todas
            # las filas de `entries` y reporta terceros de OTROS sitios.
            args_r12 = [str(csv_3p)] if csv_3p.exists() else []
            if dominio:
                args_r12.append(dominio)
            data = ejecutar_analisis("r12_software_terceros", *args_r12,
                                      salida_dir=dir_analisis(output_dir, es_raiz=True))
            veredicto = "PASSED"
            for sitio_data in (data.values() if isinstance(data, dict) else []):
                if isinstance(sitio_data, dict) and sitio_data.get("estado") == "FALLO":
                    veredicto = "FAILED"
                    break
            # Distinguir un PASSED real (sí hubo captura, sin rastreadores
            # comerciales en PRE) de una captura vacía (Privacy Pioneer no
            # registró NADA para el sitio: fallo de captura tras Cloudflare,
            # etc.). Sin esto, un sitio no capturado daría un falso "cumple".
            if veredicto == "PASSED" and _captura_vacia(dominio):
                veredicto = "NO_EVALUABLE"
                data = {"_captura_vacia": True, "detalle": (
                    "Privacy Pioneer no capturó tráfico para este sitio "
                    "(probable fallo de captura, p. ej. sitio tras anti-bot). "
                    "R12 no evaluable: no implica ausencia de rastreadores.")}
            with lock:
                resultados["R12"] = {"veredicto": veredicto, "detalle": data}
        except Exception as e:
            log.error("r12_software_terceros falló: %s", e)
            with lock:
                resultados["R12"] = {"veredicto": "ERROR", "detalle": str(e)}

    # ── R15: Privacy Pioneer + PoliGraph ─────────────────────────────────────
    if "R15" in sel:
        try:
            args_r15 = [str(graph_yml)] if graph_yml.exists() else []
            # Clave del sitio para filtrar el reporte GLOBAL de cookies (que
            # acumula todos los sitios): misma transformación que usa el crawler
            # para las claves de reporte_auditoria.json (no-alfanuméricos → '_').
            # Sin esto, la evidencia de R15 mostraba los 100+ sitios auditados.
            if dominio:
                args_r15.append(re.sub(r"[^a-zA-Z0-9]", "_", dominio))
            data = ejecutar_analisis("r15_responsables", *args_r15,
                                      salida_dir=dir_analisis(output_dir, es_raiz=True))
            veredicto_global = "PASSED"
            for sitio_data in data.values() if isinstance(data, dict) else []:
                if isinstance(sitio_data, dict):
                    for val in sitio_data.values():
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
