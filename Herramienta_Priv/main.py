"""
Herramienta de auditoría de privacidad web.

Modo sitio único:
    python3 main.py https://www.ejemplo.com
    python3 main.py https://www.ejemplo.com --salida informe.html

Modo batch:
    python3 main.py --batch sitios.txt
    python3 main.py --batch sitios.txt --salida resultados/

Flujo:
  1. Lanza en paralelo los hilos de cada herramienta externa.
  2. El hilo de PoliGraph busca primero la URL de la política de privacidad
     (buscador_politica.py) y luego ejecuta el pipeline de PoliGraph.
  3. Los requisitos que combinan varias herramientas (R12, R15, R16) esperan
     a que terminen sus dependencias mediante threading.Event.
  4. Cuando todos los hilos terminan, genera el informe HTML.
"""

import argparse
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# ── Importación de módulos de herramienta ────────────────────────────────────
# Cada módulo expone una función ejecutar(url, output_dir, resultados, lock)
# que lanza la herramienta, ejecuta los scripts de análisis y escribe los
# resultados en el dict compartido.
from modulos_herramientas import (
    privacy_pioneer,
    wec,
    blacklight,
    openwpm,
    webxray,
    poligraph,
    playwright_mod,
    combinados,
)

from buscador_politica import buscar_url_politica, _sondear_subdominios
from modulos_herramientas.poligraph import WrongPolicyPageError

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Mapeo herramienta → requisitos que cubre (incluyendo los que combinados lee)
# ─────────────────────────────────────────────────────────────────────────────
_TODOS_REQS = {f"R{i}" for i in range(1, 20)}

_REQS_PP         = {"R2", "R3", "R9", "R12", "R15"}
_REQS_WEC        = {"R10", "R17", "R18"}
_REQS_BLACKLIGHT = {"R6"}
_REQS_OPENWPM    = {"R7", "R8", "R11"}
_REQS_POLIGRAPH  = {"R1", "R5", "R14", "R15", "R16", "R19"}
_REQS_PLAYWRIGHT = {"R4", "R13", "R1", "R5"}   # R1/R5 precisan el árbol AX
_REQS_WEBXRAY    = {"R12", "R16"}
_REQS_COMBINADOS = {"R12", "R15", "R16"}


# ─────────────────────────────────────────────────────────────────────────────
# Hilos por herramienta
# ─────────────────────────────────────────────────────────────────────────────

def _hilo_privacy_pioneer(url: str, output_dir: Path,
                           resultados: dict, lock: threading.Lock,
                           evento: threading.Event,
                           requisitos: set | None = None) -> None:
    """Ejecuta Privacy Pioneer y analiza R2, R3, R9."""
    try:
        privacy_pioneer.ejecutar(url, output_dir / "privacy_pioneer", resultados, lock,
                                  requisitos=requisitos)
    except Exception as e:
        log.error("Privacy Pioneer falló: %s", e)
        _marcar_error(resultados, lock, ["R2", "R3", "R9"], str(e))
    finally:
        evento.set()


def _hilo_wec(url: str, output_dir: Path,
               resultados: dict, lock: threading.Lock,
               evento: threading.Event,
               requisitos: set | None = None) -> None:
    """Ejecuta WEC y analiza R10, R17, R18."""
    try:
        wec.ejecutar(url, output_dir / "wec", resultados, lock, requisitos=requisitos)
    except Exception as e:
        log.error("WEC falló: %s", e)
        _marcar_error(resultados, lock, ["R10", "R17", "R18"], str(e))
    finally:
        evento.set()


def _hilo_blacklight(url: str, output_dir: Path,
                      resultados: dict, lock: threading.Lock,
                      evento: threading.Event,
                      requisitos: set | None = None) -> None:
    """Ejecuta Blacklight y analiza R6."""
    try:
        blacklight.ejecutar(url, output_dir / "blacklight", resultados, lock,
                             requisitos=requisitos)
    except Exception as e:
        log.error("Blacklight falló: %s", e)
        _marcar_error(resultados, lock, ["R6"], str(e))
    finally:
        evento.set()


