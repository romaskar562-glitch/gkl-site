# -*- coding: utf-8 -*-
"""Парсер дизайн-проекта PDF: классификация листов, дедубликация,
определение элементов (треки, ниши, плинтусы, точки).

Вход: PDF-файл. Выход: структура проекта для расчёта.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf
from io import BytesIO
from shapely.geometry import Polygon, box, mapping
from shapely.ops import unary_union
from pypdf import PdfReader


# ----------------------------------------------------------------------------
# Классификация листов по заголовку (последняя строка текста)
# ----------------------------------------------------------------------------

SHEET_KINDS = {
    "plan_ceilings": "План потолков",
    "track_lights": "План привязки осветительных приборов",
    "plan_lighting_control": "План управления осветительными приборами",
    "plan_sockets": "План розеток",
    "plan_electric": "Совмещённый план электрооборудования",
    "plan_development": "План разверток",
    "development": "Развертки",
    "plan_partitions": "План монтируемых перегородок",
    "plan_flooring": "План укладки напольных покрытий",
    "plan_doors": "План дверей",
    "plan_plumbing": "План точек воды",
    "plan_general": "Планировочное решение",
    "other": "Другой лист",
}


def classify_sheet(text: str) -> str:
    """Определяет вид работ листа по заголовку (последняя строка)."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    tail = next((line for line in reversed(lines) if not line.isdigit()), "")
    t = tail.lower()
    if "план потолков" in t:
        return "plan_ceilings"
    if "привязки осветительных" in t or "осветительных приборов" in t:
        return "track_lights"
    if "управления осветительными" in t:
        return "plan_lighting_control"
    if "розеток" in t:
        return "plan_sockets"
    if "совмещённый план электро" in t or "электрооборудовани" in t:
        return "plan_electric"
    if "план разверток" in t:
        return "plan_development"
    if "развертки" in t:
        return "development"
    if "перегородок" in t:
        return "plan_partitions"
    if "напольных покрытий" in t:
        return "plan_flooring"
    if "план дверей" in t:
        return "plan_doors"
    if "точек воды" in t or "топления" in t:
        return "plan_plumbing"
    if "планировочное решение" in t:
        return "plan_general"
    return "other"


# ----------------------------------------------------------------------------
# Извлечение данных
# ----------------------------------------------------------------------------

AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*м\s*2", re.IGNORECASE)


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def extract_areas(text: str) -> list[float]:
    return [_to_float(m) for m in AREA_RE.findall(text)]


# Слова-аннотации чертежа, которые не могут быть названиями помещений
ANNOTATION_WORDS = {
    "бетонное перекрытие", "перекрытие", "бетон", "уровень", "отметка",
    "сечение", "теневой", "потолок гкл", "реечный", "потолок", "потолка",
    "установка", "устройство", "ниша", "закладной", "шкафа", "шкаф",
    "душевой", "перегородки", "подсветки", "подсветку", "отверстие",
    "монтаж", "портьерной", "чистового", "встраиваемого", "встраиваемая",
    "стеклянной", "магнитный", "шинопровод", "регулятор", "аквастраж",
    "экспликация", "примечания", "примечание", "лист", "листо",
    "листов", "таблиц", "наименование", "помещения", "м2",
}


# Название материала в легенде -> ключ для поиска.
# Порядок важен: более точные ключи (закладн) идут перед общими (гкл),
# чтобы «Установка закладной за ГКЛ» не разрешалась как «потолок ГКЛ».
LEGEND_KEYS = {
    "теневой шов": "shadow",
    "тенев": "shadow",
    "закладн": "carcass_insert",
    "шкаф": "cabinet",
    "ниша для встраиваемой подсветки": "niche_light",
    "ниша": "niche",
    "реечный потолок": "slat_ceiling",
    "реечн": "slat_ceiling",
    "потолок гкл": "ceiling",
    "гкл": "ceiling",
}


