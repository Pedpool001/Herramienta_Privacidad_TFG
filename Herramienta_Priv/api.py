"""
API REST — Herramienta de auditoría de privacidad web.

Endpoints:
  GET  /                    — interfaz web
  POST /api/audit/single    — lanza auditoría de un sitio único
  POST /api/audit/batch     — lanza auditoría de múltiples sitios
  GET  /api/status/<tid>    — estado y logs en tiempo real
  GET  /report/<tid>        — sirve el informe HTML generado

Uso:
  python3 api.py
  python3 api.py --port 5000 --debug
"""

import argparse
import logging
import sys
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file

# ── Rutas ─────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

_TASKS_DIR = _HERE / "output" / "api_tasks"

app = Flask(__name__, template_folder=str(_HERE / "templates"))

# ── Almacén de tareas ─────────────────────────────────────────────────────────
# task_id → { estado, logs, report_path, error, creado, modo, sitios_total,
#             sitios_done }
_tasks: dict = {}
_tasks_lock = threading.Lock()


# ── Log handler por tarea ─────────────────────────────────────────────────────

class _TaskLogHandler(logging.Handler):
    """Redirige mensajes de logging al log de la tarea en tiempo real."""

    def __init__(self, task_id: str):
        super().__init__()
        self.task_id = task_id
        self.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S"
        ))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with _tasks_lock:
                t = _tasks.get(self.task_id)
                if t is not None:
                    t["logs"].append(msg)
        except Exception:
            pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _nueva_tarea(modo: str, sitios_total: int = 1) -> str:
    task_id = uuid.uuid4().hex[:12]
    (_TASKS_DIR / task_id).mkdir(parents=True, exist_ok=True)
    with _tasks_lock:
        _tasks[task_id] = {
            "estado":       "pending",
            "logs":         [],
            "report_path":  None,
            "error":        None,
            "creado":       datetime.now().isoformat(timespec="seconds"),
            "modo":         modo,
            "sitios_total": sitios_total,
            "sitios_done":  0,
        }
    return task_id


def _set_task(task_id: str, **kwargs) -> None:
    with _tasks_lock:
        if task_id in _tasks:
            _tasks[task_id].update(kwargs)


