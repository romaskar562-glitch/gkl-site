# -*- coding: utf-8 -*-
"""Черновой расчёт одноуровневого потолка ГКЛ и скрипт модели SketchUp.

Правила этой версии, согласованные в переписке и явно помеченные допущения:
- шаг профилей 400 x 400 мм, один уровень, соединитель «краб»;
- нониус-подвес по несущему профилю с шагом 1000 мм;
- два слоя ГКЛ 12,5 мм, лист 1200 x 2500 мм;
- периметр — теневой профиль 3000 мм, марку выбирает клиент;
- к расходуемым материалам +10 %, готовые изделия в этот расчёт не входят;
- крайний профиль не ставится ближе 200 мм к стене.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, asdict

STEP_MM = 400
HANGER_STEP_MM = 1000
EDGE_CLEAR_MM = 200
PROFILE_STOCK_MM = 4000
SHADOW_STOCK_M = 3.0
SHEET_W_MM = 1200
SHEET_L_MM = 2500
SHEET_AREA_M2 = 1.2 * 2.5
LAYERS = 2
WASTE = 1.10
SCREW_PITCH_MM = 170

# Площади, которые на листах «Анненки» повторяет экспликация помещений.
EXPLICATION_M2 = {5.0, 10.0, 10.2, 17.6, 22.2, 2.8, 7.3, 37.8}

AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*м\s*2", re.IGNORECASE)
DIM_RE = re.compile(r"(?<!\d)(\d{1,2})\s?(\d{3})(?!\d)")


def _ceil_div(value: float, step: float) -> int:
    if value <= 0:
        return 0
    return int(math.ceil(value / step - 1e-9))


def interior_axes(length_mm: float, step: int = STEP_MM) -> list[float]:
    positions: list[float] = []
    cursor = float(step)
    limit = length_mm - EDGE_CLEAR_MM
    while cursor <= limit + 1e-6:
        positions.append(cursor)
        cursor += step
    return positions


def hanger_count(span_mm: float) -> int:
    if span_mm <= HANGER_STEP_MM:
        return 1
    return int((span_mm - 1) // HANGER_STEP_MM)


def sheet_count_layout(width_mm: float, depth_mm: float, layers: int = LAYERS) -> int:
    def pack(side_a: float, side_b: float, sheet_a: float, sheet_b: float) -> int:
        return _ceil_div(side_a, sheet_a) * _ceil_div(side_b, sheet_b)

    one = min(
        pack(width_mm, depth_mm, SHEET_W_MM, SHEET_L_MM),
        pack(width_mm, depth_mm, SHEET_L_MM, SHEET_W_MM),
    )
    return one * layers


def sticks_for_runs(lengths_mm: list[float], stock_mm: int) -> tuple[int, int]:
    sticks = 0
    connectors = 0
    for length in lengths_mm:
        count = _ceil_div(length, stock_mm)
        sticks += count
        connectors += max(0, count - 1)
    return sticks, connectors


def _order_count(net: float, waste: float = WASTE) -> int:
    if net <= 0:
        return 0
    return int(math.ceil(net * waste - 1e-9))


@dataclass
class ZoneInput:
    name: str
    width_m: float
    length_m: float
    pdf_area_m2: float | None = None
    page: int | None = None


def calculate_zone(zone: ZoneInput) -> dict:
    width_mm = zone.width_m * 1000.0
    depth_mm = zone.length_m * 1000.0
    short, long_side = (width_mm, depth_mm) if width_mm <= depth_mm else (depth_mm, width_mm)

    carrier_positions = interior_axes(long_side)
    cross_positions = interior_axes(short)
    carrier_lengths = [short] * len(carrier_positions)
    cross_lengths = [long_side] * len(cross_positions)
    all_lengths = carrier_lengths + cross_lengths

    net_profile_m = sum(all_lengths) / 1000.0
    cut_sticks, splices = sticks_for_runs(all_lengths, PROFILE_STOCK_MM)
    waste_profile_m = net_profile_m * WASTE
    sticks_by_meters = _ceil_div(waste_profile_m, PROFILE_STOCK_MM / 1000.0)
    profile_sticks = max(cut_sticks, sticks_by_meters)

    hangers_net = hanger_count(short) * len(carrier_positions)
    crabs_net = len(carrier_positions) * len(cross_positions)

    sides_m = [zone.width_m, zone.length_m, zone.width_m, zone.length_m]
    shadow_sticks = 0
    shadow_joints = 0
    for side in sides_m:
        count = _ceil_div(side, SHADOW_STOCK_M)
        shadow_sticks += count
        shadow_joints += max(0, count - 1)
    perimeter_m = sum(sides_m)
    shadow_sticks = max(shadow_sticks, _ceil_div(perimeter_m * WASTE, SHADOW_STOCK_M))

    area = zone.width_m * zone.length_m
    sheets_layout = sheet_count_layout(width_mm, depth_mm)
    sheets_by_area = _ceil_div(area * LAYERS * WASTE, SHEET_AREA_M2)
    sheets = max(sheets_layout, sheets_by_area)

    screws_net = 0
    for length in all_lengths:
        screws_net += int(length // SCREW_PITCH_MM) + 1
    screws_net *= LAYERS

    warnings = [
        "Прямоугольник заменяет реальный контур. Ниши, борта и закладные в площадь не входят.",
        "Саморезы посчитаны шагом 170 мм по оси каждого профиля на каждом слое. Это черновая геометрическая норма, её сверяет Георгий с картой двухслойного потолка.",
    ]
    if zone.pdf_area_m2 is not None and abs(area - zone.pdf_area_m2) > 0.15:
        warnings.append(
            f"Площадь прямоугольника {area:.2f} м² отличается от подписи на листе {zone.pdf_area_m2:.2f} м²."
        )
    if long_side > PROFILE_STOCK_MM:
        warnings.append("Есть прогоны длиннее 4 м: в ведомости стоят удлинители профиля.")

    def line(name: str, unit: str, net: float, order, note: str, waste: bool = True) -> dict:
        return {
            "name": name,
            "unit": unit,
            "net": round(net, 2) if isinstance(net, float) else net,
            "order": order,
            "waste": "10%" if waste else "без запаса",
            "note": note,
        }

    items = [
        line(
            "Профиль потолочный ПП 60×27",
            "м",
            round(net_profile_m, 2),
            round(waste_profile_m, 2),
            "Несущие идут по короткой стороне, шаг 400 мм. Поперечные — по длинной.",
        ),
        line(
            "Профиль ПП 60×27, хлыст 4 м",
            "шт.",
            cut_sticks,
            profile_sticks,
            "Заказ — большее из раскроя и метража с запасом 10 %.",
        ),
        line(
            "Удлинитель профиля ПП 60×27",
            "шт.",
            splices,
            _order_count(splices),
            "Только там, где прогон длиннее 4 м.",
        ),
        line(
            "Соединитель одноуровневый «краб»",
            "шт.",
            crabs_net,
            _order_count(crabs_net),
            "Каждое пересечение несущего и поперечного профиля.",
        ),
        line(
            "Подвес нониус, комплект",
            "шт.",
            hangers_net,
            _order_count(hangers_net),
            "Шаг 1 м по несущему профилю. Крайний подвес не дальше 1 м от стены.",
        ),
        line(
            "Анкер или дюбель в перекрытие",
            "шт.",
            hangers_net,
            _order_count(hangers_net),
            "По одному на подвес. Тип крепления зависит от перекрытия.",
        ),
        line(
            "Теневой профиль потолка, хлыст 3 м",
            "шт.",
            _ceil_div(perimeter_m, SHADOW_STOCK_M),
            shadow_sticks,
            "KRAAB или LACONISTIQ — выбор клиента. Модель узла пока не различается.",
        ),
        line(
            "Угол теневого профиля",
            "шт.",
            4,
            _order_count(4),
            "Четыре угла прямоугольной зоны.",
        ),
        line(
            "Прямой соединитель теневого профиля",
            "шт.",
            shadow_joints,
            _order_count(shadow_joints),
            "Стык хлыстов на стороне длиннее 3 м.",
        ),
        line(
            "ГКЛ 12,5 мм 1200×2500, два слоя",
            "лист",
            sheets_layout,
            sheets,
            "В заказ взято большее: раскладка листов или площадь с запасом 10 %.",
        ),
        line(
            "Саморез по металлу, 1-й слой",
            "шт.",
            screws_net // LAYERS,
            _order_count(screws_net // LAYERS),
            "Черновая норма. Длину шурупа для Аквамарина утверждает Георгий.",
        ),
        line(
            "Саморез по металлу, 2-й слой",
            "шт.",
            screws_net // LAYERS,
            _order_count(screws_net // LAYERS),
            "Черновая норма, шаг 170 мм.",
        ),
    ]
    return {
        "name": zone.name,
        "page": zone.page,
        "width_m": round(zone.width_m, 3),
        "length_m": round(zone.length_m, 3),
        "area_m2": round(area, 2),
        "pdf_area_m2": zone.pdf_area_m2,
        "carriers": len(carrier_positions),
        "crosses": len(cross_positions),
        "warnings": warnings,
        "items": items,
        "geometry": {
            "short_mm": round(short, 1),
            "long_mm": round(long_side, 1),
            "carrier_positions_mm": [round(v, 1) for v in carrier_positions],
            "cross_positions_mm": [round(v, 1) for v in cross_positions],
            "hanger_step_mm": HANGER_STEP_MM,
            "hanger_count_per_carrier": hanger_count(short),
        },
    }


def combine(zones: list[dict]) -> list[dict]:
    bucket: dict[tuple, dict] = {}
    for zone in zones:
        for item in zone["items"]:
            key = (item["name"], item["unit"])
            row = bucket.setdefault(
                key,
                {
                    "name": item["name"],
                    "unit": item["unit"],
                    "net": 0,
                    "order": 0,
                    "waste": item["waste"],
                    "note": item["note"],
                },
            )
            row["net"] = round(row["net"] + float(item["net"]), 2)
            row["order"] = round(row["order"] + float(item["order"]), 2)
    return list(bucket.values())


def extract_page_areas(text: str, page: int) -> dict:
    areas = []
    seen = []
    for match in AREA_RE.finditer(text or ""):
        value = round(float(match.group(1).replace(",", ".")), 2)
        seen.append(value)
        areas.append(
            {
                "page": page,
                "m2": value,
                "explication": value in EXPLICATION_M2,
            }
        )
    dims = []
    for match in DIM_RE.finditer(text or ""):
        number = int(match.group(1) + match.group(2))
        if 300 <= number <= 20000 and number not in dims:
            dims.append(number)
    ceiling_hint = bool(re.search(r"потол|тенев", text or "", re.IGNORECASE))
    return {
        "page": page,
        "ceiling_hint": ceiling_hint,
        "areas": areas,
        "dimensions_mm": dims[:48],
    }


def ruby_for_zones(zones: list[dict], save_path: str) -> str:
    """Скрипт, который SketchUp выполняет и сохраняет модель каркаса."""
    save = save_path.replace("\\", "/")
    chunks = []
    cursor_x = 0.0
    for zone in zones:
        geo = zone["geometry"]
        short = geo["short_mm"]
        long_side = geo["long_mm"]
        name = str(zone["name"]).replace("\\", " ").replace('"', "'")[:80]
        carriers = []
        hangers_each = geo["hanger_count_per_carrier"]
        span = short
        for index, pos in enumerate(geo["carrier_positions_mm"]):
            hangers = []
            if hangers_each <= 1:
                hangers = [span / 2.0]
            else:
                step = span / (hangers_each + 1)
                hangers = [step * (n + 1) for n in range(hangers_each)]
            carriers.append({"y": pos, "hangers": [round(h, 1) for h in hangers]})
        chunks.append(
            {
                "name": name,
                "origin_x": round(cursor_x, 1),
                "short": short,
                "long": long_side,
                "carriers": carriers,
                "crosses": geo["cross_positions_mm"],
            }
        )
        cursor_x += long_side + 1500.0

    # Данные только числа и уже очищенные имена.
    import json

    payload = json.dumps(chunks, ensure_ascii=False)
    done = save + ".done"
    error = save + ".error"
    return f"""# encoding: UTF-8