def parse_color_legend(page) -> list[dict]:
    """Считывает уточняющую таблицу (легенду) цветов на листе.

    Правило: цвет в легенде указывает на вид отделочного материала.
    Изменение цвета линий/деталей = другой материал или вид отделки —
    этот момент нельзя игнорировать, чтобы не было путаницы.
    Возвращает список: [{name, kind, color_rgb, rect}].
    """
    legend: list[dict] = []
    # 1) Собираем текстовые спанс с названиями материалов
    spans = []
    for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES).get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span["text"].strip()
                if not txt:
                    continue
                low = txt.lower()
                if low.startswith("гкл,") or low.startswith("зашивается гкл"):
                    continue
                kind = None
                for key, k in LEGEND_KEYS.items():
                    if key in low:
                        kind = k
                        break
                if kind:
                    spans.append((txt, pymupdf.Rect(span["bbox"]), kind))
    if not spans:
        return legend

    # 2) Собираем цветные маркеры (заливки и strokes) на листе
    markers = []
    for path in page.get_drawings():
        r = path["rect"]
        if r.width > 45 or r.height > 25:
            continue
        color = path.get("fill") or path.get("color")
        if color and color not in ((1.0, 1.0, 1.0), (0.0, 0.0, 0.0),
                                   (0.416, 0.416, 0.416)):
            markers.append((tuple(round(x, 3) for x in color), r))

    # 3) Сопоставляем каждый пункт легенды с ближайшим цветным маркером
    for txt, rect, kind in spans:
        best, best_d, best_rect = None, 1e9, None
        for color, mrect in markers:
            # расстояние между ближайшими точками bbox (без Rect.distance)
            dx = max(mrect.x0 - rect.x1, rect.x0 - mrect.x1, 0.0)
            dy = max(mrect.y0 - rect.y1, rect.y0 - mrect.y1, 0.0)
            d = (dx * dx + dy * dy) ** 0.5
            if d < best_d:
                best_d, best, best_rect = d, color, mrect
        if best is not None and best_d <= 20:
            legend.append({
                "name": txt,
                "kind": kind,
                "color_rgb": list(best),
                "distance": round(best_d, 1),
                "marker_rect": list(best_rect),
            })
    return legend


# --- Цвета из легенды листа, не фиксированы ---------------------------------
# Парсер читает цвета ГКЛ/ниш из легенды листа. Здесь — универсальный
# фильтр: светлый сине-фиолетовый оттенок (b >= r > g), исключая фон.

def _is_ceiling_fill(fill) -> bool:
    """Фиолетовый цвет ГКЛ: сине-фиолетовый (b >= r > g).

    Векторный цвет в диапазоне 0..1. Универсально ловит и светлый
    растровый (228,216,246) и векторный (0.584,0.384,0.859) оттенки.
    """
    if not fill:
        return False
    r, g, b = fill[0], fill[1], fill[2]
    return b >= r and r > g and (b - g) >= 0.03


def _is_niche_fill(fill) -> bool:
    """Зелёный цвет ниши: g > r, g > b (0..1)."""
    if not fill:
        return False
    r, g, b = fill[0], fill[1], fill[2]
    return g > r and g > b and (g - r) >= 0.03


def _fill_geometry(path):
    """Read straight PDF subpaths and preserve holes in even-odd fills."""
    rings = []
    points = []

    def flush():
        if len(points) >= 3:
            polygon = Polygon(points)
            if polygon.is_valid and polygon.area > 0:
                rings.append(polygon)
        points.clear()

    for cmd, *args in path["items"]:
        if cmd == "l":
            start, end = (tuple(p) for p in args)
            if points and points[-1] != start:
                flush()
            if not points:
                points.append(start)
            points.append(end)
            if points[0] == end:
                flush()
        elif cmd == "re":
            flush()
            rings.append(box(*args[0]))
        elif cmd == "qu":
            flush()
            q = args[0]
            rings.append(Polygon([tuple(q.ul), tuple(q.ur), tuple(q.lr), tuple(q.ll)]))
        else:
            return None
    flush()
    if path.get("even_odd"):
        from functools import reduce
        from shapely.geometry import GeometryCollection
        return reduce(lambda a, b: a.symmetric_difference(b), rings, GeometryCollection())
    if any(a.contains(b) for i, a in enumerate(rings) for j, b in enumerate(rings) if i != j and not a.equals(b)):
        return None
    return unary_union(rings)


