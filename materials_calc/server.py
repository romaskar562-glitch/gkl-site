# -*- coding: utf-8 -*-
"""Локальный сервер калькулятора материалов и запуска SketchUp."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from pypdf import PdfReader

from engine import ZoneInput, calculate_zone, combine, extract_page_areas, ruby_for_zones

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "sketchup-out"
SKETCHUP = Path(r"C:\Program Files\SketchUp\SketchUp 2027\SketchUp\SketchUp.exe")
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8765"))


def read_pdf(data: bytes) -> dict:
    reader = PdfReader(stream := __import__("io").BytesIO(data))
    del stream
    pages = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        info = extract_page_areas(text, index)
        if info["areas"] or info["ceiling_hint"]:
            pages.append(info)
    return {"pages": pages, "page_count": len(reader.pages)}


def parse_zones(raw_zones: list) -> list[ZoneInput]:
    zones = []
    for index, item in enumerate(raw_zones, start=1):
        if not item.get("include", True):
            continue
        width = float(item["width_m"])
        length = float(item["length_m"])
        if width <= 0.2 or length <= 0.2 or width > 100 or length > 100:
            raise ValueError(f"Зона {index}: укажите длину и ширину в метрах.")
        pdf_area = item.get("pdf_area_m2")
        zones.append(
            ZoneInput(
                name=str(item.get("name") or f"Зона {index}")[:80],
                width_m=width,
                length_m=length,
                pdf_area_m2=float(pdf_area) if pdf_area not in (None, "") else None,
                page=int(item["page"]) if item.get("page") else None,
            )
        )
    if not zones:
        raise ValueError("Отметьте хотя бы одну зону и заполните её размеры.")
    return zones


def sketchup_running() -> bool:
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq SketchUp.exe", "/NH"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "SketchUp.exe" in (result.stdout or "")


def build_in_sketchup(zones: list[dict]) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    skp = OUT / f"karkas-{stamp}.skp"
    script = OUT / f"karkas-{stamp}.rb"
    done = Path(str(skp) + ".done")
    error = Path(str(skp) + ".error")
    for path in (done, error):
        if path.exists():
            path.unlink()
    script.write_text(ruby_for_zones(zones, str(skp)), encoding="utf-8")
    if not SKETCHUP.exists():
        return {
            "started": False,
            "message": "SketchUp 2027 не найден. Скрипт модели сохранён рядом с сайтом.",
            "script": str(script),
        }
    if sketchup_running():
        return {
            "started": False,
            "message": "SketchUp уже открыт. Закройте его и повторите сборку, иначе второй экземпляр не заберёт новый скрипт.",
            "script": str(script),
        }
    subprocess.Popen([str(SKETCHUP), "-RubyStartup", str(script)])
    deadline = time.time() + 90
    while time.time() < deadline:
        if done.exists() or skp.exists():
            return {
                "started": True,
                "message": "SketchUp собрал каркас и сохранил модель.",
                "model": str(skp),
                "script": str(script),
            }
        if error.exists():
            return {
                "started": False,
                "message": error.read_text(encoding="utf-8", errors="replace")[:2000],
                "script": str(script),
            }
        time.sleep(1)
    return {
        "started": True,
        "message": "SketchUp запущен. Если модель ещё не сохранилась, проверьте окно программы: его мог остановить экран приветствия или запрос лицензии.",
        "script": str(script),
        "model": str(skp) if skp.exists() else "",
    }


class Handler(BaseHTTPRequestHandler):
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
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        if path in ("/", ""):
            path = "/материалы-по-проекту.html"
        target = (ROOT / path.lstrip("/")).resolve()
        if not str(target).startswith(str(ROOT)) or not target.is_file():
            self._json(404, {"error": "Файл не найден."})
            return
        kind = "text/html; charset=utf-8"
        if target.suffix == ".js":
            kind = "text/javascript; charset=utf-8"
        elif target.suffix == ".css":
            kind = "text/css; charset=utf-8"
        elif target.suffix == ".skp":
            kind = "application/octet-stream"
        self._send(200, target.read_bytes(), kind)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            if path == "/api/pdf":
                self._json(200, read_pdf(body))
                return
            if path == "/api/calculate":
                data = json.loads(body.decode("utf-8"))
                zones = [calculate_zone(zone) for zone in parse_zones(data.get("zones") or [])]
                result = {
                    "zones": zones,
                    "totals": combine(zones),
                    "assumptions": [
                        "Один уровень, ячейка 400×400 мм, крабы, нониус через 1 м по несущему профилю.",
                        "Два слоя листа 12,5 мм, формат 1200×2500. Запас 10 % на расходные материалы.",
                        "Контур зоны — прямоугольник заданных ширины и длины. Кривые, ниши и коммуникации сюда не входят.",
                        "Перед закупкой ведомость проверяет Георгий.",
                    ],
                }
                if data.get("build_sketchup"):
                    result["sketchup"] = build_in_sketchup(zones)
                self._json(200, result)
                return
        except Exception as error:
            self._json(400, {"error": str(error)})
            return
        self._json(404, {"error": "Неизвестный запрос."})


def main() -> None:
    os.chdir(ROOT)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/материалы-по-проекту.html"
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
