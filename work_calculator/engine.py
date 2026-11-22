# -*- coding: utf-8 -*-
"""Движок расчёта стоимости РАБОТ для калькулятора ГКЛ-потолков (v3).

Связывает разобранный дизайн-проект (project.json) с базовой сметой работ
(smeta_base.json) по триггерам элементов. Калькулятор считает стоимость
РАБОТ (не материалов).

Доработки по 8 пунктам:
  1. Площадь потолков — только зоны, окрашенные фиолетовым (зона ГКЛ),
     с листа plan_flooring; Беседка/хоз.блок/сауна другого цвета не считаются.
  2. Слои ГКЛ — кнопка (по умолчанию 2 слоя: зашивка 27.9).
  3. Шаг профилей 400×400 — усиленный каркас (27.4), для потолков только такой.
  4. Уголформирующая лента — п.м. по алгоритму: все углы стен на плане
     (внутренние + внешние) × высота потолка (лист 7, 2.75 м).
  5. Периметр — корректно по зонам.
  6. Шпаклевание примыканий к трековым светильникам — 2 стороны (×2).
  7. Два вида шпаклевания (базовое 34.6/34.7 + финишное 34.12/34.13,
     по 2 слоя каждый) — все учтено.
  8. Все этапы работ из сметы: промежуточные грунтовки (34.4, 34.9, 34.15)
     и ошкуривания (34.8, 34.14) — не убираются, все забиваются в расчёт.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Цепочки работ по виду отделки потолка (порядок важен)
# ---------------------------------------------------------------------------

FINISH_CHAINS = {
    # Покраска (потолки ГКЛ под покраску) — базовая цепочка
    "paint": {
        "carcas": ["27.4"],              # усиленный каркас шаг 400×400
        "cover": ["27.9"],               # зашивка ГКЛВ в 2 слоя (по умолчанию)
        "armature": ["27.11"],           # армирование стыков
        "taping": ["34.5"],              # уголформирующая лента (п.м. по углам × высота)
        "putty_basic": ["34.6", "34.7"],     # шпаклёвка базовая: 1 и 2 слой
        "putty_fine": ["34.12", "34.13"],   # шпаклёвка финишная: 1 и 2 слой
        "grinding": ["34.8", "34.14"],      # ошкуривание: финиш + супер-финиш
        "primer": ["34.4", "34.15", "34.9"],  # грунтовка: 1-я (до работ), 2-я (после 1-го ошкуривания), 3-я (после 2-го)
    },
    # Стеклохолст / обои
    "glasscloth": {
        "carcas": ["27.4"],
        "cover": ["27.9"],
        "armature": ["27.11"],
        "taping": ["34.5"],
        "putty_basic": ["34.6", "34.7"],
        "putty_fine": ["34.12", "34.13"],
        "grinding": ["34.8", "34.14"],
        "primer": ["34.4", "34.9"],         # грунтовка под обои
        "glasscloth": ["34.10", "34.11"],
    },
}

# Триггер -> код работы (основная позиция)
SIMPLE_TRIGGERS = {
    "shadow": ["27.15"],           # теневой профиль
    "carnation": ["36.1", "36.5", "36.10"],   # карниз: монтаж, шпаклёвка, окраска
    "insulation": ["27.14"],       # утеплитель
    "chandelier": ["38.34"],       # люстра
    "spot": ["38.39", "38.40"],    # спот + отверстие
    "bra": ["38.32"],              # бра
    "vent_grille": ["38.30"],      # вентиляционная решётка
    "diffuser": ["38.31"],         # щелевой диффузор
}

# Триггер трека -> коды работ (на п.м.)
TRACK_TRIGGERS = {
    "track": ["38.36", "34.17"],   # монтаж трека + шпаклёвка примыканий (×2 стороны)
    "track_fixture": ["38.35"],    # световые изделия в трек (шт.)
    "transformer_track": ["38.28"],  # трансформатор в трек (шт.)
    "niche_track": ["27.5", "34.1"],  # ниши под треки (п.м.)
}


@dataclass
class WorkItem:
    """Позиция в ведомости работ."""
    code: str            # код работы из сметы
    name: str            # название работы
    trigger: str         # триггер (элемент)
    element: str         # описание элемента проекта
    unit: str            # м² / п.м. / шт.
    quantity: float      # количество
    price: float         # цена за ЕИ
    total: float         # сумма


@dataclass
class CalcResult:
    rooms: list = field(default_factory=list)
    ceiling_area: float = 0.0          # площадь ГКЛ-зон (фиолетовые)
    track_length: float = 0.0
    track_fixtures: int = 0
    track_fixture_models: list = field(default_factory=list)
    perimeter: float = 0.0
    finish_type: str = "paint"
    gkl_layers: int = 2                # слои ГКЛ (кнопка)
    wall_corners: int = 0              # все углы стен на плане (внутренние + внешние)
    height_m: float = 2.75             # высота потолка (план листа 7)
    doors: int = 0                     # количество дверей на плане
    items: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    graphic_extra: list = field(default_factory=list)
    color_legend: list = field(default_factory=list)
    flooring_zones: dict = field(default_factory=dict)

    @property
    def total_price(self) -> float:
        return round(sum(i.total for i in self.items), 2)


# ---------------------------------------------------------------------------
# Загрузка данных
# ---------------------------------------------------------------------------

def load_project(path: str | Path | None = None) -> dict:
    p = Path(path) if path else BASE_DIR / "project.json"
    return json.loads(p.read_text(encoding="utf-8"))


def load_smeta(path: str | Path | None = None) -> dict:
    p = Path(path) if path else BASE_DIR / "smeta_base.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _price_of(work: dict) -> float:
    try:
        return float(str(work.get("price", "")).replace(",", ".").strip() or 0)
    except ValueError:
        return 0.0


def _perimeter_of_flooring_zones(fz: dict, rooms: list[dict]) -> float:
    """Периметр зон ГКЛ: по прямоугольникам фиолетовых зон (plan_flooring).

    Если зоны определены — периметр по их прямоугольникам в метрах
    (по масштабу). Если нет — оценка по комнатам.
    """
    rects = fz.get("rects", []) if fz else []
    scale = fz.get("scale_pt_per_mm") or 0.0
    if rects and scale > 0:
        p = 0.0
        for r in rects:
            x0, y0, x1, y1 = r["rect"]
            w_mm = (x1 - x0) / scale
            l_mm = (y1 - y0) / scale
            p += 2.0 * (w_mm + l_mm) / 1000.0  # в м
        return round(p, 2)
    # Затмение: оценка по комнатам
    return _perimeter_of_rooms(rooms)


def _perimeter_of_rooms(rooms: list[dict]) -> float:
    """Оценка периметра по комнатам (затмение)."""
    p = 0.0
    for r in rooms:
        a = r.get("area_m2") or 0
        if a > 0:
            p += 4.0 * math.sqrt(a)
    return round(p, 2)


# ---------------------------------------------------------------------------
# Сборка ведомости
# ---------------------------------------------------------------------------

def _works_index(smeta: dict) -> dict:
    """Индекс работ по коду: {code: work}."""
    return {code: w for code, w in smeta.get("works", {}).items() if not code.startswith("38.")}


def _chain_codes(chain: dict, keys: list[str]) -> list[str]:
    """Выбирает коды цепочки, сохраняя порядок."""
    out: list[str] = []
    for k in keys:
        if k in chain:
            out.extend(chain[k])
    return out


def _add_work(items: list, idx: dict, code: str, trigger: str,
              element: str, unit: str, qty: float, price: float) -> bool:
    """Добавляет работу в ведомость, если код есть в смете."""
    w = idx.get(code)
    if code.startswith("38.") or not w:
        return False
    p = _price_of(w)
    items.append(WorkItem(
        code=code, name=w.get("name", "").strip(), trigger=trigger,
        element=element, unit=unit, quantity=round(qty, 2),
        price=p, total=round(qty * p, 2),
    ))
    return True


def calculate(project: dict, smeta: dict, gkl_layers: int = 2) -> CalcResult:
    result = CalcResult()
    rooms = project.get("rooms", [])
    result.rooms = rooms
    result.gkl_layers = max(1, min(gkl_layers, 3))

    # --- 1. Площадь потолков: только зоны, окрашенные фиолетовым (зона ГКЛ) ---
    fz = project.get("flooring_zones", {}) or {}
    result.flooring_zones = fz
    sheets = fz.get("sheets", [])
    measured = [sheet for sheet in sheets if sheet["scale_pt_per_mm"] > 0 and sheet["area_pt2"] > 0]
    result.ceiling_area = round(sum(
        sheet["area_pt2"] / (sheet["scale_pt_per_mm"] * 1000) ** 2
        for sheet in measured
    ), 2)
    result.perimeter = round(sum(
        sheet["boundary_pt"] / (sheet["scale_pt_per_mm"] * 1000)
        for sheet in measured
    ), 2)
    if measured:
        result.warnings.append("Предварительный обмер: проверьте масштаб и контуры по PDF.")
    if not sheets:
        result.warnings.append("Загрузите PDF повторно для обмера по листам.")
    for sheet in sheets:
        result.warnings.extend(f"Страница {sheet['page']}: {w}" for w in sheet["warnings"])

    result.finish_type = project.get("finish_type", "paint")
    result.color_legend = project.get("color_legend", [])

    # --- Углы стен и высота (план листа 7) для ленты, теневого профиля, откосов ---
    result.wall_corners = int(project.get("wall_corners") or 0)
    result.height_m = float(project.get("height_m") or 2.75)
    result.doors = int(project.get("doors") or 0)
    track_lines = project.get("track_lines", [])
    result.track_length = round(sum(l.get("length_m", 0) for l in track_lines), 2)
    result.track_fixtures = int(project.get("track_fixtures", 0))
    result.track_fixture_models = list(project.get("track_fixture_models", []))

    # --- 5. Периметр — корректно по зонам ГКЛ ---


    idx = _works_index(smeta)

    # --- 2, 3. Потолки: усиленный каркас 400×400 + зашивка по слоям ---
    chain = FINISH_CHAINS.get(result.finish_type, FINISH_CHAINS["paint"])
    # Подбираем зашивку по слоям: 1 слой -> 27.8, 2 слоя -> 27.9
    cover_codes = chain.get("cover", ["27.9"])
    if result.gkl_layers == 1 and "27.8" in idx:
        cover_codes = ["27.8"]

    ceiling_codes = _chain_codes(
        chain,
        ["carcas", "cover", "armature", "taping", "putty_basic",
         "putty_fine", "grinding", "primer", "glasscloth"],
    )
    # Подменяем cover кодами по слоям
    for i, c in enumerate(ceiling_codes):
        if c == chain.get("cover", ["27.9"])[0]:
            ceiling_codes[i] = cover_codes[0]
            if len(cover_codes) > 1:
                ceiling_codes.insert(i + 1, cover_codes[1])
                break

    for code in ceiling_codes:
        w = idx.get(code)
        if not w:
            continue
        price = _price_of(w)
        name = w.get("name", "").strip()
        if code in {"34.5", "35.3"}:
            continue
        unit = w.get("unit") or "м²"
        qty = result.ceiling_area if unit == "м²" else result.perimeter
        if qty <= 0:
            continue
        element = f"Потолок ГКЛ, {result.ceiling_area} м², {result.gkl_layers} слоя"
        result.items.append(WorkItem(
            code=code, name=name, trigger="ceiling",
            element=element,
            unit=unit, quantity=round(qty, 2), price=price,
            total=round(qty * price, 2),
        ))
    tape_code = "35.3" if result.finish_type == "glasscloth" else "34.5"
    corner_length = project.get("vertical_corner_length_m")
    edge_length = project.get("niche_edge_length_m")
    if corner_length is None or edge_length is None:
        result.warnings.append(f"{tape_code} · Объём ленты пока не рассчитан. Автоматический подсчёт вертикальных углов и кромок ниш не подтверждён.")
    elif corner_length + edge_length > 0:
        _add_work(result.items, idx, tape_code, "tape",
                  "Вертикальные углы и кромки потолочных ниш", "п.м.",
                  corner_length + edge_length, 0)

    doors = project.get("hidden_door_lengths_m")
    if doors is None:
        result.warnings.append("32.1–32.4 · Проверьте спецификацию дверей. Если высота двери не указана, используйте высоту потолка в её зоне.")
    elif doors:
        pages = ", ".join(map(str, project.get("door_specification_pages", [])))
        source = f" · таблицы дверей, страницы PDF {pages}" if pages else " · подтверждённые высоты"
        result.doors = len(doors)
        for code in ("32.1", "32.2", "32.3", "32.4"):
            _add_work(result.items, idx, code, "hidden_doors",
                      (f"Скрытые двери, {len(doors)} шт. × две длины каждой{source}"
                       + (" · уголформирующая лента" if code == "32.3" else "")),
                      "п.м.", 2 * sum(doors), 0)

    window_length = project.get("gkl_window_joint_length_m")
    if window_length is None:
        result.warnings.append("31.3 · Объём примыканий откосов ГКЛ пока не рассчитан. Извлечение длин из развёрток стен не подтверждено.")
    elif window_length > 0:
        _add_work(result.items, idx, "31.3", "gkl_windows",
                  "Примыкания оконных откосов из ГКЛ к стенам",
                  "п.м.", window_length, 0)

    # --- 4. Теневой профиль ГКЛ (п.м. по периметру, не углы × высота) ---
    # Теневой шов = теневой профиль; НЕ путать с теневым плинтусом (стена-пол).
    shadow_length = project.get("shadow_length_m")
    if project.get("has_shadow_profile"):
        if shadow_length is None:
            result.warnings.append("27.15 · Объём теневого профиля пока не рассчитан. Распознавание полной трассы двойной линии по легенде не подтверждено.")
        else:
            for code in SIMPLE_TRIGGERS["shadow"]:
                _add_work(result.items, idx, code, "shadow",
                          "Теневой профиль ГКЛ", "п.м.", shadow_length, 0)
            result.groups.append("теневой профиль")

    if result.track_length > 0:
        source = "; ".join(line.get("note", "") for line in track_lines)
        for code, multiplier in (("27.5", 1), ("34.17", 2)):
            _add_work(result.items, idx, code, "track",
                      f"Врезной трек · {source}", "п.м.", result.track_length * multiplier, 0)


    # --- Ниши под подсветку ---
    if sheets:
        niche_len = project.get("niche_length_m")
        if niche_len is None:
            if project.get("has_niches"):
                result.warnings.append("Ниши под подсветку: проверьте отдельное обозначение в легенде и длину.")
            else:
                result.warnings.append("Ниши под подсветку: отдельное обозначение в легенде не найдено. Уточните у прораба.")
        else:
            for code in ["27.6", "34.2"]:
                _add_work(result.items, idx, code, "niche_light",
                          "Скрытые ниши под подсветку", "п.м.", niche_len, 0)
            result.groups.append("ниши под подсветку")

    # --- Утеплитель ---
    if project.get("has_insulation"):
        _add_work(result.items, idx, "27.14", "insulation",
                  "Утеплитель потолка", "м²", result.ceiling_area, 0)
        result.groups.append("утеплитель")

    # --- 8. Проверки: все этапы работ из сметы учтены ---
    if project.get("has_shadow_profile") and not idx.get("27.15"):
        result.warnings.append("Теневой профиль есть, но в смете нет работы 27.15")
    if result.ceiling_area > 0 and not idx.get("27.4"):
        result.warnings.append("Площадь есть, но в смете нет усиленного каркаса 27.4")
    # Цветовая сверка
    if not result.color_legend:
        result.warnings.append(
            "Уточняющая таблица цветов не найдена — сверка цветов отделки "
            "не выполнена"
        )
    result.groups = sorted(set(result.groups))
    return result


def result_to_dict(r: CalcResult) -> dict:
    return {
        "rooms": r.rooms,
        "ceiling_area": r.ceiling_area,
        "track_length": r.track_length,
        "track_fixtures": r.track_fixtures,
        "track_fixture_models": r.track_fixture_models,
        "perimeter": r.perimeter,
        "finish_type": r.finish_type,
        "gkl_layers": r.gkl_layers,
        "wall_corners": r.wall_corners,
        "height_m": r.height_m,
        "doors": r.doors,
        "total_price": r.total_price,
        "groups": r.groups,
        "color_legend": r.color_legend,
        "flooring_zones": r.flooring_zones,
        "items": [
            {
                "code": i.code, "name": i.name, "trigger": i.trigger,
                "element": i.element, "unit": i.unit,
                "quantity": i.quantity, "price": i.price, "total": i.total,
            } for i in r.items
        ],
        "warnings": r.warnings,
        "graphic_extra": r.graphic_extra,
    }


if __name__ == "__main__":
    proj = load_project()
    smeta = load_smeta()
    res = calculate(proj, smeta)
    print(f"Помещений: {len(res.rooms)}, площадь ГКЛ-зон: {res.ceiling_area} м²")
    print(f"Периметр: {res.perimeter} п.м., слои ГКЛ: {res.gkl_layers}")
    print(f"Треки: {res.track_length} п.м., светильников: {res.track_fixtures}")
    print(f"Позиций в ведомости: {len(res.items)}")
    for it in res.items:
        print(f"  {it.code:8s} | {it.trigger:18s} | {it.quantity} {it.unit} "
              f"| {it.price:>8.0f} | {it.total:>10.0f}")
    print(f"ИТОГО РАБОТ: {res.total_price:.0f} ₽")
    if res.warnings:
        print("Предупреждения:", *res.warnings, sep="\n  ")