def propose_rectangular_corners(page, shapes, openings, page_number):
    """Suggest envelope corners for isolated rectangular ceiling zones."""
    import base64
    from shapely.geometry import Point

    blocks = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    lines = []
    for block in blocks["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line.get("spans", [])).strip()
            lines.append((text, pymupdf.Rect(line["bbox"])))
    proposals = []
    for polygon in shapes:
        if polygon.geom_type != "Polygon" or polygon.interiors:
            continue
        x0, y0, x1, y1 = polygon.bounds
        rect_area = (x1 - x0) * (y1 - y0)
        if rect_area < 500 or polygon.area / rect_area < 0.995:
            continue
        if any(polygon.intersection(opening).area > 1 for opening in openings):
            continue
        if any(other is not polygon and polygon.intersection(other).area > 1 for other in shapes):
            continue
        local = [text for text, bounds in lines
                 if polygon.contains(Point((bounds.x0 + bounds.x1) / 2,
                                           (bounds.y0 + bounds.y1) / 2))]
        labels = [text for text in local if re.fullmatch(r"\d{1,2}\.\d{1,2}", text)]
        heights = [float(m[1].replace(",", ".")) for text in local
                   if (m := re.fullmatch(r"\+\s*([2-6][.,]\d{3})", text))]
        if len(set(labels)) != 1 or len(set(heights)) != 1:
            continue
        clip = pymupdf.Rect(x0 - 14, y0 - 14, x1 + 14, y1 + 14) & page.rect
        preview = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip, alpha=False)
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        proposals.append({
            "page": page_number, "zone": labels[0], "height_m": heights[0],
            "preview": base64.b64encode(preview.tobytes("png")).decode("ascii"),
            "points": [[(2*x - preview.x) / preview.width,
                        (2*y - preview.y) / preview.height] for x, y in corners],
        })
    return proposals


def extract_ceiling_zones(page, page_number=None) -> dict:
    legend = parse_color_legend(page)
    # Only the exact table rows define the material colors for this sheet.
    swatches = {
        e["name"].strip().casefold(): e for e in legend
        if e["name"].strip().casefold() in {"потолок гкл", "ниша в потолке гкл"}
    }
    ceiling = swatches.get("потолок гкл")
    niche = swatches.get("ниша в потолке гкл")
    warnings = []
    shapes = []
    openings = []
    skipped = 0
    for path in page.get_drawings():
        fill = path.get("fill")
        if not fill:
            continue
        color = tuple(round(value, 3) for value in fill)
        for swatch, target in ((ceiling, shapes), (niche, openings)):
            if swatch is None or color != tuple(swatch["color_rgb"]):
                continue
            if list(path["rect"]) == swatch["marker_rect"]:
                continue
            polygon = _fill_geometry(path)
            if polygon is None or polygon.is_empty:
                skipped += 1
            elif polygon.area > 0:
                target.append(polygon)
            break
    outline = unary_union(shapes).difference(unary_union(openings))
    scale = _detect_scale(page)
    if skipped:
        warnings.append(f"Пропущено неподдерживаемых заливок: {skipped}.")
    if outline.is_empty or not scale:
        warnings.append("Не удалось определить контур ГКЛ или масштаб.")
    return {
        "area_pt2": outline.area, "boundary_pt": outline.length,
        "scale_pt_per_mm": scale, "geometry": mapping(outline),
        "warnings": warnings,
        "corner_proposals": propose_rectangular_corners(page, shapes, openings, page_number) if page_number else [],
    }


