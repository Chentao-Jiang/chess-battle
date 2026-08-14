"""Board-to-PNG image generation for multimodal models using Pillow."""
import base64
import io
from engine.board import *
from PIL import Image, ImageDraw, ImageFont


def _is_multimodal(model: str) -> bool:
    """Check if model likely supports image input."""
    m = model.lower()
    return any(v in m for v in [
        'gpt-4o', 'gpt-4-turbo', 'gpt-4-vision', 'gpt-4.5',
        'claude-3', 'claude-3.5', 'claude-4',
        'gemini', 'qwen-vl', 'qwen2.5-vl',
        'glm-4v', 'glm-5', 'kimi', 'k2',
        'deepseek-v4', 'vision',
    ])


def get_content_format(model: str, text: str, board) -> list | str:
    """Return message content with PNG image for vision models, text only otherwise."""
    if not _is_multimodal(model):
        return text
    try:
        png_b64 = render_board_png(board)
        return [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png_b64}"}},
        ]
    except Exception:
        return text  # fallback to text if image generation fails


def render_board_png(board) -> str:
    """Render board as PNG, return base64-encoded string."""
    CELL, PAD, R = 60, 35, 24
    W, H = PAD * 2 + CELL * 8, PAD * 2 + CELL * 9

    img = Image.new('RGB', (W, H), '#f0d9a0')
    draw = ImageDraw.Draw(img)

    # Try to load a CJK font, fall back gracefully
    font = None
    for fp in ['/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
               '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
               '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
               '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc']:
        try: font = ImageFont.truetype(fp, 18); break
        except: pass
    if font is None:
        try: font = ImageFont.load_default()
        except: pass

    # Grid lines
    line_color = '#4a2f1a'
    for i in range(10):
        sw = 2 if i in (4, 5) else 1
        draw.line([(PAD, PAD + i * CELL), (PAD + 8 * CELL, PAD + i * CELL)], fill=line_color, width=sw)
    draw.line([(PAD, PAD), (PAD, PAD + 9 * CELL)], fill=line_color, width=2)
    draw.line([(PAD + 8 * CELL, PAD), (PAD + 8 * CELL, PAD + 9 * CELL)], fill=line_color, width=2)
    for i in range(1, 8):
        draw.line([(PAD + i * CELL, PAD), (PAD + i * CELL, PAD + 4 * CELL)], fill=line_color)
        draw.line([(PAD + i * CELL, PAD + 5 * CELL), (PAD + i * CELL, PAD + 9 * CELL)], fill=line_color)
    # Palace diagonals
    for (c1, r1, c2, r2) in [(3, 0, 5, 2), (5, 0, 3, 2), (3, 7, 5, 9), (5, 7, 3, 9)]:
        draw.line([(PAD + c1 * CELL, PAD + r1 * CELL), (PAD + c2 * CELL, PAD + r2 * CELL)], fill=line_color)

    # Pieces
    for r in range(10):
        for c in range(9):
            p = board[r][c]
            if not p:
                continue
            cx, cy = PAD + c * CELL, PAD + r * CELL
            is_red = p.isupper()
            outline = '#cc0000' if is_red else '#1a1a1a'
            # Piece circle
            draw.ellipse([cx - R, cy - R, cx + R, cy + R], fill='#f5e6c8', outline=outline, width=2)
            # Piece name
            cn = CHINESE.get(p, p)
            if font:
                bbox = draw.textbbox((0, 0), cn, font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
                draw.text((cx - tw // 2, cy - th // 2), cn, fill=outline, font=font)
            else:
                # Fallback: colored dot indicating red/black
                dot_r = 8
                draw.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=outline)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return base64.b64encode(buf.getvalue()).decode()
