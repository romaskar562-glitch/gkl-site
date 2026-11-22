from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import shape


def draw_outline(project: dict, filename: Path) -> None:
    sheets = project.get("flooring_zones", {}).get("sheets", [])
    if not sheets:
        return
    image = Image.new("RGB", (750, 280 * len(sheets)), "white")
    draw = ImageDraw.Draw(image)
    for idx, sheet in enumerate(sheets):
        top = idx * 280
        draw.text((16, top + 10), f"PDF {sheet['page']}", fill="black")
        geometry = shape(sheet["geometry"])
        if geometry.is_empty:
            continue
        x0, y0, x1, y1 = geometry.bounds
        ratio = min(700 / (x1 - x0), 230 / (y1 - y0))
        polygons = [geometry] if geometry.geom_type == "Polygon" else geometry.geoms
        for polygon in polygons:
            for ring in (polygon.exterior, *polygon.interiors):
                points = [(20 + (x - x0) * ratio, top + 35 + (y - y0) * ratio)
                          for x, y in ring.coords]
                draw.line(points, fill="#6844a8", width=2)
    image.save(filename)