def _detect_scale(page) -> float:
    """Определяет масштаб чертежа (pt/мм) по размерным линиям.

    Размерные линии — чёрные или магентовые. Текст размеров — числовые
    значения в мм. Сопоставляем длину линии и текст по координатам,
    выбираем самый частый (модальный) коэффициент pt/мм.
    """
    # Горизонтальные и вертикальные размерные линии (чёрные или магентовые)
    lines = []
    for path in page.get_drawings():
        color = path.get("color")
        r = path["rect"]
        is_dim_color = (
            color is None
            or (abs(color[0] - 0) < 0.05 and abs(color[1] - 0) < 0.05
                and abs(color[2] - 0) < 0.05)
            or (abs(color[0] - 1) < 0.05 and abs(color[1] - 0) < 0.05
                and abs(color[2] - 0.502) < 0.05)
        )
        if not is_dim_color:
            continue
        #  thin stroke — размерная линия (не большая заливка)
        stroke_w = path.get("width") or 0
        w = r.x1 - r.x0
        h = r.y1 - r.y0
        is_thin = (w > 10 and h < 3 and stroke_w < 2) or \
                  (h > 10 and w < 3 and stroke_w < 2)
        if is_thin:
            if w > 10 and h < 3:
                lines.append((r.x0, r.y0, r.x1, r.y1))
            elif h > 10 and w < 3:
                lines.append((r.y0, r.x0, r.y1, r.x1))  # вертикальная: x0<->y
    if not lines:
        return 0.0

    # Тексты размеров
    texts = []
    for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES).get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span["text"].strip().replace(" ", "").replace(",", ".")
                txt = txt.replace("м", "").replace("М", "")
                txt = txt.replace("\xa0", "")
                if txt.replace(".", "", 1).isdigit():
                    try:
                        val = float(txt)
                        if 10 <= val <= 5000:
                            texts.append((span["bbox"][0], span["bbox"][1], val))
                    except ValueError:
                        pass

    # Собираем коэффициенты: для каждой линии ищем ближайший текст по центру
    ratios = []
    for (a0, b0, a1, b1) in lines:
        length = abs(a1 - a0)
        line_center = (a0 + a1) / 2
        # Найдём текст, ближайший к центру линии (по x), в окрестности по y
        best = None
        best_d = 1e9
        for (tx, ty, val) in texts:
            if abs(ty - b0) < 5 or abs(ty - b1) < 5:
                d = abs(tx - line_center)
                if d < best_d:
                    best_d, best = d, val
        if best is not None and best > 0 and best_d < 40:
            ratios.append(length / best)
    if not ratios:
        return 0.0
    # Модальный коэффициент
    from collections import Counter
    buckets = Counter(round(r, 4) for r in ratios)
    if buckets:
        return buckets.most_common(1)[0][0]

    # Fallback: масштаб по самому длинному размеру на листе.
    # Берём наибольшее числовое значение (мм) из текстов размеров и
    # сопоставляем с длинной ближайшей размерной линии.
    big_vals = [(x, y, v) for (x, y, v) in texts if v >= 2000]
    if not big_vals:
        return 0.0
    _, _, big_val = max(big_vals, key=lambda t: t[2])
    longest = max(lines, key=lambda l: abs(l[2] - l[0]))
    length = abs(longest[2] - longest[0])
    if big_val > 0 and length > 0:
        return round(length / big_val, 5)
    return 0.0


def extract_heights(text: str) -> list[float]:
    """Извлекает уровни/отметки потолка ('+2,750', '+3.000' и т.п.).

    Только разумные значения высоты потолка (2–6 м); отфильтровывает
    номера листов/координаты вроде +79200221044.
    """
    out = []
    for m in re.finditer(r"\+\s*(\d+(?:[.,]\d+)?)", text):
        v = _to_float(m.group(1))
        # Отметка потолка — целые или с одним знаком после запятой
        if 2.0 <= v <= 6.0 and round(v, 1) == round(v, 1):
            out.append(round(v, 2))
    return out


def _is_annotation(name: str) -> bool:
    n = name.lower().strip()
    if not n:
        return True
    if n in ANNOTATION_WORDS:
        return True
    if any(w in n for w in ANNOTATION_WORDS):
        return True
    # Явные номера/размеры
    if re.fullmatch(r"[\d.,:\-]+", n):
        return True
    return False


