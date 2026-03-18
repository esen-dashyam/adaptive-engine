"""Generate K-8 educational step-by-step illustrations via Gemini Imagen.

Pipeline:
  1. Gemini text → break concept into visual steps + annotation callouts
  2. Imagen → generate one image per step (no text)
  3. Pillow → overlay infographic-style annotations that blend with the image
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
import textwrap

from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

from backend.app.core.settings import settings

log = logging.getLogger(__name__)

# ── Font setup ──────────────────────────────────────────

_FONT_PATHS = [
    "/System/Library/Fonts/Avenir Next.ttc",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/ArialHB.ttc",
]


def _get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a clean sans-serif font at the given size."""
    for path in _FONT_PATHS:
        try:
            idx = 1 if bold and path.endswith(".ttc") else 0
            try:
                return ImageFont.truetype(path, size, index=idx)
            except Exception:
                return ImageFont.truetype(path, size, index=0)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# ── Step-palette colors ─────────────────────────────────

_STEP_THEMES = [
    {"bg": (99, 102, 241), "text": (255, 255, 255), "bubble": (255, 255, 255, 220), "btxt": (30, 30, 60)},
    {"bg": (16, 185, 129), "text": (255, 255, 255), "bubble": (255, 255, 255, 220), "btxt": (10, 60, 40)},
    {"bg": (245, 158, 11), "text": (255, 255, 255), "bubble": (255, 255, 255, 220), "btxt": (80, 50, 0)},
    {"bg": (239, 68, 68),  "text": (255, 255, 255), "bubble": (255, 255, 255, 220), "btxt": (80, 20, 20)},
    {"bg": (139, 92, 246), "text": (255, 255, 255), "bubble": (255, 255, 255, 220), "btxt": (50, 20, 80)},
    {"bg": (6, 182, 212),  "text": (255, 255, 255), "bubble": (255, 255, 255, 220), "btxt": (10, 50, 60)},
]


# ── Pillow drawing helpers ──────────────────────────────