def _hilo_openwpm(url: str, output_dir: Path,
                   resultados: dict, lock: threading.Lock,
                   evento: threading.Event,
                   requisitos: set | None = None) -> None:
    """Ejecuta OpenWPM y analiza R7, R8, R11."""
    try:
        openwpm.ejecutar(url, output_dir / "openwpm", resultados, lock,
                          requisitos=requisitos)
    except Exception as e:
        log.error("OpenWPM falló: %s", e)
        _marcar_error(resultados, lock, ["R7", "R8", "R11"], str(e))
    finally:
        evento.set()


def _hilo_webxray(url: str, output_dir: Path,
                   resultados: dict, lock: threading.Lock,
                   evento: threading.Event,
                   requisitos: set | None = None) -> None:
    """
    Ejecuta el crawl de webXray. No produce requisitos propios; su output
    (3p_domains.csv) lo consumen R12 y R16 en el módulo combinados.
    Se mantiene como hilo paralelo para que el crawl solape con el resto.
    """
    try:
        webxray.recopilar(url, output_dir / "webxray")
    except Exception as e:
        log.error("webXray falló: %s", e)
    finally:
        evento.set()


def _hilo_poligraph(url_sitio: str, output_dir: Path,
                     resultados: dict, lock: threading.Lock,
                     evento: threading.Event,
                     requisitos: set | None = None) -> None:
    """
    Busca la URL de la política de privacidad y ejecuta el pipeline de
    PoliGraph. Analiza R1, R5, R14, R19 según los requisitos seleccionados.
    El pipeline siempre corre para producir graph.yml (usado por combinados).
    """
    log.info("Buscando URL de política de privacidad para %s", url_sitio)
    info_politica = buscar_url_politica(url_sitio)
    url_politica = info_politica.get("url")

    if not url_politica:
        log.warning("No se encontró política de privacidad. "
                    "Requisitos de PoliGraph marcados como NO_EVALUABLE.")
        _marcar_no_evaluable(resultados, lock,
                             ["R1", "R5", "R14", "R15", "R16", "R19"],
                             "Política de privacidad no localizable automáticamente")
        evento.set()
        return

    log.info("Política encontrada: %s (fuente: %s, inglés: %s)",
             url_politica, info_politica.get("fuente"), info_politica.get("es_ingles"))

    try:
        poligraph.ejecutar(url_politica, output_dir / "poligraph", resultados, lock,
                           requisitos=requisitos)
    except WrongPolicyPageError:
        log.warning("URL '%s' rechazada por PoliGraph. Probando subdominios...", url_politica)
        sondeo = _sondear_subdominios(url_sitio)
        url_fallback = sondeo.get("url") if sondeo else None
        if url_fallback and url_fallback != url_politica:
            log.info("Reintentando PoliGraph con URL de subdominio: %s", url_fallback)
            try:
                poligraph.ejecutar(url_fallback, output_dir / "poligraph", resultados, lock,
                                   requisitos=requisitos)
            except Exception as e2:
                log.error("PoliGraph también falló con URL fallback: %s", e2)
                _marcar_error(resultados, lock, ["R1", "R5", "R14", "R19"], str(e2))
        else:
            _marcar_no_evaluable(resultados, lock, ["R1", "R5", "R14", "R19"],
                                 "URL de política rechazada por PoliGraph; sin URL alternativa")
    except Exception as e:
        log.error("PoliGraph falló: %s", e)
        _marcar_error(resultados, lock, ["R1", "R5", "R14", "R19"], str(e))
    finally:
        evento.set()


def _hilo_playwright(url: str, output_dir: Path,
                      resultados: dict, lock: threading.Lock,
                      evento: threading.Event,
                      requisitos: set | None = None) -> None:
    """Ejecuta los análisis Playwright para R4 y R13 (y captura árbol AX para R1/R5)."""
    try:
        playwright_mod.ejecutar(url, output_dir / "playwright", resultados, lock,
                                 requisitos=requisitos)
    except Exception as e:
        log.error("Playwright falló: %s", e)
        _marcar_error(resultados, lock, ["R4", "R13"], str(e))
    finally:
        evento.set()


