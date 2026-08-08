"""Ephemerer Sandbox-Entrypoint – führt build123d/cadquery-Code isoliert aus
(SPEC Kap. 1.6, 4).

Dieses Skript ist bewusst als eigenständige Datei ohne Abhängigkeiten zum
restlichen Projekt gehalten (Isolationsprinzip: der Sandbox-Container kennt
nur sich selbst, nicht `agents`/`app`).

Aufruf:
    python run.py <script_path|-> [export_dir]

    - <script_path>: Pfad zu einer Python-Datei mit dem auszuführenden Code.
      Wird "-" übergeben (oder ganz weggelassen), wird der Code stattdessen
      von stdin gelesen.
    - [export_dir]: Zielordner für STEP-/STL-Export (Default: /exports).

Ausgabe (letzte Zeile auf stdout, einzeiliges JSON):
    {"status": "SUCCESS" | "SANDBOX_ERROR",
     "error_type": str | None,
     "error_message": str | None,
     "traceback": str | None,
     "stdout": str,             # von der Nutzer-Code-Ausführung erzeugte Ausgaben
     "export_paths": [str]}     # Pfade der erzeugten .step/.stl-Dateien

Exit-Code 0 bei Erfolg, 1 bei einem abgefangenen Fehler im generierten Code.

Export-Konvention: Der generierte Code muss die Geometrie in einer Variablen
namens `part` ablegen, z. B. `with BuildPart() as part: ...` (build123d) oder
äquivalent ein Objekt mit `.part`-Attribut. Ohne diese Konvention wird die
Ausführung weiterhin als Erfolg gewertet, aber `export_paths` bleibt leer.
"""

import contextlib
import io
import json
import sys
import traceback
import uuid
from pathlib import Path

DEFAULT_EXPORT_DIR = "/exports"


def _export_geometry(namespace: dict, export_dir: Path) -> list[str]:
    builder = namespace.get("part")
    shape = getattr(builder, "part", None) if builder is not None else None
    if shape is None:
        return []

    try:
        from build123d import export_step, export_stl
    except ImportError:
        return []

    run_id = uuid.uuid4().hex[:8]
    step_path = export_dir / f"model_{run_id}.step"
    stl_path = export_dir / f"model_{run_id}.stl"

    exported: list[str] = []
    try:
        export_step(shape, str(step_path))
        exported.append(str(step_path))
    except Exception:  # noqa: BLE001 - Export-Fehler dürfen die Sandbox nicht abstürzen lassen
        pass
    try:
        export_stl(shape, str(stl_path))
        exported.append(str(stl_path))
    except Exception:  # noqa: BLE001
        pass

    return exported


def run(code: str, export_dir: Path) -> dict:
    namespace: dict = {"__name__": "__sandbox__"}
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()

    try:
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            exec(compile(code, "<generated_cad_code>", "exec"), namespace)  # noqa: S102 - Zweck der Sandbox
    except Exception as exc:  # noqa: BLE001 - Sandbox muss jeden Fehler des generierten Codes abfangen
        return {
            "status": "SANDBOX_ERROR",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc(),
            "stdout": stdout_buffer.getvalue(),
            "export_paths": [],
        }

    export_paths = _export_geometry(namespace, export_dir)
    return {
        "status": "SUCCESS",
        "error_type": None,
        "error_message": None,
        "traceback": None,
        "stdout": stdout_buffer.getvalue(),
        "export_paths": export_paths,
    }


def main() -> None:
    args = sys.argv[1:]

    if args and args[0] != "-":
        code = Path(args[0]).read_text(encoding="utf-8")
    else:
        code = sys.stdin.read()

    export_dir = Path(args[1]) if len(args) > 1 else Path(DEFAULT_EXPORT_DIR)
    export_dir.mkdir(parents=True, exist_ok=True)

    result = run(code, export_dir)
    print(json.dumps(result))
    sys.exit(0 if result["status"] == "SUCCESS" else 1)


if __name__ == "__main__":
    main()
