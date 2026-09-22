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

from ._loader import ejecutar_analisis, TFG_DIR, ANALYSIS_DATA, dir_analisis as _dir_analisis

log = logging.getLogger(__name__)

# ── Rutas ─────────────────────────────────────────────────────────────────────
PP_DIR         = TFG_DIR / "privacy-pioneer-web-crawler"
CRAWLER_DIR    = PP_DIR / "selenium-crawler"
REST_API_DIR   = PP_DIR / "rest-api"

# Timeout del crawler en segundos (por defecto ~5 min por sitio)
CRAWLER_TIMEOUT = 360


def _dominio(url: str) -> str:
    return urlparse(url).netloc.lstrip("www.")


def _contar_evidencias(dominio: str, *, solo_snippets: bool = False) -> int | None:
    """
    Cuenta las filas que la extensión registró en MySQL para este dominio.

    Es la señal que distingue "el sitio no tiene rastreadores" de "la captura
    falló": un sitio real siempre genera tráfico de red, así que 0 filas tras un
    crawl que sí visitó el sitio indica casi siempre un fallo de captura de la
    extensión (Bugs 22/37/40), no ausencia de rastreadores. Devuelve None si no
    se pudo consultar MySQL (para no confundir el fallo de consulta con 0 filas).

    Con `solo_snippets=True` cuenta únicamente las filas con `snippet` no vacío,
    que son las que necesita R9 (minimización) para poder evaluarse.
    """
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host=os.getenv("MYSQL_HOST", "localhost"),
            user=os.getenv("MYSQL_USER", "pioneer"),
            password=os.getenv("MYSQL_PASSWORD", "abc"),
            database=os.getenv("MYSQL_DATABASE", "analysis"),
        )
        cur = conn.cursor()
        sql = "SELECT COUNT(*) FROM entries WHERE rootUrl LIKE %s"
        if solo_snippets:
            sql += " AND snippet IS NOT NULL AND snippet != ''"
        cur.execute(sql, (f"%{dominio}%",))
        n = cur.fetchone()[0]
        conn.close()
        return int(n)
    except Exception as e:
        log.warning("No se pudo contar evidencias de %s: %s", dominio, e)
        return None


def _esperar_estabilizacion(dominio: str, timeout: float = 25.0,
                            intervalo: float = 2.0) -> int | None:
    """
    Espera a que la evidencia del dominio esté completa en MySQL.

    La extensión Privacy Pioneer no inserta la evidencia poco a poco, sino de
    golpe cuando su temporizador `sender` dispara. Antes de ese instante hay 0
    filas; justo después, todas. Por eso NO basta con esperar a "dos lecturas
    iguales": si el sondeo cae en la ventana previa al envío, leería 0, 0 y daría
    por estable un 0 que en realidad era "aún no ha llegado nada" → R9 correría
    contra MySQL vacío y saldría SIN_DATOS.

    La corrección: un 0 nunca se considera estable. Solo se acepta la
    estabilización cuando ya hay filas (`total > 0`) y dos lecturas consecutivas
    de (total, con_snippet) coinciden. Si el conteo sigue en 0 hasta el timeout,
    entonces sí es un sitio sin rastreadores o una captura fallida, y se devuelve
    0 con conocimiento de causa. Se estabiliza también sobre el nº de filas con
    snippet porque son las que R9 necesita.

    Debe invocarse con la REST API todavía viva, para dar margen a que el envío
    de la extensión termine de insertarse.
    """
    prev, t0, n_total = None, time.monotonic(), 0
    while time.monotonic() - t0 < timeout:
        n_total = _contar_evidencias(dominio)
        if n_total is None:               # no se pudo consultar: no bloquear
            return None
        n_snip = _contar_evidencias(dominio, solo_snippets=True) or 0
        actual = (n_total, n_snip)
        # Un 0 no cuenta como estable: puede ser "el sender aún no ha disparado".
        if n_total > 0 and actual == prev:
            break                         # dos lecturas iguales y con datos
        prev = actual
        time.sleep(intervalo)
    return n_total


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


_EXCLUSIVO = threading.Lock()
"""
Cerrojo global del crawl de Privacy Pioneer.

El crawl usa recursos de proceso único que no admiten concurrencia: el puerto
8080 de la REST API (que además se libera con `fuser -k` al arrancar, matando
la instancia de otro sitio), la tabla MySQL `entries` con un único timestamp
de consentimiento global, el CSV `prueba-tfg.csv` del crawler y la ventana de
Firefox headful. Por eso, aunque la auditoría procese varios sitios en
paralelo, solo un sitio puede estar crawleando con Privacy Pioneer a la vez.
"""


