"""图像分析：元数据、感知哈希、错误级别分析（篡改检测）、损伤特征识别与新旧损比对。

内置实现基于经典图像处理；配置了私域多模态模型时，图像理解任务由模型完成并与本模块的量化结果交叉校验。
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import ExifTags, Image, ImageChops, ImageFilter
from scipy.fft import dctn


def load(path: str | Path) -> Image.Image:
    return Image.open(path)


def metadata(img: Image.Image) -> dict:
    out = {"width": img.width, "height": img.height, "format": img.format}
    info = getattr(img, "text", None) or {}
    for k, v in info.items():
        out[k] = v
    try:
        exif = img.getexif()
        for tag, value in exif.items():
            name = ExifTags.TAGS.get(tag, str(tag))
            if name in ("DateTimeOriginal", "DateTime", "Model", "Make", "Software"):
                out[name] = str(value)
    except Exception:  # noqa: BLE001
        pass
    return out


def phash(img: Image.Image) -> str:
    g = np.asarray(img.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=float)
    d = dctn(g, norm="ortho")[:8, :8]
    med = np.median(d[1:, 1:])
    bits = (d > med).flatten()
    return "".join("1" if b else "0" for b in bits)


def hamming(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b))


def ela(img: Image.Image, quality: int = 90) -> dict:
    """错误级别分析：重新压缩后与原图差异过于集中的区域可能经过编辑。"""
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    diff = np.asarray(ImageChops.difference(rgb, Image.open(buf)).convert("L"), dtype=float)
    h, w = diff.shape
    gh, gw = max(1, h // 8), max(1, w // 8)
    cells = np.array([[diff[i * gh : (i + 1) * gh, j * gw : (j + 1) * gw].mean() for j in range(8)] for i in range(8)])
    mean = float(cells.mean())
    std = float(cells.std()) or 1e-6
    z = (cells - mean) / std
    idx = np.unravel_index(np.argmax(z), z.shape)
    score = float(z.max())
    # 拼接区域的压缩误差显著高于全图典型水平：同时要求统计离群（z）与幅度对比（相对中位数的倍数）
    ratio = float(cells.max() / (np.median(cells) or 1e-6))
    return {"score": round(score, 3), "ratio": round(ratio, 3), "suspicious": score > 3.0 and ratio >= 1.8, "region": {"x": int(idx[1] * gw), "y": int(idx[0] * gh), "w": gw, "h": gh}, "mean_error": round(mean, 3)}


def _hsv(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB").convert("HSV"), dtype=float) / 255.0


def damage_features(img: Image.Image) -> dict:
    hsv = _hsv(img)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    gray = np.asarray(img.convert("L"), dtype=float) / 255.0
    edges = np.asarray(img.convert("L").filter(ImageFilter.FIND_EDGES), dtype=float) / 255.0
    burn = (v < 0.2) & (s < 0.45)
    rust = (h > 0.03) & (h < 0.11) & (s > 0.45) & (v > 0.25) & (v < 0.85)
    water = (h > 0.5) & (h < 0.7) & (s > 0.15) & (s < 0.6) & (v > 0.3)
    crack = (edges > 0.35) & (gray < 0.35)
    total = gray.size
    feats = {"burn_ratio": float(burn.sum() / total), "rust_ratio": float(rust.sum() / total), "water_ratio": float(water.sum() / total), "crack_ratio": float(crack.sum() / total), "edge_density": float((edges > 0.25).mean())}
    regions = []
    hh, ww = gray.shape
    gh, gw = hh // 8, ww // 8
    for name, mask in (("烧蚀", burn), ("锈蚀", rust), ("水渍", water), ("裂纹", crack)):
        cells = [(i, j, mask[i * gh : (i + 1) * gh, j * gw : (j + 1) * gw].mean()) for i in range(8) for j in range(8)]
        for i, j, r in sorted(cells, key=lambda t: -t[2])[:2]:
            if r > 0.12:
                regions.append({"type": name, "x": j * gw, "y": i * gh, "w": gw, "h": gh, "coverage": round(float(r), 3)})
    return {"features": {k: round(v, 4) for k, v in feats.items()}, "regions": regions}


DAMAGE_RULES = [("burn_ratio", "烧蚀", [0.01, 0.05, 0.12, 0.25]), ("crack_ratio", "破裂", [0.004, 0.015, 0.04, 0.08]), ("water_ratio", "水渍", [0.02, 0.08, 0.18, 0.35]), ("rust_ratio", "锈蚀", [0.02, 0.06, 0.15, 0.3])]
GRADES = ["无明显损伤", "轻微", "中度", "严重", "全损"]


def assess_damage(img: Image.Image) -> dict:
    df = damage_features(img)
    found = []
    worst = 0
    for key, name, cuts in DAMAGE_RULES:
        v = df["features"][key]
        grade = sum(v >= c for c in cuts)
        if grade:
            found.append({"type": name, "grade": GRADES[grade], "level": grade, "ratio": v})
            worst = max(worst, grade)
    repair = "更换" if worst >= 3 else ("维修" if worst >= 1 else "无需处理")
    confidence = round(min(0.95, 0.55 + 0.1 * len(found) + 0.05 * worst), 2)
    return {"damages": found, "grade": GRADES[worst], "level": worst, "action": repair, "regions": df["regions"], "features": df["features"], "confidence": confidence}


def compare_old_damage(before: Image.Image, after: Image.Image) -> dict:
    """新旧损比对：承保时照片若已存在同类损伤特征，判定为旧损可能。"""
    b, a = assess_damage(before), assess_damage(after)
    old = [d for d in a["damages"] if any(x["type"] == d["type"] and x["level"] >= max(1, d["level"] - 1) for x in b["damages"])]
    return {"hamming": hamming(phash(before), phash(after)), "before_grade": b["grade"], "after_grade": a["grade"], "old_damage_types": [d["type"] for d in old], "old_damage": bool(old)}


def to_data_url(path: str | Path, max_side: int = 768) -> str:
    import base64

    img = Image.open(path).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