def _draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float, float, float],
    radius: int,
    fill: tuple,
    outline: tuple | None = None,
    outline_width: int = 0,
):
    """Draw a rounded rectangle (compatible with older Pillow)."""
    x0, y0, x1, y1 = [int(v) for v in xy]
    r = min(radius, (x1 - x0) // 2, (y1 - y0) // 2)
    try:
        draw.rounded_rectangle(
            [(x0, y0), (x1, y1)],
            radius=r,
            fill=fill,
            outline=outline,
            width=outline_width,
        )
    except AttributeError:
        draw.rectangle([(x0, y0), (x1, y1)], fill=fill, outline=outline)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Wrap text to fit within max_width pixels."""
    avg_char_w = font.getlength("W")
    chars_per_line = max(10, int(max_width / avg_char_w))
    wrapped = textwrap.wrap(text, width=chars_per_line)
    return wrapped or [text]


# ── Infographic overlay ─────────────────────────────────


def _add_infographic(
    img_b64: str,
    step_num: int,
    title: str,
    annotations: list[dict],
) -> str:
    """Add infographic-style overlays to an image.

    Returns new base64-encoded JPEG.
    """
    img_bytes = base64.b64decode(img_b64)
    img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    w, h = img.size
    theme = _STEP_THEMES[(step_num - 1) % len(_STEP_THEMES)]

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    margin = max(12, w // 40)
    bubble_pad_x = max(10, w // 50)
    bubble_pad_y = max(6, h // 80)

    # ── Title banner at top ──
    title_font = _get_font(max(18, w // 25), bold=True)
    title_lines = _wrap_text(title, title_font, w - margin * 4)
    line_h = title_font.getbbox("Ay")[3] + 4
    banner_h = len(title_lines) * line_h + bubble_pad_y * 2

    _draw_rounded_rect(
        draw,
        (margin, margin, w - margin, margin + banner_h),
        radius=14,
        fill=(*theme["bg"], 230),
    )

    # Step badge circle
    badge_r = max(14, banner_h // 3)
    badge_cx = margin + bubble_pad_x + badge_r
    badge_cy = margin + banner_h // 2
    draw.ellipse(
        [(badge_cx - badge_r, badge_cy - badge_r),
         (badge_cx + badge_r, badge_cy + badge_r)],
        fill=(255, 255, 255, 240),
    )
    num_font = _get_font(int(badge_r * 1.2), bold=True)
    nbbox = draw.textbbox((0, 0), str(step_num), font=num_font)
    draw.text(
        (badge_cx - (nbbox[2] - nbbox[0]) // 2,
         badge_cy - (nbbox[3] - nbbox[1]) // 2 - 1),
        str(step_num),
        fill=(*theme["bg"], 255),
        font=num_font,
    )

    # Title text
    text_x = badge_cx + badge_r + bubble_pad_x
    text_y = margin + bubble_pad_y
    for line in title_lines:
        draw.text((text_x, text_y), line, fill=theme["text"], font=title_font)
        text_y += line_h

    # ── Annotation bubbles ──
    ann_font = _get_font(max(13, w // 38))
    ann_line_h = ann_font.getbbox("Ay")[3] + 3

    pos_cycle = ["bottom-left", "bottom-right", "top-left", "top-right"]

    for i, ann in enumerate(annotations[:4]):
        text = ann.get("text", "")
        if not text:
            continue
        pos = ann.get("position", pos_cycle[i % len(pos_cycle)])

        max_bubble_w = int(w * 0.45)
        lines = _wrap_text(text, ann_font, max_bubble_w)
        text_block_w = max(ann_font.getlength(ln) for ln in lines)
        text_block_h = len(lines) * ann_line_h
        bw = int(text_block_w + bubble_pad_x * 2)
        bh = int(text_block_h + bubble_pad_y * 2)

        if "right" in pos:
            bx = w - margin - bw
        else:
            bx = margin

        if "top" in pos:
            by = margin + banner_h + 8 + (i // 2) * (bh + 6)
        elif "center" in pos:
            by = h // 2 - bh // 2
        else:
            by = h - margin - bh - (i // 2) * (bh + 6)

        bx = max(margin, min(bx, w - bw - margin))
        by = max(margin + banner_h + 4, min(by, h - bh - margin))

        _draw_rounded_rect(
            draw,
            (bx, by, bx + bw, by + bh),
            radius=10,
            fill=theme["bubble"],
            outline=(*theme["bg"], 180),
            outline_width=2,
        )

        tx = bx + bubble_pad_x
        ty = by + bubble_pad_y
        for ln in lines:
            draw.text((tx, ty), ln, fill=theme["btxt"], font=ann_font)
            ty += ann_line_h

    # ── Composite ──
    result = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ── Gemini step planner ─────────────────────────────────


def _gemini_plan_steps(
    topic: str, subject: str, grade: int, num_steps: int = 4
) -> list[dict]:
    """Ask Gemini to break the concept into visual steps with annotations."""
    client = genai.Client(api_key=settings.gemini_api_key)
    age = f"ages {grade + 4}-{grade + 6}"

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"""You are an art director for a children's educational publisher.

Break the concept "{topic}" ({subject or 'school'}, grade {grade}, {age})
into exactly {num_steps} sequential visual steps that explain it like a picture book.

Return a JSON array. Each step has a scene description for the illustrator,
AND 2-3 short annotation callouts that explain what the student should notice:

[
  {{
    "step": 1,
    "label": "Step 1: Short title",
    "scene": "Detailed scene description (2-3 sentences, concrete visuals)...",
    "annotations": [
      {{"text": "Short fact or explanation (max 15 words)", "position": "bottom-left"}},
      {{"text": "Another callout (max 15 words)", "position": "bottom-right"}}
    ]
  }},
  ...
]

Rules for scene descriptions:
- CONCRETE visual elements only (objects, people, places).
- Focus on what "{topic}" actually IS.
- Do NOT mention any text to put in the image.

Rules for annotations:
- 2-3 per step. Each max 15 words. Simple language for {age}.
- These are educational callouts explaining what the picture shows.
- position: "bottom-left", "bottom-right", "top-left", or "top-right"
- Distribute positions so they don't overlap.

Return ONLY the JSON array.""",
        config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=2500,
            response_mime_type="application/json",
        ),
    )

    text = resp.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]

    text = re.sub(r",\s*([}\]])", r"\1", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            data = json.loads(match.group())
        else:
            raise

    if isinstance(data, dict) and "steps" in data:
        data = data["steps"]
    return data


# ── Imagen single-image generator ──────────────────────


def _generate_one_image(prompt: str) -> str | None:
    """Generate a single image via Imagen. Returns base64 JPEG or None."""
    client = genai.Client(api_key=settings.gemini_api_key)
    response = client.models.generate_images(
        model="imagen-4.0-fast-generate-001",
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            output_mime_type="image/jpeg",
        ),
    )
    if response.generated_images:
        img = response.generated_images[0]
        return base64.b64encode(img.image.image_bytes).decode("utf-8")
    return None


# ── Main pipeline ──────────────────────────────────────


def generate_concept_images(
    topic: str,
    subject: str = "",
    grade: int = 5,
    num_steps: int = 4,
) -> list[tuple[str, str]]:
    """Generate step-by-step educational illustrations with annotations.

    Returns list of (step_label, base64_jpeg) tuples.
    """
    if not settings.gemini_api_key:
        return []

    try:
        steps = _gemini_plan_steps(topic, subject, grade, num_steps)
        log.info("Gemini planned %d steps for '%s'", len(steps), topic)

        results: list[tuple[str, str]] = []
        age = f"ages {grade + 4}-{grade + 6}"

        for step_info in steps:
            scene = step_info.get("scene", "")
            label = step_info.get("label", f"Step {step_info.get('step', '?')}")
            step_num = step_info.get("step", len(results) + 1)
            annotations = step_info.get("annotations", [])

            prompt = (
                f"{scene} "
                f"Educational illustration for kids ({age}). "
                f"Bright saturated colors, clean composition, "
                f"kid-friendly cartoon style, digital art. "
                f"IMPORTANT: The image must contain ZERO text — "
                f"no words, no letters, no numbers, no labels, "
                f"no captions, no signs, no writing of any kind."
            )
            log.info("Imagen step %s: %s", label, prompt[:100])

            try:
                b64 = _generate_one_image(prompt)
                if not b64:
                    log.info("Retrying step '%s' with softer prompt", label)
                    soft_prompt = (
                        f"A peaceful, kid-friendly educational illustration about "
                        f"'{label}' related to {topic}. "
                        f"Colorful cartoon style for children ({age}), "
                        f"educational poster, digital art. "
                        f"The image must contain absolutely no text or writing."
                    )
                    b64 = _generate_one_image(soft_prompt)
                if b64:
                    b64_final = _add_infographic(b64, step_num, label, annotations)
                    results.append((label, b64_final))
                else:
                    log.warning("Imagen returned no image for step: %s", label)
            except Exception as e:
                log.warning("Imagen failed for step '%s': %s", label, e)

        return results

    except Exception as e:
        log.warning("Image generation pipeline failed: %s", e)
        return []


# ── Backward-compat single-image (used by quiz pipeline) ──


def generate_concept_image(
    topic: str,
    subject: str = "",
    grade: int = 5,
) -> str | None:
    """Generate a single concept illustration."""
    results = generate_concept_images(topic, subject, grade, num_steps=1)
    if results:
        return results[0][1]
    return None


def build_image_html(image_b64: str, alt: str = "Concept illustration") -> str:
    """Return an <img> tag with embedded base64 image."""
    return (
        f'<img src="data:image/jpeg;base64,{image_b64}" '
        f'alt="{alt}" style="max-width:100%;border-radius:12px;'
        f'box-shadow:0 4px 16px rgba(0,0,0,0.1);display:block;margin:0 auto;" />'
    )