def _crawl(url: str, output_dir: Path, dominio: str,
           resultados: dict, lock: threading.Lock) -> bool:
    """
    Ejecuta el crawl de Privacy Pioneer (pasos 1-6). Debe invocarse siempre
    bajo `_EXCLUSIVO`. Devuelve False si no se pudo ni siquiera arrancar.
    """
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
        return False

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
        # Esperar a que la evidencia se estabilice ANTES de matar la REST API:
        # la extensión inserta los snippets (que necesita R9) con retardo, y si
        # el análisis corre demasiado pronto esa evidencia se pierde.
        _count = _esperar_estabilizacion(dominio)
        log.info("MySQL filas insertadas para %s: %s", dominio,
                 _count if _count is not None else "?")
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

    return True


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

    # Sección crítica: solo un sitio puede crawlear con Privacy Pioneer a la vez.
    with _EXCLUSIVO:
        log.info("Privacy Pioneer: turno exclusivo para %s", dominio)
        if not _crawl(url, output_dir, dominio, resultados, lock):
            return

    # A partir de aquí ya no hay recursos compartidos: cada sitio escribe su
    # JSON en su propio analysis_data y las consultas MySQL van filtradas por
    # dominio, así que el análisis puede solaparse con el crawl de otro sitio.

    # Nº de evidencias capturadas: sirve para (a) distinguir captura fallida de
    # sitio limpio cuando el veredicto es NO_EVALUABLE, y (b) informar de la
    # fiabilidad de un veredicto (uno basado en 3 evidencias es menos robusto
    # que uno basado en 88, dada la variabilidad de la publicidad programática).
    n_evidencias = _contar_evidencias(dominio)
    captura_vacia = n_evidencias == 0

    # 7. Ejecutar análisis (solo para requisitos seleccionados)
    for req, script, args in [
        (["R2", "R3"], "r2_r3_cookies_beacons", [dominio]),
        (["R9"],       "r9_minimizacion",        [dominio]),
    ]:
        if not any(r in sel for r in req):
            continue
        try:
            data = ejecutar_analisis(script, *args,
                                     salida_dir=_dir_analisis(output_dir))

            with lock:
                if script == "r2_r3_cookies_beacons":
                    # r2/r3: dict con clave "sitios" → {dominio: {veredicto, ...}}
                    sitios  = data.get("sitios", {})
                    if data.get("no_evaluable") or not sitios:
                        veredicto = "NO_EVALUABLE"
                        # Distinguir captura fallida de sitio sin rastreadores:
                        # 0 filas tras visitar el sitio es casi siempre un fallo
                        # de captura de la extensión, no un sitio limpio.
                        if captura_vacia:
                            entrada = {"detalle":
                                "Captura vacía: el crawler visitó el sitio pero la extensión "
                                "de Privacy Pioneer no registró tráfico de red. Probable fallo "
                                "de captura, no ausencia de rastreadores.",
                                "n_evidencias": 0}
                        else:
                            entrada = {"detalle":
                                "No hay datos de Privacy Pioneer para este sitio.",
                                "n_evidencias": n_evidencias}
                    else:
                        entrada = next(
                            (v for k, v in sitios.items() if dominio in k or k in dominio),
                            next(iter(sitios.values()), None)
                        )
                        veredicto = entrada.get("veredicto", "ERROR") if entrada else "NO_EVALUABLE"
                        if entrada is not None and n_evidencias is not None:
                            entrada["n_evidencias"] = n_evidencias
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
                    detalle_r9 = {"resultado": data, "n_evidencias": n_evidencias}
                    # Si el veredicto es NO_EVALUABLE por captura vacía, aclararlo
                    if veredicto == "NO_EVALUABLE" and captura_vacia:
                        detalle_r9["detalle"] = (
                            "Captura vacía: la extensión de Privacy Pioneer no registró "
                            "tráfico. Probable fallo de captura, no ausencia de datos.")
                    resultados["R9"] = {"veredicto": veredicto, "detalle": detalle_r9}

        except Exception as e:
            log.error("%s falló: %s", script, e)
            with lock:
                for r in req:
                    resultados[r] = {"veredicto": "ERROR", "detalle": str(e)}
