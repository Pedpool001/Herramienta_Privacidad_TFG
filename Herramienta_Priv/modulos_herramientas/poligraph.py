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

import requests as _requests

from ._loader import ejecutar_analisis, TFG_DIR

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _url_es_pdf(url: str) -> bool:
    """Comprueba vía HEAD request si la URL devuelve un PDF u otro tipo de descarga."""
    if not url.startswith("http"):
        return False
    try:
        r = _requests.head(url, timeout=8, allow_redirects=True,
                           headers={"User-Agent": _UA})
        ct = r.headers.get("Content-Type", "").lower()
        return "pdf" in ct or "msword" in ct or "vnd.openxmlformats" in ct or "octet-stream" in ct
    except Exception:
        return False


def _pdf_a_html_temporal(url_pdf: str, output_dir: Path) -> str | None:
    """
    Descarga un PDF de política de privacidad y extrae su texto como HTML temporal.

    Estrategia 1: petición HTTP directa (rápida, funciona para la mayoría).
    Estrategia 2: Playwright con accept_downloads=True, para servidores que requieren
                  sesión de navegador real (ej: mediaset.es devuelve 403 a requests pero
                  permite la descarga desde Playwright).

    Devuelve la ruta absoluta al HTML generado, o None si falla (PDF escaneado sin
    texto seleccionable, descarga bloqueada por anti-bot, pdfplumber no instalado).
    """
    try:
        import pdfplumber
    except ImportError:
        log.warning("pdfplumber no instalado. Ejecute: pip install pdfplumber")
        return None

    pdf_path = output_dir / "politica.pdf"
    pdf_bytes: bytes | None = None

    # ── Estrategia 1: HTTP directo ────────────────────────────────────────────
    try:
        log.info("Descargando PDF (HTTP directo): %s", url_pdf)
        r = _requests.get(url_pdf, timeout=30, allow_redirects=True,
                          headers={"User-Agent": _UA})
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "").lower()
        if "html" in ct:
            # La URL devuelve HTML — no es un PDF descargable
            log.info("URL devuelve HTML, no PDF: %s", url_pdf)
            return None
        pdf_bytes = r.content
        log.info("PDF descargado vía HTTP (%d bytes)", len(pdf_bytes))
    except Exception as e:
        log.info("HTTP directo falló (%s) — probando con Playwright...", e)

    # ── Estrategia 2: Playwright accept_downloads ─────────────────────────────
    if pdf_bytes is None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as _pw:
                _browser = _pw.chromium.launch(headless=True)
                _ctx = _browser.new_context(
                    accept_downloads=True,
                    user_agent=_UA,
                    viewport={"width": 1280, "height": 800},
                )
                _page = _ctx.new_page()
                try:
                    with _page.expect_download(timeout=30000) as _dl_info:
                        try:
                            _page.goto(url_pdf, wait_until="domcontentloaded",
                                       timeout=30000)
                        except Exception:
                            pass  # "Download is starting" interrumpe goto; dl_info ya tiene el fichero
                    _dl = _dl_info.value
                    _dl_path = _dl.path()
                    if _dl_path:
                        pdf_bytes = Path(_dl_path).read_bytes()
                        log.info("PDF descargado vía Playwright (%d bytes)", len(pdf_bytes))
                    else:
                        log.warning("Playwright: dl.path() devolvió None")
                except Exception as e2:
                    log.warning("Playwright download falló: %s", e2)
                finally:
                    _browser.close()
        except Exception as e3:
            log.warning("Error iniciando Playwright para descarga: %s", e3)

    if pdf_bytes is None:
        log.warning("No se pudo descargar el PDF por ninguna estrategia: %s", url_pdf)
        return None

    pdf_path.write_bytes(pdf_bytes)

    # ── Extracción de texto con pdfplumber ────────────────────────────────────
    parrafos: list[str] = []
    try:
        with pdfplumber.open(pdf_path) as pdf_doc:
            for pg in pdf_doc.pages:
                texto = pg.extract_text()
                if texto and texto.strip():
                    parrafos.append(texto.strip())
    except Exception as e:
        log.warning("Error leyendo PDF con pdfplumber: %s", e)
        return None

    if not parrafos:
        log.warning("PDF sin texto seleccionable (posiblemente escaneado): %s", url_pdf)
        return None

    # ── Generación de HTML temporal ───────────────────────────────────────────
    texto_completo = "\n\n".join(parrafos)
    texto_html = (
        texto_completo
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    bloques_html = "".join(
        f"<p>{bloque.replace(chr(10), ' ')}</p>\n"
        for bloque in texto_html.split("\n\n")
        if bloque.strip()
    )
    html = (
        "<!DOCTYPE html>\n"
        '<html lang="es">\n'
        '<head><meta charset="utf-8"><title>Política de Privacidad</title></head>\n'
        "<body><main><article>\n"
        f"{bloques_html}"
        "</article></main></body>\n</html>"
    )

    html_path = output_dir / "politica_from_pdf.html"
    html_path.write_text(html, encoding="utf-8")
    log.info("PDF → HTML temporal (%d párrafos): %s", len(parrafos), html_path)
    return str(html_path)


class WrongPolicyPageError(Exception):
    """html_crawler rechazó la URL porque el contenido no parece una política de privacidad."""

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
             resultados: dict, lock: threading.Lock,
             requisitos: set | None = None) -> None:
    """
    Ejecuta el pipeline de PoliGraph y evalúa R1, R5, R14, R19.

    El pipeline (html_crawler → init_document → run_annotators → build_graph)
    siempre se ejecuta si este módulo corre, porque combinados necesita el
    graph.yml resultante para R15 y R16. Solo los scripts de análisis se
    filtran según `requisitos`.

    Args:
        url_politica: URL de la política de privacidad.
        output_dir:   Directorio de salida para los ficheros de PoliGraph.
        resultados:   Dict compartido entre hilos.
        lock:         Lock para escribir en resultados.
        requisitos:   Conjunto de requisitos seleccionados. None = todos.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # Requisitos que este módulo puede evaluar directamente
    _propios = {"R1", "R5", "R14", "R19"}
    sel = (set(requisitos) & _propios) if requisitos else _propios
    # Lista de propios seleccionados — se usa para marcar errores del pipeline
    requisitos_poligraph = [r for r in ["R1", "R5", "R14", "R19"] if r in sel]

    # ── Paso 1: html_crawler ──────────────────────────────────────────────────
    # Si la URL apunta a un fichero PDF (detectado por Content-Type o extensión),
    # intentar extraer el texto antes de llamar a html_crawler.
    url_para_crawler = url_politica
    if url_politica.lower().endswith(".pdf") or _url_es_pdf(url_politica):
        log.info("Política en PDF — intentando extracción de texto: %s", url_politica)
        url_html_temp = _pdf_a_html_temporal(url_politica, output_dir)
        if url_html_temp:
            url_para_crawler = url_html_temp
            log.info("Usando HTML extraído del PDF: %s", url_html_temp)
        else:
            log.warning("PDF sin texto extraíble — NO_EVALUABLE")
            with lock:
                for req in requisitos_poligraph:
                    resultados[req] = {
                        "veredicto": "NO_EVALUABLE",
                        "detalle": "Política en PDF sin texto seleccionable (posiblemente escaneada)",
                    }
            return

    log.info("PoliGraph html_crawler para %s", url_para_crawler)
    try:
        r = _conda_run(
            "python", "-m", "poligrapher.scripts.html_crawler",
            url_para_crawler, str(output_dir),
            timeout=120,
        )
        if r.returncode != 0:
            stderr_msg = r.stderr[-800:]
            if "Not like a privacy policy" in stderr_msg:
                raise WrongPolicyPageError(stderr_msg)
            if "HTTP error 403" in stderr_msg or "Got HTTP error 403" in stderr_msg:
                log.warning("PoliGraph html_crawler: HTTP 403 — política bloqueada por anti-bot")
                with lock:
                    for req in requisitos_poligraph:
                        resultados[req] = {
                            "veredicto": "NO_EVALUABLE",
                            "detalle": "HTTP 403 — política de privacidad bloqueada por anti-bot",
                        }
                return
            if "Download is starting" in stderr_msg or "Download is starting" in r.stdout:
                # Política en descarga detectada por Playwright — intentar extracción PDF
                log.info("html_crawler detectó descarga — intentando extracción de PDF...")
                url_html_temp = _pdf_a_html_temporal(url_para_crawler, output_dir)
                if url_html_temp:
                    r2 = _conda_run(
                        "python", "-m", "poligrapher.scripts.html_crawler",
                        url_html_temp, str(output_dir),
                        timeout=120,
                    )
                    if r2.returncode != 0:
                        raise RuntimeError(f"html_crawler (PDF→HTML): {r2.stderr[-800:]}")
                    log.info("html_crawler completado con HTML extraído del PDF")
                    # r2 tuvo éxito: salir del bloque de error y continuar pipeline
                else:
                    log.warning("No se pudo extraer texto del PDF — NO_EVALUABLE")
                    with lock:
                        for req in requisitos_poligraph:
                            resultados[req] = {
                                "veredicto": "NO_EVALUABLE",
                                "detalle": "Política en formato de descarga no procesable (PDF escaneado o Word)",
                            }
                    return
            else:
                # Error desconocido de html_crawler
                raise RuntimeError(f"html_crawler: {stderr_msg}")
    except WrongPolicyPageError:
        raise  # propagar al caller para que intente URL alternativa
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
    # R1 y R5 necesitan el árbol de accesibilidad del SITIO PRINCIPAL, donde el
    # banner de cookies es visible. playwright_mod.py lo guarda en
    # output_dir.parent/playwright/accessibility_tree.json. Si existe, se usa ese;
    # si no (playwright no terminó aún o falló), se usa el árbol de la política.
    playwright_tree = output_dir.parent / "playwright" / "accessibility_tree.json"

    for req, script in [
        ("R1",  "r1_capas"),
        ("R5",  "r5_revocabilidad"),
        ("R14", "r14_lenguaje"),
        ("R19", "r19_dpo"),
    ]:
        if req not in sel:
            continue
        # R1 y R5 prefieren el árbol del sitio principal (con el banner de cookies)
        if script in ("r1_capas", "r5_revocabilidad") and playwright_tree.exists():
            script_input = str(playwright_tree)
        else:
            script_input = str(output_dir)
        try:
            data = ejecutar_analisis(script, script_input)
            with lock:
                resultados[req] = {
                    "veredicto": data.get("veredicto", "ERROR"),
                    "detalle":   data,
                }
        except Exception as e:
            log.error("%s falló: %s", script, e)
            with lock:
                resultados[req] = {"veredicto": "ERROR", "detalle": str(e)}