require 'json'
module GklPremiumFrame
  def self.build
    data = JSON.parse({payload!r})
    model = Sketchup.active_model
    model.start_operation('GKL Premium frame', true)
    ents = model.active_entities
    data.each do |zone|
      group = ents.add_group
      group.name = zone['name']
      ge = group.entities
      ox = zone['origin_x'].to_f.mm
      short_len = zone['short'].to_f
      long_len = zone['long'].to_f
      zone['carriers'].each do |carrier|
        y = carrier['y'].to_f.mm
        ge.add_edges(Geom::Point3d.new(ox, y, 0), Geom::Point3d.new(ox + short_len.mm, y, 0))
        carrier['hangers'].each do |h|
          x = ox + h.to_f.mm
          ge.add_edges(Geom::Point3d.new(x, y, 0), Geom::Point3d.new(x, y, 200.mm))
        end
      end
      zone['crosses'].each do |x|
        px = ox + x.to_f.mm
        ge.add_edges(Geom::Point3d.new(px, 0, 0), Geom::Point3d.new(px, long_len.mm, 0))
      end
      ge.add_edges(
        Geom::Point3d.new(ox, 0, -12.5.mm),
        Geom::Point3d.new(ox + short_len.mm, 0, -12.5.mm),
        Geom::Point3d.new(ox + short_len.mm, long_len.mm, -12.5.mm),
        Geom::Point3d.new(ox, long_len.mm, -12.5.mm),
        Geom::Point3d.new(ox, 0, -12.5.mm)
      )
    end
    model.commit_operation
    model.active_view.zoom_extents if model.active_view
    model.save({save!r})
    File.write({done!r}, 'ok')
    Sketchup.quit if Sketchup.respond_to?(:quit)
  end
end
UI.start_timer(3.0, false) do
  begin
    GklPremiumFrame.build
  rescue => error
    File.write({error!r}, error.full_message)
  end
end
"""


def to_plain(zone: ZoneInput) -> dict:
    return asdict(zone)