def extract_rooms(text: str) -> list[dict]:
    """Извлекает помещения из экспликации таблицы.

    Строки таблицы могут разбиваться построчно: номер, название, площадь
    идут на разных линиях ('3', 'Помывочная', '7,3 м 2').
    Также бывает '5 м 2 Сауна' — площадь с названием после.
    """
    rooms: list[dict] = []
    cur_no: int | None = None
    cur_name: str = ""
    cur_area: float | None = None

    def flush():
        nonlocal cur_no, cur_name, cur_area
        if cur_no is not None:
            if cur_area is not None:
                name = cur_name or f"Зона {cur_no}"
                if not _is_annotation(name):
                    rooms.append({"name": name.strip(), "area_m2": cur_area})
            elif cur_name and not _is_annotation(cur_name):
                rooms.append({"name": cur_name.strip(), "area_m2": None})
        cur_no, cur_name, cur_area = None, "", None

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        # Полная строка: '4 Комната отдыха 22,2 м 2' / '8 37,8 м 2 Беседка' / '7 17,6 м 2'
        m_row = re.match(
            r"^(?:(\d{1,2})\s+)?(.*?)\s*(\d+(?:[.,]\d+)?)\s*м\s*2\s*(.*)$",
            line, re.I,
        )
        if m_row and re.search(r"\d", line):
            num = int(m_row.group(1)) if m_row.group(1) else None
            name = (m_row.group(2) or "").strip()
            area = _to_float(m_row.group(3))
            extra = (m_row.group(4) or "").strip()
            if name and not _is_annotation(name) and name not in ("№",):
                if extra and not _is_annotation(extra):
                    name = f"{name} {extra}"
                flush()
                rooms.append({"name": name, "area_m2": area})
                continue
            if not name and extra and not _is_annotation(extra):
                # '8 37,8 м 2 Беседка'
                flush()
                rooms.append({"name": extra, "area_m2": area})
                continue
            if num is not None and not name:
                # Только номер и площадь: '7 17,6 м 2'
                flush()
                rooms.append({"name": f"Зона {num}", "area_m2": area})
                continue
        # '5 м 2 Сауна' / '5 м 2Сауна' — площадь, потом название
        m_area_name = re.match(
            r"^(\d+(?:[.,]\d+)?)\s*м\s*2\s*(.+)$", line, re.I,
        )
        if m_area_name:
            area = _to_float(m_area_name.group(1))
            name = m_area_name.group(2).strip()
            if not _is_annotation(name):
                flush()
                rooms.append({"name": name, "area_m2": area})
                continue
        # Только номер — сбрасываем накопленное название (это начало новой строки)
        m_no = re.fullmatch(r"(\d{1,2})", line)
        if m_no:
            flush()
            cur_no = int(m_no.group(1))
            cur_name = ""
            continue
        # Только площадь
        m_area = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*м\s*2", line, re.I)
        if m_area:
            if cur_no is not None and cur_area is None:
                cur_area = _to_float(m_area.group(1))
            continue
        # Только название
        if cur_no is not None and not cur_name and not re.search(r"[\dм]", line.lower()):
            if not _is_annotation(line):
                cur_name = line
            continue
    flush()
    # Удаляем дубли
    seen: set[str] = set()
    out: list[dict] = []
    for r in rooms:
        if r["name"] is None:
            continue
        k = r["name"].lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def extract_dimensions(text: str) -> list[int]:
    """Извлекает разметочные размеры (мм): 600, 830, 1525..."""
    dims = [int(x) for x in re.findall(r"(?<![\d.,])(\d{3,4})(?![\d.,])", text)]
    # Отбрасываем явно неразмерные (номера, времени, телефоны)
    return [d for d in dims if 150 <= d <= 6000]


# ----------------------------------------------------------------------------
# Триггеры элементов проекта
# ----------------------------------------------------------------------------

@dataclass
class TrackLine:
    """Трековая линия света."""
    length_m: float
    fixture_count: int = 0
    note: str = ""


