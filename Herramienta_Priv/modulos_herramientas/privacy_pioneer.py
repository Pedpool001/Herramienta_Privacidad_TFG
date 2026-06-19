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
import os
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
            host=os.getenv("MYSQL_HOST", "localhost"),
            user=os.getenv("MYSQL_USER", "pioneer"),
            password=os.getenv("MYSQL_PASSWORD", "abc"),
            database=os.getenv("MYSQL_DATABASE", "analysis"),
        )
        cur = conn.cursor()
        cur.execute("DELETE FROM entries WHERE rootUrl LIKE %s", (f"%{dominio}%",))
        conn.commit()
        conn.close()
        log.info("MySQL limpiado para %s (%d filas)", dominio, cur.rowcount)
    except Exception as e:
        log.warning("No se pudo limpiar MySQL: %s", e)


def ejecutar(url: str, output_dir: Path, resultados: dict, lock: threading.Lock,
             requisitos: set | None = None) -> None:
    """
    Lanza Privacy Pioneer para el sitio dado, luego evalúa R2, R3 y R9.

    El crawl siempre se ejecuta (sus datos los consume también combinados para
    R12 y R15). Solo se ejecutan los scripts de análisis cuyos requisitos
    estén en `requisitos`.

    Args:
        url:        URL del sitio a auditar.
        output_dir: Directorio donde guardar la salida de la herramienta.
        resultados: Dict compartido entre hilos.
        lock:       Lock para escribir en resultados.
        requisitos: Conjunto de requisitos seleccionados. None = todos.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    sel = set(requisitos) if requisitos else {"R2", "R3", "R9"}
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

    # 3. Iniciar REST API (liberar puerto 8080 si lo ocupa un proceso previo)
    try:
        subprocess.run(["fuser", "-k", "8080/tcp"], capture_output=True)
    except FileNotFoundError:
        subprocess.run(["pkill", "-f", "rest-api/index.js"], capture_output=True, check=False)
    time.sleep(1)

    # Preparar entorno con display (necesario para Firefox headful en Docker)
    xvfb_proc = None
    env = os.environ.copy()
    if not env.get("DISPLAY"):
        try:
            xvfb_proc = subprocess.Popen(
                ["Xvfb", ":99", "-screen", "0", "1280x720x24", "-ac"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1)
            env["DISPLAY"] = ":99"
            log.info("Xvfb iniciado en :99 para Firefox headful")
        except FileNotFoundError:
            log.warning("Xvfb no disponible; Firefox puede fallar sin display")
    log.info("Iniciando REST API de Privacy Pioneer…")
    api_log_path = output_dir / "rest_api.log"
    api_log_file = open(api_log_path, "w")
    api_proc = subprocess.Popen(
        ["node", "index.js"],
        cwd=str(REST_API_DIR),
        stdout=api_log_file,
        stderr=api_log_file,
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
            env=env,
        )
        # Guardar stdout completo para diagnóstico
        crawler_log = output_dir / "crawler.log"
        crawler_log.write_text(crawler.stdout or "", encoding="utf-8", errors="replace")
        log.info("Crawler terminó (código %d). Log completo en %s. Stdout últimas 15 líneas:\n%s",
                 crawler.returncode, crawler_log,
                 "\n".join(crawler.stdout.splitlines()[-15:]) if crawler.stdout else "(vacío)")
        if crawler.stderr:
            log.warning("Crawler stderr: %s", crawler.stderr[-400:])
        # Comprobar cuántas filas se insertaron en MySQL
        try:
            import mysql.connector
            _conn = mysql.connector.connect(
                host=os.getenv("MYSQL_HOST", "localhost"),
                user=os.getenv("MYSQL_USER", "pioneer"),
                password=os.getenv("MYSQL_PASSWORD", "abc"),
                database=os.getenv("MYSQL_DATABASE", "analysis"),
            )
            _cur = _conn.cursor()
            _cur.execute("SELECT COUNT(*) FROM entries WHERE rootUrl LIKE %s", (f"%{dominio}%",))
            _count = _cur.fetchone()[0]
            _conn.close()
            log.info("MySQL filas insertadas para %s: %d", dominio, _count)
        except Exception as _e:
            log.warning("No se pudo consultar MySQL post-crawl: %s", _e)
    except subprocess.TimeoutExpired:
        log.warning("Crawler superó timeout (%ds)", CRAWLER_TIMEOUT)
    finally:
        # 5. Matar REST API y restaurar CSV
        if xvfb_proc:
            try:
                xvfb_proc.terminate()
                xvfb_proc.wait(timeout=3)
            except Exception:
                pass
        api_proc.terminate()
        try:
            api_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            api_proc.kill()
        api_log_file.close()
        # Loguear el output de la REST API
        try:
            api_log_content = api_log_path.read_text(encoding="utf-8", errors="replace")
            log.info("REST API log:\n%s", api_log_content[-600:] if api_log_content else "(vacío)")
        except Exception:
            pass

        csv_sitio.unlink(missing_ok=True)
        if csv_bak.exists():
            csv_bak.rename(csv_sitio)

    # 6. Copiar reporte de cookies al output_dir
    reporte_src = ANALYSIS_DATA / "reporte_auditoria.json"
    if reporte_src.exists():
        import shutil
        shutil.copy2(reporte_src, output_dir / "reporte_auditoria.json")

    # 7. Ejecutar análisis (solo para requisitos seleccionados)
    for req, script, args in [
        (["R2", "R3"], "r2_r3_cookies_beacons", [dominio]),
        (["R9"],       "r9_minimizacion",        [dominio]),
    ]:
        if not any(r in sel for r in req):
            continue
        try:
            data = ejecutar_analisis(script, *args)

            with lock:
                if script == "r2_r3_cookies_beacons":
                    # r2/r3: dict con clave "sitios" → {dominio: {veredicto, ...}}
                    sitios  = data.get("sitios", {})
                    if data.get("no_evaluable") or not sitios:
                        veredicto = "NO_EVALUABLE"
                        entrada   = {"detalle": "No hay datos de Privacy Pioneer para este sitio."}
                    else:
                        entrada = next(
                            (v for k, v in sitios.items() if dominio in k or k in dominio),
                            next(iter(sitios.values()), None)
                        )
                        veredicto = entrada.get("veredicto", "ERROR") if entrada else "NO_EVALUABLE"
                    if "R2" in sel:
                        resultados["R2"] = {"veredicto": veredicto, "detalle": entrada or {}}
                    if "R3" in sel:
                        resultados["R3"] = {"veredicto": veredicto, "detalle": entrada or {}}
                else:
                    # r9: lista de dicts con {sitio, estado, ...}
                    _ESTADO_MAP = {"FALLO": "FAILED", "ADVERTENCIA": "WARNING",
                                   "OK": "PASSED", "SIN_DATOS": "NO_EVALUABLE"}
                    lista = data if isinstance(data, list) else []
                    item  = next((d for d in lista if dominio in d.get("sitio", "")), None)
                    if item is None and lista:
                        item = lista[0]
                    estado    = item.get("estado", "SIN_DATOS") if item else "SIN_DATOS"
                    veredicto = _ESTADO_MAP.get(estado, estado)
                    resultados["R9"] = {"veredicto": veredicto, "detalle": data}

        except Exception as e:
            log.error("%s falló: %s", script, e)
            with lock:
                for r in req:
                    resultados[r] = {"veredicto": "ERROR", "detalle": str(e)}