def _hilo_combinados(url: str, output_dir: Path,
                      resultados: dict, lock: threading.Lock,
                      ev_pp: threading.Event,
                      ev_webxray: threading.Event,
                      ev_poligraph: threading.Event,
                      requisitos: set | None = None) -> None:
    """
    Delega en combinados.py los requisitos que cruzan varias herramientas.
    Espera los eventos en el orden más eficiente para minimizar tiempo de espera:

      R16 → ev_poligraph + ev_webxray
      R12 → ev_pp        + ev_webxray   (webXray ya listo tras R16)
      R15 → ev_pp        + ev_poligraph (ambos ya listos)
    """
    try:
        combinados.ejecutar(
            output_dir,
            resultados,
            lock,
            ev_pp=ev_pp,
            ev_webxray=ev_webxray,
            ev_poligraph=ev_poligraph,
            requisitos=requisitos,
        )
    except Exception as e:
        log.error("Combinados falló: %s", e)
        _marcar_error(resultados, lock, ["R12", "R15", "R16"], str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades de resultado
# ─────────────────────────────────────────────────────────────────────────────

def _marcar_error(resultados: dict, lock: threading.Lock,
                   requisitos: list, detalle: str) -> None:
    with lock:
        for r in requisitos:
            resultados[r] = {"veredicto": "ERROR", "detalle": detalle}


def _marcar_no_evaluable(resultados: dict, lock: threading.Lock,
                          requisitos: list, motivo: str) -> None:
    with lock:
        for r in requisitos:
            resultados[r] = {"veredicto": "NO_EVALUABLE", "detalle": motivo}


# ─────────────────────────────────────────────────────────────────────────────
# Punto de entrada principal
# ─────────────────────────────────────────────────────────────────────────────

def auditar(url_sitio: str, ruta_salida: str | None = None,
            parent_dir: Path | None = None,
            requisitos: set | None = None) -> dict:
    """
    Ejecuta la auditoría de un sitio web.

    Args:
        url_sitio:   URL del sitio a auditar.
        ruta_salida: Ruta del informe HTML. Si es None, se genera en output_dir.
        parent_dir:  Si se proporciona, output_dir se crea dentro de él
                     (usado en modo batch).
        requisitos:  Conjunto de requisitos a evaluar, ej. {"R1","R6","R13"}.
                     None = todos los requisitos R1-R19.

    Returns:
        Diccionario con los resultados de todos los requisitos R1-R19.
    """
    # ── Preparar directorio de trabajo ───────────────────────────────────────
    dominio = urlparse(url_sitio).netloc.lstrip("www.")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = Path(parent_dir) if parent_dir is not None else Path(__file__).parent / "output"
    output_dir = base / f"{dominio}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    log.info("Directorio de trabajo: %s", output_dir)

    # ── Determinar requisitos seleccionados ───────────────────────────────────
    sel = set(requisitos) if requisitos else _TODOS_REQS
    no_sel = _TODOS_REQS - sel

    # ── Estado compartido entre hilos ─────────────────────────────────────────
    resultados: dict = {}
    lock = threading.Lock()

    # Marcar de entrada los no solicitados
    if no_sel:
        with lock:
            for r in sorted(no_sel):
                resultados[r] = {
                    "veredicto": "NO_SOLICITADO",
                    "detalle": "No seleccionado para esta auditoría",
                }

    # Eventos de sincronización (se activan cuando termina cada herramienta)
    ev_pp        = threading.Event()
    ev_wec       = threading.Event()
    ev_blacklight= threading.Event()
    ev_openwpm   = threading.Event()
    ev_webxray   = threading.Event()
    ev_poligraph = threading.Event()
    ev_playwright= threading.Event()

    # Decidir qué herramientas deben correr
    run_pp         = bool(sel & _REQS_PP)
    run_wec        = bool(sel & _REQS_WEC)
    run_blacklight = bool(sel & _REQS_BLACKLIGHT)
    run_openwpm    = bool(sel & _REQS_OPENWPM)
    run_poligraph  = bool(sel & _REQS_POLIGRAPH)
    run_playwright = bool(sel & _REQS_PLAYWRIGHT)
    run_webxray    = bool(sel & _REQS_WEBXRAY)
    run_combinados = bool(sel & _REQS_COMBINADOS)

    # Activar eventos de herramientas que no van a correr para no bloquear
    # el hilo de combinados si este sí debe ejecutarse.
    if not run_pp:        ev_pp.set()
    if not run_webxray:   ev_webxray.set()
    if not run_poligraph: ev_poligraph.set()

    # ── Construir y lanzar hilos según herramientas necesarias ────────────────
    hilos = []

    if run_pp:
        hilos.append(threading.Thread(
            name="PrivacyPioneer",
            target=_hilo_privacy_pioneer,
            args=(url_sitio, output_dir, resultados, lock, ev_pp, sel),
        ))
    if run_wec:
        hilos.append(threading.Thread(
            name="WEC",
            target=_hilo_wec,
            args=(url_sitio, output_dir, resultados, lock, ev_wec, sel),
        ))
    if run_blacklight:
        hilos.append(threading.Thread(
            name="Blacklight",
            target=_hilo_blacklight,
            args=(url_sitio, output_dir, resultados, lock, ev_blacklight, sel),
        ))
    if run_openwpm:
        hilos.append(threading.Thread(
            name="OpenWPM",
            target=_hilo_openwpm,
            args=(url_sitio, output_dir, resultados, lock, ev_openwpm, sel),
        ))
    if run_webxray:
        hilos.append(threading.Thread(
            name="webXray",
            target=_hilo_webxray,
            args=(url_sitio, output_dir, resultados, lock, ev_webxray, sel),
        ))
    if run_poligraph:
        hilos.append(threading.Thread(
            name="PoliGraph",
            target=_hilo_poligraph,
            args=(url_sitio, output_dir, resultados, lock, ev_poligraph, sel),
        ))
    if run_playwright:
        hilos.append(threading.Thread(
            name="Playwright",
            target=_hilo_playwright,
            args=(url_sitio, output_dir, resultados, lock, ev_playwright, sel),
        ))
    if run_combinados:
        hilos.append(threading.Thread(
            name="Combinados",
            target=_hilo_combinados,
            args=(url_sitio, output_dir, resultados, lock,
                  ev_pp, ev_webxray, ev_poligraph),
            kwargs={"requisitos": sel & _REQS_COMBINADOS},
        ))

    log.info("Lanzando %d hilos de análisis para %s "
             "(requisitos seleccionados: %s)",
             len(hilos), url_sitio,
             ", ".join(sorted(sel)) if sel != _TODOS_REQS else "todos")
    for h in hilos:
        h.start()

    for h in hilos:
        h.join()

    log.info("Todos los hilos han terminado.")

    # ── Generar informe HTML ──────────────────────────────────────────────────
    if ruta_salida is None:
        ruta_salida = str(output_dir / "informe.html")

    try:
        from salida import generar_informe_unico
        generar_informe_unico(url_sitio, resultados, ruta_salida)
        log.info("Informe generado en: %s", ruta_salida)
    except Exception as e:
        log.error("Error al generar el informe HTML: %s", e)

    return resultados


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Auditoría de privacidad web."
    )
    parser.add_argument(
        "url", nargs="?",
        help="URL del sitio a auditar (ej: https://www.marca.com). "
             "Obligatorio si no se usa --batch.",
    )
    parser.add_argument(
        "--batch", metavar="FILE",
        help="Fichero .txt con una URL por línea. Ejecuta auditorías secuenciales.",
    )
    parser.add_argument(
        "--salida", metavar="PATH",
        help="Ruta del informe HTML (modo único) o directorio de salida (modo batch).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(threadName)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.batch:
        # ── Modo batch ────────────────────────────────────────────────────────
        import batch as _batch
        _batch.auditar_batch(args.batch, args.salida)

    elif args.url:
        # ── Modo sitio único ──────────────────────────────────────────────────
        resultados = auditar(args.url, args.salida)

        # Resumen por consola
        print("\n── Resumen ──────────────────────────────────")
        for req in sorted(resultados):
            v = resultados[req].get("veredicto", "?")
            print(f"  {req:4s}  {v}")
        print("─────────────────────────────────────────────\n")

    else:
        parser.error("Debes proporcionar una URL o usar --batch FICHERO.")
