"""
Modo batch — auditoría de múltiples sitios desde un fichero de URLs.

Uso:
    python3 main.py --batch sitios.txt
    python3 main.py --batch sitios.txt --salida resultados/
"""

import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

REQUISITOS = [f"R{i}" for i in range(1, 20)]

_COLORES_TERMINAR = {
    "PASSED":       "\033[92m",
    "WARNING":      "\033[93m",
    "FAILED":       "\033[91m",
    "NO_EVALUABLE": "\033[90m",
    "ERROR":        "\033[90m",
}
_RESET = "\033[0m"


def leer_urls(fichero: str) -> list[str]:
    """Lee el fichero de URLs, ignorando líneas vacías y comentarios (#)."""
    urls = []
    with open(fichero, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if linea and not linea.startswith("#"):
                if not linea.startswith("http"):
                    linea = "https://" + linea
                urls.append(linea)
    if not urls:
        raise ValueError(f"No se encontraron URLs en {fichero}")
    return urls


def auditar_batch(fichero_urls: str, dir_salida: str | None = None,
                  requisitos: set | None = None) -> dict:
    """
    Ejecuta auditorías secuenciales para cada URL del fichero.

    Args:
        fichero_urls: Ruta al fichero .txt con una URL por línea.
        dir_salida:   Directorio donde guardar los informes. Si es None,
                      se crea automáticamente output/batch_<timestamp>/.
        requisitos:   Conjunto de requisitos a evaluar. None = todos.

    Returns:
        {url: {R1: {veredicto, detalle}, ...}, ...}
    """
    from main import auditar

    urls = leer_urls(fichero_urls)

    # Directorio raíz de esta sesión batch
    if dir_salida:
        base_dir = Path(dir_salida)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = Path(__file__).parent / "output" / f"batch_{timestamp}"
    base_dir.mkdir(parents=True, exist_ok=True)

    resultados_batch: dict[str, dict] = {}
    total = len(urls)

    for i, url in enumerate(urls, 1):
        separador = "=" * 62
        print(f"\n{separador}")
        print(f"  [{i}/{total}]  {url}")
        print(separador)

        try:
            res = auditar(url, parent_dir=base_dir, requisitos=requisitos)
            resultados_batch[url] = res
        except Exception as e:
            log.error("Error auditando %s: %s", url, e)
            resultados_batch[url] = {
                r: {"veredicto": "ERROR", "detalle": str(e)}
                for r in REQUISITOS
            }

        _imprimir_fila_resumen(url, resultados_batch[url], i, total)

    # ── Informe batch ─────────────────────────────────────────────────────────
    ruta_informe = str(base_dir / "informe_batch.html")
    try:
        from salida_batch import generar_informe_batch
        generar_informe_batch(resultados_batch, ruta_informe)
    except Exception as e:
        log.error("Error al generar informe batch: %s", e)

    _imprimir_resumen_final(resultados_batch)
    return resultados_batch


def _imprimir_fila_resumen(url: str, resultados: dict, i: int, total: int) -> None:
    """Imprime una línea de resumen con colores tras auditar un sitio."""
    counts = {"PASSED": 0, "WARNING": 0, "FAILED": 0, "NO_EVALUABLE": 0, "ERROR": 0}
    for r in REQUISITOS:
        v = resultados.get(r, {}).get("veredicto", "ERROR")
        if v == "NO_SOLICITADO":
            continue
        counts[v if v in counts else "ERROR"] += 1
    partes = " | ".join(
        f"{_COLORES_TERMINAR[k]}{k}×{c}{_RESET}"
        for k, c in counts.items() if c > 0
    )
    print(f"  → {partes}")


def _imprimir_resumen_final(resultados_batch: dict) -> None:
    """Tabla de resumen por consola al finalizar todas las auditorías."""
    urls = list(resultados_batch)
    print(f"\n{'─'*62}")
    print(f"  RESUMEN BATCH — {len(urls)} sitios auditados")
    print(f"{'─'*62}")

    header = f"  {'Sitio':<30} " + "  ".join(f"{r:>3}" for r in REQUISITOS)
    print(header)
    print(f"  {'─'*30} " + "  ".join("───" for _ in REQUISITOS))

    _LETRA = {"PASSED": "✓", "WARNING": "!", "FAILED": "✗",
              "NO_EVALUABLE": "─", "ERROR": "E", "NO_SOLICITADO": "○"}

    for url in urls:
        dominio = url.replace("https://", "").replace("http://", "").lstrip("www.")[:28]
        celdas = []
        for r in REQUISITOS:
            v = resultados_batch[url].get(r, {}).get("veredicto", "ERROR")
            letra = _LETRA.get(v, "?")
            color = _COLORES_TERMINAR.get(v, "")
            celdas.append(f"{color}{letra:>3}{_RESET}")
        print(f"  {dominio:<30} " + "  ".join(celdas))

    print(f"{'─'*62}\n")