@dataclass
class Project:
    """Структура разобранного дизайн-проекта."""
    sheets: list[dict] = field(default_factory=list)   # [{page, kind, title}]
    rooms: list[dict] = field(default_factory=list)    # [{name, area_m2, page}]
    ceiling_dims: list[int] = field(default_factory=list)
    track_lines: list[TrackLine] = field(default_factory=list)
    track_fixtures: int = 0
    track_fixture_models: list[str] = field(default_factory=list)
    color_legend: list[dict] = field(default_factory=list)   # сверка цветов с уточняющей таблицей
    height_m: float = 0.0                                     # высота потолка (отметка)
    wall_corners: int = 0                                     # все углы стен (внутренние + внешние)
    hidden_door_lengths_m: list[float] | None = None
    door_specification_pages: list[int] = field(default_factory=list)
    doors: int = 0                                            # количество дверей на плане
    flooring_zones: dict = field(default_factory=dict)         # зоны ГКЛ по фиолетовому цвету на листе 5
    vent_grilles: int = 0
    diffusers: int = 0
    chandeliers: int = 0
    spots: int = 0
    points: int = 0
    has_shadow_profile: bool = False
    has_niches: bool = False
    has_insulation: bool = False
    bras: int = 0
    notes: list[str] = field(default_factory=list)


def hidden_door_specification(text: str) -> list[float] | None:
    """Read explicitly identified hidden doors in a door schedule."""
    if not all(word in text.casefold() for word in ("наименование", "полотно", "проема")):
        return None
    rows = re.split(r"(?im)^\s*дверь\s+(?!до потолка)", text)
    hidden = [row for row in rows[1:] if re.match(r"скрыт", row, re.I)]
    heights = []
    for row in hidden:
        match = re.search(r"(?i)полотно\s+\d{3,4}\s*[xх×]\s*(\d{4})", row)
        if not match:
            return None
        heights.append(int(match[1]) / 1000)
    return heights


