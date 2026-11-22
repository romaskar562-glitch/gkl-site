# -*- coding: utf-8 -*-
"""Сервер калькулятора v2 (API + экран проверки).

По HTTP-эндпоинтам:
  POST /api/calculate-v2  — загрузка PDF дизайн-проекта, разбор + расчёт,
                            возвращает JSON: помещения, ведомость, итог.
  POST /api/report-v2     — генерация PDF-отчёта (чертёж + ведомость)
                            и отправка файла.
  GET  /api/report-v2     — скачивание уже генерированного отчёта.
  GET  /                  — экран проверки (index_v2.html).
  GET  /static/<file>     — статика (скрипт, стили).

Работает без SketchUp: чертёж каркаса генерируется 2D (matplotlib).
"""

from __future__ import annotations

import io
import math
from hashlib import sha256
import json
import os
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4
from threading import Lock, BoundedSemaphore
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

# Включаем каталог этого модуля в путь, чтобы импортировать парсер/движок
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from parser import parse_pdf                     # noqa: E402
from engine import calculate, load_smeta, result_to_dict  # noqa: E402
from outline import draw_outline                  # noqa: E402
from report import build_report                 # noqa: E402

ROOT = BASE_DIR
PROJECTS = {}
PROJECT_LOCK = Lock()
UPLOAD_SLOT = BoundedSemaphore(1)
DRAW_LOCK = Lock()


def read_pdf_bytes(body: bytes) -> dict:
    """Разбирает PDF и сохраняет project.json. Возвращает структуру проекта."""
    proj = parse_pdf(body)
    data = {
        "pdf_fingerprint": sha256(body).hexdigest(),
        "sheets": proj.sheets,
        "rooms": proj.rooms,
        "ceiling_dims": proj.ceiling_dims,
        "track_lines": [{"length_m": t.length_m, "note": t.note}
                        for t in proj.track_lines],
        "track_fixtures": proj.track_fixtures,
        "track_fixture_models": proj.track_fixture_models,
        "vent_grilles": proj.vent_grilles,
        "diffusers": proj.diffusers,
        "chandeliers": proj.chandeliers,
        "spots": proj.spots,
        "points": proj.points,
        "has_shadow_profile": proj.has_shadow_profile,
        "has_niches": proj.has_niches,
        "has_insulation": proj.has_insulation,
        "bras": proj.bras,
        "color_legend": proj.color_legend,
        "flooring_zones": proj.flooring_zones,
        "height_m": proj.height_m,
        "wall_corners": proj.wall_corners,
        "doors": proj.doors,
        "hidden_door_lengths_m": proj.hidden_door_lengths_m,
        "door_specification_pages": proj.door_specification_pages,
        "finish_type": "paint",
        "notes": proj.notes,
    }
    return data


def make_calculation(project: dict) -> dict:
    """Расчёт по проекту: ведомость работ и итог."""
    smeta = load_smeta()
    res = calculate(project, smeta)
    return result_to_dict(res)


def make_report(project: dict) -> bytes:
    with TemporaryDirectory() as folder:
        pdf = Path(folder) / "report.pdf"
        png = Path(folder) / "outline.png"
        with DRAW_LOCK:
            draw_outline(project, png)
        build_report(make_calculation(project), project, pdf, png)
        return pdf.read_bytes()


MEASUREMENT_FIELDS = (
    "vertical_corner_length_m", "niche_edge_length_m", "gkl_window_joint_length_m",
)


def with_measurements(project: dict, values: dict) -> dict:
    def length(value, minimum=0):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < minimum):
            raise ValueError("Укажите допустимые длины в метрах.")
        return value

    updated = dict(project)
    for key in MEASUREMENT_FIELDS:
        value = values.get(key)
        updated[key] = None if value is None else length(value)
    doors = values.get("hidden_door_lengths_m")
    if doors is not None and not isinstance(doors, list):
        raise ValueError("Укажите высоты скрытых дверей.")
    updated["hidden_door_lengths_m"] = project.get("hidden_door_lengths_m") if doors is None else [
        length(value, 0.01) for value in doors
    ]
    if doors is not None and doors != project.get("hidden_door_lengths_m"):
        updated["door_specification_pages"] = []
    return updated


class Handler(BaseHTTPRequestHandler):
    server_version = "GKLCalcV2"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, payload: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, code: int, data: dict) -> None:
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path == "/health":
            self._json(200, {"status": "ok"})
        elif path in ("/", "/index_v2.html"):
            self._send(200, (ROOT / "index_v2.html").read_bytes(), "text/html; charset=utf-8")
        elif path in ("/xlsx.full.min.js", "/corner_workbook.js"):
            self._send(200, (ROOT / path[1:]).read_bytes(), "text/javascript; charset=utf-8")
        else:
            self._json(404, {"error": "Страница не найдена."})

    def setup(self) -> None:
        self.request.settimeout(45)
        super().setup()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        limit = 80 * 1024 * 1024 if path == "/api/calculate-v2" else 32 * 1024
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "Некорректный размер запроса."})
            return
        if not 0 <= length <= limit:
            self._json(413, {"error": "Размер PDF превышает 80 МБ."})
            return
        parsing = path == "/api/calculate-v2"
        if parsing and not UPLOAD_SLOT.acquire(blocking=False):
            self._json(503, {"error": "Калькулятор обрабатывает другой PDF. Повторите через несколько секунд."})
            return
        try:
            body = self.rfile.read(length)
            if path == "/api/calculate-v2":
                if not body:
                    self._json(400, {"error": "Не загружен PDF-файл."})
                    return
                if not body.startswith(b"%PDF-"):
                    self._json(400, {"error": "Загрузите файл PDF."})
                    return
                project = read_pdf_bytes(body)
                calc = make_calculation(project)
                project_id = uuid4().hex
                with PROJECT_LOCK:
                    if len(PROJECTS) >= 24:
                        PROJECTS.pop(next(iter(PROJECTS)))
                    PROJECTS[project_id] = project
                calc["project_id"] = project_id
                calc["project"] = project
                self._json(200, calc)
                return
            if path == "/api/recalculate-v2":
                data = json.loads(body)
                project_id = data.get("project_id")
                if not isinstance(project_id, str) or project_id not in PROJECTS:
                    self._json(404, {"error": "Загрузите PDF повторно."})
                    return
                project = with_measurements(PROJECTS[project_id], data)
                calc = make_calculation(project)
                PROJECTS[project_id] = project
                self._json(200, {**calc, "project": project, "project_id": project_id})
                return
            if path == "/api/report-v2":
                data = json.loads(body)
                project = PROJECTS.get(data.get("project_id"))
                if project is None:
                    self._json(404, {"error": "Загрузите PDF повторно."})
                    return
                self._send(200, make_report(project), "application/pdf")
                return
        except Exception as error:
            self.log_error("Calculation failed: %s", type(error).__name__)
            message = str(error) if isinstance(error, ValueError) else "Не удалось обработать запрос."
            self._json(400, {"error": message})
            return
        finally:
            if parsing:
                UPLOAD_SLOT.release()
        self._json(404, {"error": "Неизвестный запрос."})


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8766"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Калькулятор v2: http://{host}:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
