"""Regenerate the self-contained HTML and PNG from agents.architecture.json.

Run: python tools/render_diagram.py (Pillow is already an application dependency).
For PNG Cyrillic text, a DejaVu Sans font must be available on the build host.
"""

import html
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DIRECTORY = Path(__file__).resolve().parents[1] / "diagr"


def main():
    data = json.loads((DIRECTORY / "agents.architecture.json").read_text(encoding="utf-8"))
    width, height = data["canvas"]
    image = Image.new("RGB", (width, height), "#FAF7F0")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("DejaVuSans.ttf", 16)
    title_font = ImageFont.truetype("DejaVuSans.ttf", 28)
    small = ImageFont.truetype("DejaVuSans.ttf", 13)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="Архитектура трекера">',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8" fill="#A89A83"/></marker></defs>',
        f'<rect width="{width}" height="{height}" fill="#FAF7F0"/>',
    ]
    svg.append(
        '<text x="40" y="50" fill="#3F3A33" font-size="26" font-family="Georgia, &quot;Times New Roman&quot;, serif">'
        + html.escape(data["meta"]["title"])
        + "</text>"
    )
    draw.text((40, 24), data["meta"]["title"], font=title_font, fill="#3F3A33")
    components = {component["id"]: component for component in data["components"]}
    for edge in data["connections"]:
        points = edge["points"]
        svg.append(
            '<polyline points="'
            + " ".join(f"{x},{y}" for x, y in points)
            + '" fill="none" stroke="#A89A83" stroke-width="2" marker-end="url(#arrow)"/>'
        )
        draw.line([tuple(point) for point in points], fill="#A89A83", width=2)
        (ax, ay), (bx, by) = points[-2:]
        angle = math.atan2(by - ay, bx - ax)
        draw.polygon(
            [
                (bx, by),
                (bx - 10 * math.cos(angle - 0.45), by - 10 * math.sin(angle - 0.45)),
                (bx - 10 * math.cos(angle + 0.45), by - 10 * math.sin(angle + 0.45)),
            ],
            fill="#A89A83",
        )
        if edge.get("label"):
            x, y = edge["labelAt"]
            text = edge["label"]
            box = draw.textbbox((x, y - 12), text, font=small)
            draw.rectangle((box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), fill="#FAF7F0")
            draw.text((x, y - 12), text, font=small, fill="#6F6A5F")
            svg.append(
                f'<text x="{x}" y="{y}" fill="#6F6A5F" font-size="13" paint-order="stroke" stroke="#FAF7F0" stroke-width="6">{html.escape(text)}</text>'
            )
    for component in components.values():
        x, y = component["pos"]
        w, h = component["size"]
        color = component.get("color", "#8A6D4B")
        draw.rounded_rectangle((x, y, x + w, y + h), radius=10, fill="#FFFFFF", outline=color, width=2)
        svg.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#FFFFFF" stroke="{color}" stroke-width="2"/>'
        )
        for index, text in enumerate([component["label"], *component.get("details", [])]):
            ty = y + 26 + index * 22
            fill = "#3F3A33" if index == 0 else "#6F6A5F"
            weight = ' font-weight="600"' if index == 0 else ""
            svg.append(
                f'<text x="{x + 14}" y="{ty}" fill="{fill}" font-size="{16 if index == 0 else 13}"{weight}>{html.escape(text)}</text>'
            )
            draw.text((x + 14, ty - 16), text, font=font if index == 0 else small, fill=fill)
    for index, text in enumerate(data["notes"]):
        y = height - 112 + index * 25
        svg.append(f'<text x="40" y="{y}" fill="#6F6A5F" font-size="15">{html.escape(text)}</text>')
        draw.text((40, y - 15), text, font=small, fill="#6F6A5F")
    svg.append("</svg>")
    markup = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Архитектура агентов</title><style>body{margin:0;background:#FAF7F0;font-family:system-ui,-apple-system,sans-serif}svg{display:block;width:100%;min-width:1000px}main{overflow:auto}</style></head><body><main>'
        + "\n".join(svg)
        + "</main></body></html>"
    )
    (DIRECTORY / "agents.html").write_text(markup, encoding="utf-8")
    image.save(DIRECTORY / "agents.png", optimize=True)


if __name__ == "__main__":
    main()
