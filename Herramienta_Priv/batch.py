"""
Modo batch — auditoría de múltiples sitios desde un fichero de URLs.

Uso:
    python3 main.py --batch sitios.txt
    python3 main.py --batch sitios.txt --salida resultados/
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

REQUISITOS = [f"R{i}" for i in range(1, 20)]

# Tope duro de sitios concurrentes. Por encima de 3-4 no se gana tiempo: el
# crawl de Privacy Pioneer está serializado (usa puerto y BD globales) y pasa a
# ser el factor limitante. Subir más solo aumenta el riesgo de que los timeouts
# de las herramientas salten y conviertan veredictos válidos en ERROR.
_MAX_WORKERS_TOPE = 4


def calcular_workers() -> int:
    """
    Calcula cuántos sitios auditar en paralelo según los recursos de la máquina.

    Cada sitio lanza ya ~7 navegadores (uno por herramienta), por lo que el
    número de sitios concurrentes debe ser bastante menor que el de núcleos.
    Se toma el mínimo entre la capacidad de CPU y la de memoria, ya que un
    navegador consume del orden de 200-300 MB.
    """
    try:
        nucleos = os.cpu_count() or 2
    except Exception:
        nucleos = 2

    # Memoria disponible (no total): lo que realmente se puede usar sin swap
    ram_gb = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for linea in f:
                if linea.startswith("MemAvailable:"):
                    ram_gb = int(linea.split()[1]) / 1024 / 1024
                    break
    except Exception:
        pass

    por_cpu = max(1, nucleos // 3)
    por_ram = max(1, int(ram_gb // 4)) if ram_gb else por_cpu

    return max(1, min(por_cpu, por_ram, _MAX_WORKERS_TOPE))

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
                  requisitos: set | None = None,
                  workers: int | None = None) -> dict:
    """
    Audita todos los sitios del fichero, procesando varios en paralelo.

    Args:
        fichero_urls: Ruta al fichero .txt con una URL por línea.
        dir_salida:   Directorio donde guardar los informes. Si es None,
                      se crea automáticamente output/batch_<timestamp>/.
        requisitos:   Conjunto de requisitos a evaluar. None = todos.
        workers:      Sitios a auditar en paralelo. None = automático según
                      los recursos de la máquina. 1 fuerza el modo secuencial.

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

    n_workers = workers if workers and workers > 0 else calcular_workers()
    n_workers = min(n_workers, total)

    print(f"\n  Auditando {total} sitios · {n_workers} en paralelo")
    if n_workers > 1:
        print("  (el crawl de Privacy Pioneer se serializa: usa puerto y BD globales)")

    # `print` desde varios hilos entrelaza líneas; este lock las mantiene juntas
    io_lock = threading.Lock()
    completados = 0

    def _auditar_uno(url: str) -> tuple[str, dict]:
        """Audita un sitio y devuelve (url, resultados). No lanza excepciones."""
        try:
            return url, auditar(url, parent_dir=base_dir, requisitos=requisitos)
        except Exception as e:
            log.error("Error auditando %s: %s", url, e)
            return url, {r: {"veredicto": "ERROR", "detalle": str(e)}
                         for r in REQUISITOS}

    if n_workers == 1:
        # Modo secuencial: se conserva el formato de salida original
        for i, url in enumerate(urls, 1):
            separador = "=" * 62
            print(f"\n{separador}")
            print(f"  [{i}/{total}]  {url}")
            print(separador)
            _, res = _auditar_uno(url)
            resultados_batch[url] = res
            _imprimir_fila_resumen(url, res, i, total)
    else:
        with ThreadPoolExecutor(max_workers=n_workers,
                                thread_name_prefix="sitio") as pool:
            futuros = {pool.submit(_auditar_uno, u): u for u in urls}
            for fut in as_completed(futuros):
                url, res = fut.result()
                resultados_batch[url] = res
                with io_lock:
                    completados += 1
                    print(f"\n  [{completados}/{total}]  {url}")
                    _imprimir_fila_resumen(url, res, completados, total)

    # Reordenar según el fichero de entrada: as_completed devuelve los sitios en
    # orden de finalización, y el informe debe respetar el orden original.
    resultados_batch = {u: resultados_batch[u] for u in urls if u in resultados_batch}

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
