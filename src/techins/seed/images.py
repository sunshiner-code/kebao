"""演示图像生成：设备铭牌照片、实验室现场照片、设备损坏照片（含烧蚀、裂纹、锈蚀、水渍特征），以及篡改与翻拍样本。

图像的拍摄信息写入 PNG 文本块（序列号、型号、部位、拍摄时间），供多模态解析与一致性核验使用。
"""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from PIL.PngImagePlugin import PngInfo

W, H = 640, 480


def _noise(img: Image.Image, sigma: float, seed: int, chroma: bool = False) -> Image.Image:
    """传感器噪声：真实照片的压缩误差在全图分布均匀，避免纯色合成图在错误级别分析中出现伪集中。
    默认以亮度噪声为主（不改变色相饱和度）；chroma=True 模拟另一台相机的彩色噪声（用于拼接篡改样本）。"""
    arr = np.asarray(img, dtype=float)
    rng = np.random.default_rng(seed)
    if chroma:
        arr = arr + rng.normal(0, sigma, arr.shape)
    else:
        arr = arr + rng.normal(0, sigma, arr.shape[:2] + (1,)) + rng.normal(0, sigma * 0.15, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"))


def _save(img: Image.Image, path: Path, meta: dict, noise: float = 5.0, seed: int = 0) -> Path:
    if noise:
        img = _noise(img, noise, seed)
    info = PngInfo()
    for k, v in meta.items():
        info.add_text(k, str(v))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", pnginfo=info)
    return path


def _body(seed: int, base: tuple[int, int, int] | None = None) -> tuple[Image.Image, ImageDraw.ImageDraw, random.Random]:
    """设备外观：机身、控制箱、转动部件与管线的位置、尺寸与配色随设备而异（不同设备的感知哈希不同）。"""
    rng = random.Random(seed)
    g = rng.randint(140, 200)
    base = base or (g, g + rng.randint(0, 2), g + rng.randint(0, 3))
    w = rng.randint(200, 228)
    wall = (w + rng.randint(0, 6), w + rng.randint(0, 6), w)
    img = Image.new("RGB", (W, H), wall)
    d = ImageDraw.Draw(img)
    floor_y = rng.randint(330, 390)
    f = rng.randint(120, 160)
    d.rectangle([0, floor_y, W, H], fill=(f + 6, f + 3, f))
    x0, y0 = rng.randint(40, 140), rng.randint(50, 130)
    x1, y1 = rng.randint(470, 600), floor_y - rng.randint(0, 20)
    d.rectangle([x0, y0, x1, y1], fill=base, outline=(110, 114, 118), width=4)
    for _ in range(rng.randint(2, 4)):
        bx, by = rng.randint(x0 + 10, x1 - 150), rng.randint(y0 + 10, y1 - 110)
        shade = rng.uniform(0.76, 0.93)
        d.rectangle([bx, by, bx + rng.randint(70, 150), by + rng.randint(50, 110)], fill=tuple(int(c * shade) for c in base), outline=(100, 102, 106), width=3)
    for _ in range(rng.randint(1, 3)):
        r = rng.randint(30, 75)
        cx, cy = rng.randint(x0 + r, x1 - r), rng.randint(y0 + r, y1 - r)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(rng.randint(120, 170),) * 3, outline=(90, 92, 94), width=4)
    for _ in range(rng.randint(2, 5)):
        px = rng.randint(0, W)
        d.line([(px, 0), (px + rng.randint(-60, 60), y0)], fill=(rng.randint(60, 110),) * 3, width=rng.randint(4, 9))
    for _ in range(rng.randint(2, 4)):
        gx, gy = rng.randint(0, W - 120), rng.randint(0, floor_y - 60)
        gray = rng.randint(90, 235)
        d.rectangle([gx, gy, gx + rng.randint(40, 140), gy + rng.randint(30, 120)], fill=(gray, gray, gray + 2))
    if rng.random() < 0.5:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        d = ImageDraw.Draw(img)
    return img, d, rng


