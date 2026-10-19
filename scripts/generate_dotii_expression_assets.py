#!/usr/bin/env python3
"""Generate management-center previews for the firmware's geometric Dotii face."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "bridge" / "web" / "assets" / "expressions"
MANIFEST_PATH = ASSET_DIR / "animations.json"
CANVAS_SIZE = 466
CENTER = CANVAS_SIZE // 2
FACE_SCALE_NUM = 6
FACE_SCALE_DEN = 5
DURATIONS = [100] * 8
WARM = (244, 234, 210)
AMBER = (242, 198, 109)
CYAN = (85, 216, 208)
CORAL = (255, 130, 122)


def scale_face_value(value: int) -> int:
    magnitude = (abs(value) * FACE_SCALE_NUM + FACE_SCALE_DEN // 2) // FACE_SCALE_DEN
    return magnitude if value >= 0 else -magnitude


def part_box(width: int, height: int, x: int, y: int) -> tuple[int, int, int, int]:
    left = CENTER + x - width // 2
    top = CENTER + y - height // 2
    return left, top, left + width, top + height


def draw_parts(parts: list[tuple[int, int, int, int, tuple[int, int, int]]]) -> Image.Image:
    glow = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    core = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    core_draw = ImageDraw.Draw(core)
    for width, height, x, y, color in parts:
        width = scale_face_value(width)
        height = scale_face_value(height)
        x = scale_face_value(x)
        y = scale_face_value(y)
        box = part_box(width, height, x, y)
        radius = min(width, height) // 2
        glow_draw.rounded_rectangle(box, radius=radius, fill=(*color, 105))
        core_draw.rounded_rectangle(box, radius=radius, fill=(*color, 255))
    base = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), (0, 0, 0, 255))
    base = Image.alpha_composite(base, glow.filter(ImageFilter.GaussianBlur(7)))
    return Image.alpha_composite(base, core).convert("RGB")


def frame_parts(expression: str, phase: int) -> list[tuple[int, int, int, int, tuple[int, int, int]]]:
    parts: list[tuple[int, int, int, int, tuple[int, int, int]]] = [
        (52, 72, -78, -30, WARM),
        (52, 72, 78, -30, WARM),
        (56, 12, 0, 70, WARM),
    ]
    wave = phase if phase < 4 else 8 - phase

    if expression == "idle_breath":
        breath = wave * 2
        return [(52, 68 + breath, -78, -30, WARM), (52, 68 + breath, 78, -30, WARM),
                (56, 12, 0, 70, WARM)]
    if expression == "blink":
        eye_height = 7 if phase in (2, 3) else 24 if phase in (1, 4) else 66
        return [(58, eye_height, -78, -26, WARM), (58, eye_height, 78, -26, WARM),
                (56, 12, 0, 70, WARM)]
    if expression == "curious":
        return [(62, 82, -76, -34, WARM), (46, 62, 82, -26, WARM),
                (20 + wave, 20 + wave, 8, 70, AMBER)]
    if expression == "sleepy_yawn":
        yawn = wave * 3
        return [(68, 10, -76, -18, WARM), (68, 10, 76, -18, WARM),
                (26 + yawn, 26 + yawn, 0, 72, AMBER)]
    if expression == "touch_response":
        pulse = wave * 4
        spread = wave * 3
        return [(62 + pulse // 2, 80 + pulse, -76, -34, WARM),
                (62 + pulse // 2, 80 + pulse, 76, -34, WARM),
                (30 + pulse, 16 + pulse // 2, 0, 76, CYAN),
                (13, 13, -120 - spread, 52, CYAN), (13, 13, 120 + spread, 52, CYAN)]
    if expression == "connecting":
        left = CYAN if phase < 4 else (45, 112, 108)
        right = (45, 112, 108) if phase < 4 else CYAN
        return [(48, 64, -76, -28, left), (48, 64, 76, -28, right),
                (56, 12, 0, 70, WARM), (10, 10, phase * 22 - 77, 88, CYAN)]
    if expression == "working":
        shift = wave * 4
        return [(48, 68, -86 + shift, -30, WARM), (48, 68, 70 + shift, -30, WARM),
                (62, 10, 0, 72, CYAN), (10, 10, 134, -82 + phase * 7, CYAN)]
    if expression == "complete":
        return [(68, 14, -76, -28, WARM), (68, 14, 76, -28, WARM),
                (108 + wave, 20 + wave // 2, 0, 62, AMBER),
                (10 + wave, 10 + wave, -132, -76, AMBER),
                (10 + wave, 10 + wave, 132, -76, CYAN)]
    if expression == "failure":
        return [(58, 12, -76, -22, CORAL), (58, 12, 76, -22, CORAL),
                (72, 12, 0, 80 + wave // 2, CORAL),
                (10 + wave, 10 + wave, 0, 52, CORAL)]
    raise ValueError(f"Unknown Dotii expression: {expression}")


def main() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for animation in manifest["animations"]:
        expression = animation["id"]
        frames = [draw_parts(frame_parts(expression, phase)) for phase in range(8)]
        # WebP may coalesce adjacent identical geometric phases (notably the
        # closed-eye hold). Preserve the eight-phase contract with a unique,
        # effectively black marker inside the round-screen safe area.
        for phase, frame in enumerate(frames):
            frame.putpixel((CENTER - 4 + phase, 402), (phase + 1, phase + 1, phase + 1))
        output_path = ROOT / "bridge" / "web" / animation["src"].lstrip("/")
        frames[0].save(
            output_path,
            "WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=DURATIONS,
            loop=0,
            lossless=True,
            method=6,
        )
        payload = output_path.read_bytes()
        with Image.open(output_path) as check:
            if check.size != (CANVAS_SIZE, CANVAS_SIZE) or check.n_frames != 8:
                raise ValueError(f"Invalid generated animation: {output_path.name}")
        animation["durations_ms"] = DURATIONS
        animation["bytes"] = len(payload)
        animation["sha256"] = hashlib.sha256(payload).hexdigest()
        print(f"{expression}: {len(payload)} bytes")

    manifest["style"] = "photo_geometric_v2"
    manifest["notes"] = (
        "照片版圆角条形脸；九种表情相对上一版统一放大 20%，"
        "网页预览与固件使用相同状态造型和八相位几何参数，网页额外模拟 AMOLED 光晕。"
    )
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()