def _encontrar_informe(task_dir: Path, modo: str) -> str | None:
    if modo == "batch":
        p = task_dir / "informe_batch.html"
        return str(p) if p.exists() else None
    # single: informe.html dentro del único subdirectorio del sitio
    subdirs = sorted(
        (d for d in task_dir.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime, reverse=True,
    )
    for sub in subdirs:
        p = sub / "informe.html"
        if p.exists():
            return str(p)
    return None


# ── Workers (se ejecutan en hilos daemon) ─────────────────────────────────────

def _run_single(task_id: str, url: str,
                requisitos: set | None = None) -> None:
    task_dir = _TASKS_DIR / task_id
    handler = _TaskLogHandler(task_id)
    root_log = logging.getLogger()
    root_log.addHandler(handler)
    try:
        _set_task(task_id, estado="running")
        import main as _main
        _main.auditar(url, parent_dir=task_dir, requisitos=requisitos)
        report = _encontrar_informe(task_dir, "single")
        _set_task(task_id, estado="done", report_path=report, sitios_done=1)
    except Exception as exc:
        _set_task(task_id, estado="error", error=str(exc))
    finally:
        root_log.removeHandler(handler)


def _run_batch(task_id: str, urls: list[str],
               requisitos: set | None = None) -> None:
    task_dir = _TASKS_DIR / task_id
    handler = _TaskLogHandler(task_id)
    root_log = logging.getLogger()
    root_log.addHandler(handler)
    tmp_path: str | None = None
    try:
        _set_task(task_id, estado="running")

        # Guardar URLs en fichero temporal que acepta auditar_batch()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            tmp_path = f.name
            f.write("\n".join(urls))

        import batch as _batch
        # Monkey-patch para actualizar sitios_done en tiempo real
        _original_auditar = None
        try:
            import main as _main
            _original_auditar = _main.auditar

            def _auditar_con_progreso(url_sitio, **kw):
                result = _original_auditar(url_sitio, **kw)
                with _tasks_lock:
                    if task_id in _tasks:
                        _tasks[task_id]["sitios_done"] += 1
                return result

            _main.auditar = _auditar_con_progreso
            _batch.auditar_batch(tmp_path, dir_salida=str(task_dir),
                                 requisitos=requisitos)
        finally:
            if _original_auditar is not None:
                _main.auditar = _original_auditar

        report = _encontrar_informe(task_dir, "batch")
        _set_task(task_id, estado="done", report_path=report,
                  sitios_done=len(urls))
    except Exception as exc:
        _set_task(task_id, estado="error", error=str(exc))
    finally:
        root_log.removeHandler(handler)
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


# ── Rutas Flask ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


def _parse_requisitos(data: dict) -> set | None:
    """Extrae la lista de requisitos del body JSON → set o None (= todos)."""
    raw = data.get("requisitos")
    if not raw:
        return None
    sel = {r.strip().upper() for r in raw if isinstance(r, str) and r.strip()}
    return sel if sel else None


@app.route("/api/audit/single", methods=["POST"])
def api_single():
    data = request.get_json(force=True, silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Campo 'url' requerido"}), 400
    if not url.startswith("http"):
        url = "https://" + url

    requisitos = _parse_requisitos(data)

    task_id = _nueva_tarea("single", sitios_total=1)
    threading.Thread(
        target=_run_single, args=(task_id, url),
        kwargs={"requisitos": requisitos},
        daemon=True, name=f"single-{task_id[:6]}",
    ).start()
    return jsonify({"task_id": task_id}), 202


@app.route("/api/audit/batch", methods=["POST"])
def api_batch():
    # Acepta JSON {urls: ["url1", ...], requisitos: ["R1", ...]} o form con textarea/fichero
    requisitos = None
    if request.is_json:
        data = request.get_json(force=True, silent=True) or {}
        raw = data.get("urls") or []
        urls = [u.strip() for u in raw if isinstance(u, str) and u.strip()]
        requisitos = _parse_requisitos(data)
    else:
        uploaded = request.files.get("file")
        if uploaded:
            content = uploaded.read().decode("utf-8", errors="ignore")
        else:
            content = request.form.get("urls", "")
        urls = [
            ln.strip() for ln in content.splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]

    if not urls:
        return jsonify({"error": "Se requiere al menos una URL"}), 400

    urls = [u if u.startswith("http") else "https://" + u for u in urls]

    task_id = _nueva_tarea("batch", sitios_total=len(urls))
    threading.Thread(
        target=_run_batch, args=(task_id, urls),
        kwargs={"requisitos": requisitos},
        daemon=True, name=f"batch-{task_id[:6]}",
    ).start()
    return jsonify({"task_id": task_id}), 202


@app.route("/api/status/<task_id>")
def api_status(task_id: str):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        abort(404)
    return jsonify({
        "estado":        task["estado"],
        "modo":          task["modo"],
        "creado":        task["creado"],
        "sitios_total":  task["sitios_total"],
        "sitios_done":   task["sitios_done"],
        "logs":          task["logs"][-300:],
        "error":         task["error"],
        "report_url":    f"/report/{task_id}" if task["report_path"] else None,
    })


@app.route("/report/<task_id>")
def report(task_id: str):
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None or not task.get("report_path"):
        abort(404)
    return send_file(task["report_path"])


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="API de auditoría de privacidad web")
    parser.add_argument("--host",  default="0.0.0.0")
    parser.add_argument("--port",  type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(threadName)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    _TASKS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n  Auditoría de Privacidad Web")
    print(f"  http://localhost:{args.port}/\n")
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=False)