def nameplate_photo(path: Path, *, serial: str, model: str, equipment: str, captured_at: str, seed: int, rust: float = 0.0) -> Path:
    img, d, rng = _body(seed)
    d.rectangle([380, 300, 530, 360], fill=(236, 236, 230), outline=(60, 60, 60), width=2)
    d.text((388, 308), f"MODEL {model}", fill=(30, 30, 30))
    d.text((388, 330), f"S/N {serial}", fill=(30, 30, 30))
    if rust > 0:
        _patches(d, rng, (150, 82, 30), rust, area=(100, 250, 340, 375))
    return _save(img, path, {"serial": serial, "model": model, "equipment": equipment, "captured_at": captured_at, "part": "整机及铭牌"}, seed=seed)


def lab_photo(path: Path, *, captured_at: str, seed: int, hazards: bool = False) -> Path:
    rng = random.Random(seed)
    img = Image.new("RGB", (W, H), (226, 228, 224))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 340, W, H], fill=(170, 168, 160))
    for i in range(4):
        x = 40 + i * 150
        d.rectangle([x, 180, x + 120, 340], fill=(190, 194, 198), outline=(120, 120, 120), width=3)
        d.rectangle([x + 10, 120, x + 110, 180], fill=(205, 208, 212), outline=(130, 130, 130), width=2)
    d.rectangle([560, 150, 610, 220], fill=(200, 40, 40))
    if hazards:
        _patches(d, rng, (70, 120, 170), 0.12, area=(0, 340, W, H))
    return _save(img, path, {"captured_at": captured_at, "part": "实验室全景"}, seed=seed)


def _patches(d: ImageDraw.ImageDraw, rng: random.Random, color: tuple[int, int, int], share: float, area: tuple[int, int, int, int]) -> None:
    target = share * W * H
    covered = 0.0
    x0, y0, x1, y1 = area
    while covered < target:
        r = rng.randint(14, 34)
        cx, cy = rng.randint(x0 + r, x1 - r), rng.randint(y0 + r, y1 - r)
        jitter = tuple(max(0, min(255, c + rng.randint(-10, 10))) for c in color)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=jitter)
        covered += 3.14 * r * r * 0.8


def damage_photo(path: Path, *, serial: str, part: str, captured_at: str, seed: int, burn: float = 0.26, rust: float = 0.04, cracks: int = 18, water: float = 0.0) -> Path:
    img, d, rng = _body(seed)
    _patches(d, rng, (34, 30, 28), burn, area=(100, 100, 540, 370))
    if rust:
        _patches(d, rng, (150, 82, 30), rust, area=(100, 250, 340, 375))
    if water:
        _patches(d, rng, (80, 140, 190), water, area=(0, 360, W, H))
    img = img.filter(ImageFilter.SMOOTH_MORE)
    d = ImageDraw.Draw(img)
    for _ in range(cracks):
        x, y = rng.randint(150, 500), rng.randint(130, 340)
        pts = [(x, y)]
        for _k in range(6):
            x += rng.randint(-18, 18)
            y += rng.randint(4, 16)
            pts.append((x, y))
        d.line(pts, fill=(18, 16, 15), width=3)
    return _save(img, path, {"serial": serial, "part": part, "captured_at": captured_at}, seed=seed)


def tampered(src: Path, dst: Path, *, box: tuple[int, int, int, int] = (372, 296, 532, 364), serial_text: str = "S/N HY-2026-0101", meta: dict | None = None, seed: int = 7) -> Path:
    """局部篡改：把另一张高分辨率照片中的铭牌拼接到本图铭牌位置，拼接区域的噪声与压缩特征与原图不一致（错误级别分析可检出）。"""
    img = Image.open(src).convert("RGB")
    w, h = box[2] - box[0], box[3] - box[1]
    patch = Image.new("RGB", (w, h), (240, 240, 234))
    d = ImageDraw.Draw(patch)
    d.rectangle([0, 0, w - 1, h - 1], outline=(40, 40, 40), width=2)
    d.text((8, 10), "MODEL HY-X1", fill=(20, 20, 20))
    d.text((8, 34), serial_text, fill=(20, 20, 20))
    patch = _noise(patch.filter(ImageFilter.SHARPEN), 22, seed, chroma=True)
    img.paste(patch, box[:2])
    return _save(img, dst, meta or {}, noise=0)