def parse_pdf(data: bytes) -> Project:
    """Разбирает PDF и возвращает структуру проекта."""
    # PyMuPDF-документ — для сверки цветов по уточняющей таблице
    mupdf_doc = pymupdf.open(stream=data, filetype="pdf")
    proj = Project()
    ceiling_rooms: list[dict] = []
    reader = PdfReader(BytesIO(data))
    schedules = []

    for idx, page in enumerate(mupdf_doc, start=1):
        text = page.get_text()
        kind = classify_sheet(text)
        proj.sheets.append({"page": idx, "kind": kind, "title": _title_of(text)})

        if kind == "plan_doors" or re.search(r"спецификация дверей", text, re.I):
            lengths = hidden_door_specification(text)
            schedules.append(lengths)
            if lengths is not None:
                proj.door_specification_pages.append(idx)

        if kind == "plan_ceilings":
            # Авторитетный источник зон потолков
            rooms = extract_rooms(reader.pages[idx - 1].extract_text() or text)
            if rooms:
                for r in rooms:
                    r["page"] = idx
                ceiling_rooms.extend(
                    r for r in rooms
                    if r.get("area_m2") and r["name"].strip() != "№"
                    and not re.search(r"\bверанд\w*", r["name"], re.I)
                )
            proj.ceiling_dims = extract_dimensions(text)
            proj.has_shadow_profile |= bool(re.search(r"тенев\w*\s+(?:шов|профил)", text, re.I))
            proj.has_niches |= bool(re.search(r"ниша[^\n]*подсвет", text, re.I))
            proj.has_insulation = ("утеплитель" in text.lower()
                                   or "утепл" in text.lower())
            # Сверка цветов с уточняющей таблицей + высота потолка
            try:
                legend = parse_color_legend(mupdf_doc[idx - 1])
                for item in legend:
                    if item["kind"] not in (s["kind"] for s in proj.color_legend):
                        proj.color_legend.append(item)
            except Exception:
                pass
            zones = extract_ceiling_zones(mupdf_doc[idx - 1], idx)
            zones["page"] = idx
            proj.flooring_zones.setdefault("sheets", []).append(zones)
            heights = extract_heights(text)
            if heights:
                # Высота потолка — модальное значение отметок (лист 7: +2,750),
                # не максимум (+3.000 может быть отметкой перекрытия)
                from collections import Counter
                proj.height_m = Counter(heights).most_common(1)[0][0]

        elif kind == "plan_flooring":
            # Лист с напольными покрытиями — сверка цветов зон по уточняющей таблице
            try:
                legend = parse_color_legend(mupdf_doc[idx - 1])
                for item in legend:
                    if item["kind"] not in (s["kind"] for s in proj.color_legend):
                        proj.color_legend.append(item)
            except Exception:
                pass
            # Зоны ГКЛ читаются на планах потолков (plan_ceilings),
            # не здесь — see plan_ceilings branch above.

        elif kind == "track_lights":
            # Треки: считаем длину линий и количество светильников
            dims = extract_dimensions(text)
            # Трековые светильники: парсим по моделям.
            # Блок: 'Трековый светильник <модель> ... N шт.'
            # Количества идут после блока моделей; берем первые
            # N значений 'N шт.', где N — число моделей трековых светильников.
            first = text.lower().find("трековый светильник")
            if first >= 0:
                tail = text[first:]
                for marker in ("см. развертки", "см.развертки",
                               "см.развёртки", "см. развёртки"):
                    pos = tail.lower().find(marker)
                    if pos >= 0:
                        tail = tail[:pos]
                        break
                n_models = len(re.findall(r"трековый светильник", tail, re.I))
                qty_all = [int(x) for x in re.findall(r"(\d{1,3})\s*шт\.", tail)]
                qty = qty_all[:n_models] if n_models else []
                proj.track_fixtures += sum(qty) if qty else n_models
                # Запомним модели для ведомости
                models = re.findall(r"трековый светильник[^\n]*", tail, re.I)
                for mm in models:
                    proj.track_fixture_models.append(mm.strip()[:120])
            # Keep the quantity printed next to the recessed track schedule.
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            quantities = set()
            for i, line in enumerate(lines):
                if "врезной" not in line.casefold() or "шинопровод" not in line.casefold():
                    continue
                for nearby in lines[max(0, i - 55):i + 12]:
                    match = re.fullmatch(r"~?\s*(\d+(?:[.,]\d+)?)\s*п\.?\s*м\.?", nearby, re.I)
                    if match:
                        quantities.add((_to_float(match[1]), nearby.startswith("~")))
            if len(quantities) == 1:
                length, approximate = quantities.pop()
                note = f"Лист {idx}, ведомость шинопровода" + (" (≈)" if approximate else "")
                proj.track_lines.append(TrackLine(length_m=length, note=note))
            proj.chandeliers += len(re.findall(r"люстра|люстры", text, re.I))
            proj.spots += len(re.findall(r"спот", text, re.I))
            proj.points += len(re.findall(r"эл\.?точк|точка эл", text, re.I))
            proj.bras += len(re.findall(r"бра", text, re.I))

        elif kind == "plan_electric":
            proj.vent_grilles += len(re.findall(r"вентиляционн|решетк", text, re.I))
            proj.diffusers += len(re.findall(r"диффузор|щелев", text, re.I))
            proj.points += len(re.findall(r"точка|подрозетн", text, re.I))

    # Дедубликация: площади используются только от листа потолков
    seen = set()
    for r in ceiling_rooms:
        key = (r["page"], r["name"].lower())
        if key not in seen:
            seen.add(key)
            proj.rooms.append(r)

    if schedules and all(schedule is not None for schedule in schedules):
        proj.hidden_door_lengths_m = [height for schedule in schedules for height in schedule]

    proj.notes.append(
        f"Проек разобран: {len(proj.sheets)} листов, "
        f"зон потолков: {len(proj.rooms)}, трековых линий: {len(proj.track_lines)}"
    )
    return proj


def kind_is_light_related(kind: str) -> bool:
    return kind in ("track_lights", "plan_lighting_control", "plan_sockets", "plan_electric")


def _title_of(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return next((line for line in reversed(lines) if not line.isdigit()), "")




