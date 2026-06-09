"""
Módulo Privacy Pioneer — R2, R3, R9.

Lanza el crawl de Privacy Pioneer (Selenium + extensión Firefox) y ejecuta
los scripts de análisis correspondientes.

Flujo:
  1. Limpia las entradas MySQL del sitio para evitar contaminación.
  2. Escribe un CSV temporal con la URL a auditar.
  3. Inicia la REST API (rest-api/index.js) que recibe eventos de la extensión.
  4. Lanza el crawler (selenium-crawler/local-crawler.js).
  5. Al finalizar, mata la REST API y ejecuta los scripts de análisis.
"""

import logging
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from ._loader import ejecutar_analisis, TFG_DIR, ANALYSIS_DATA

log = logging.getLogger(__name__)

# ── Rutas ─────────────────────────────────────────────────────────────────────
PP_DIR         = TFG_DIR / "privacy-pioneer-web-crawler"
CRAWLER_DIR    = PP_DIR / "selenium-crawler"
REST_API_DIR   = PP_DIR / "rest-api"

# Timeout del crawler en segundos (por defecto ~5 min por sitio)
CRAWLER_TIMEOUT = 360


def _dominio(url: str) -> str:
    return urlparse(url).netloc.lstrip("www.")


def _limpiar_mysql(dominio: str) -> None:
    """Borra las entradas de este sitio en MySQL para que el análisis sea limpio."""
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host="localhost", user="pioneer", password="abc", database="analysis"
        )
        cur = conn.cursor()
        cur.execute("DELETE FROM entries WHERE rootUrl LIKE %s", (f"%{dominio}%",))
        conn.commit()
        conn.close()
        log.info("MySQL limpiado para %s (%d filas)", dominio, cur.rowcount)
    except Exception as e:
        log.warning("No se pudo limpiar MySQL: %s", e)


def ejecutar(url: str, output_dir: Path, resultados: dict, lock: threading.Lock) -> None:
    """
    Lanza Privacy Pioneer para el sitio dado, luego evalúa R2, R3 y R9.

    Args:
        url:        URL del sitio a auditar.
        output_dir: Directorio donde guardar la salida de la herramienta.
        resultados: Dict compartido entre hilos.
        lock:       Lock para escribir en resultados.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    dominio = _dominio(url)

    # 1. Limpiar MySQL
    _limpiar_mysql(dominio)

    # 2. Escribir CSV temporal con la URL
    csv_sitio = CRAWLER_DIR / "prueba-tfg.csv"
    csv_bak   = CRAWLER_DIR / "prueba-tfg.csv.bak"
    try:
        if csv_sitio.exists():
            csv_sitio.rename(csv_bak)
        csv_sitio.write_text(f"url\n{url}\n", encoding="utf-8")
    except Exception as e:
        log.error("No se pudo escribir CSV del crawler: %s", e)
        with lock:
            for r in ["R2", "R3", "R9"]:
                resultados[r] = {"veredicto": "ERROR", "detalle": str(e)}
        return

    # 3. Iniciar REST API
    log.info("Iniciando REST API de Privacy Pioneer…")
    api_proc = subprocess.Popen(
        ["node", "index.js"],
        cwd=str(REST_API_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)  # dar tiempo a que arranque

    try:
        # 4. Lanzar crawler
        log.info("Lanzando crawler de Privacy Pioneer para %s…", url)
        crawler = subprocess.run(
            ["node", "local-crawler.js"],
            cwd=str(CRAWLER_DIR),
            capture_output=True,
            text=True,
            timeout=CRAWLER_TIMEOUT,
        )
        if crawler.returncode != 0:
            log.warning("Crawler terminó con código %d", crawler.returncode)
    except subprocess.TimeoutExpired:
        log.warning("Crawler superó timeout (%ds)", CRAWLER_TIMEOUT)
    finally:
        # 5. Matar REST API y restaurar CSV
        api_proc.terminate()
        try:
            api_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api_proc.kill()

        csv_sitio.unlink(missing_ok=True)
        if csv_bak.exists():
            csv_bak.rename(csv_sitio)

    # 6. Copiar reporte de cookies al output_dir
    reporte_src = ANALYSIS_DATA / "reporte_auditoria.json"
    if reporte_src.exists():
        import shutil
        shutil.copy2(reporte_src, output_dir / "reporte_auditoria.json")

    # 7. Ejecutar análisis
    for req, script, args in [
        (["R2", "R3"], "r2_r3_cookies_beacons", [dominio]),
        (["R9"],       "r9_minimizacion",        [dominio]),
    ]:
        try:
            data = ejecutar_analisis(script, *args)

            with lock:
                if script == "r2_r3_cookies_beacons":
                    # r2/r3: dict con clave "sitios" → {dominio: {veredicto, ...}}
                    sitios  = data.get("sitios", {})
                    entrada = next(
                        (v for k, v in sitios.items() if dominio in k or k in dominio),
                        next(iter(sitios.values()), None)
                    )
                    veredicto = entrada.get("veredicto", "ERROR") if entrada else "ERROR"
                    resultados["R2"] = {"veredicto": veredicto, "detalle": entrada or {}}
                    resultados["R3"] = {"veredicto": veredicto, "detalle": entrada or {}}
                else:
                    # r9: lista de dicts con {sitio, estado, ...}
                    _ESTADO_MAP = {"FALLO": "FAILED", "ADVERTENCIA": "WARNING",
                                   "OK": "PASSED", "SIN_DATOS": "NO_EVALUABLE"}
                    lista = data if isinstance(data, list) else []
                    item  = next((d for d in lista if dominio in d.get("sitio", "")), None)
                    if item is None and lista:
                        item = lista[0]
                    estado    = item.get("estado", "ERROR") if item else "ERROR"
                    veredicto = _ESTADO_MAP.get(estado, estado)
                    resultados["R9"] = {"veredicto": veredicto, "detalle": data}

        except Exception as e:
            log.error("%s falló: %s", script, e)
            with lock:
                for r in req:
                    resultados[r] = {"veredicto": "ERROR", "detalle": str(e)}
