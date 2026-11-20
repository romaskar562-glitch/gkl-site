# -*- coding: utf-8 -*-
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pypdf import PdfReader
from engine import ZoneInput, calculate_zone, extract_page_areas

zone = calculate_zone(ZoneInput("тест", 4, 3))
print("AREA", zone["area_m2"], "carriers", zone["carriers"], "cross", zone["crosses"])
for item in zone["items"]:
    print(item["name"], item["net"], item["order"], item["unit"])

pdf = PdfReader(r"C:\Users\USER\Desktop\Мой сайт\РП_Баня_Анненка_ 31.10.24.pdf")
info = extract_page_areas(pdf.pages[6].extract_text() or "", 7)
print("PAGE7", [(a["m2"], a["explication"]) for a in info["areas"]], "hint", info["ceiling_hint"])
