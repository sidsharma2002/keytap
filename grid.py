import string

from PIL import Image, ImageDraw, ImageFont

from config import ARGS, CURSOR_MODE


class GridRenderer:
    """Composites the grid overlay onto a PIL frame. Stateless except for font."""

    def __init__(self, win_w, win_h):
        self.font = self._load_font(win_w, win_h)

    def _load_font(self, win_w, win_h):
        fs = max(8, int(min(win_w / ARGS.cols, win_h / ARGS.rows) // 5))
        for path in [
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/Monaco.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]:
            try:
                return ImageFont.truetype(path, fs)
            except Exception:
                pass
        return ImageFont.load_default()

    def composite(self, frame, cursor_row, cursor_col, input_buf):
        base = frame.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        W, H = base.size
        cw = W / ARGS.cols
        ch = H / ARGS.rows
        typed = ''.join(input_buf).upper()
        row_selected = len(typed) == 1

        for r in range(ARGS.rows):
            row_letter = string.ascii_uppercase[r] if r < 26 else None
            row_match = row_selected and row_letter and row_letter == typed[0]

            for c in range(ARGS.cols):
                x0, y0 = int(c * cw), int(r * ch)
                x1, y1 = int((c + 1) * cw), int((r + 1) * ch)
                is_cursor = CURSOR_MODE and r == cursor_row and c == cursor_col
                label = f"{row_letter}{string.ascii_uppercase[c]}" if row_letter else None

                if not typed:
                    draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(255, 255, 255, 38))
                    if label and not CURSOR_MODE:
                        self._draw_pill(draw, x0, y0, label, active=False)
                elif row_match:
                    draw.rectangle([x0, y0, x1, y1], fill=(255, 215, 0, 28))
                    draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(255, 215, 0, 200))
                    if label and not CURSOR_MODE:
                        self._draw_pill(draw, x0, y0, label, active=True)
                else:
                    draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(255, 255, 255, 15))
                    if label and not CURSOR_MODE:
                        self._draw_pill(draw, x0, y0, label, active=None)

                if is_cursor:
                    draw.rectangle([x0, y0, x1, y1], fill=(0, 255, 80, 25))
                    draw.rectangle([x0 + 1, y0 + 1, x1 - 2, y1 - 2],
                                   outline=(0, 255, 80, 255), width=2)

        return Image.alpha_composite(base, overlay).convert("RGB")

    def _draw_pill(self, draw, x0, y0, label, active=False):
        pad = 3
        try:
            bbox = self.font.getbbox(label)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:
            tw, th = len(label) * 7, 10
        px0, py0 = x0 + 3, y0 + 3
        px1, py1 = px0 + tw + pad * 2, py0 + th + pad * 2
        if active is True:
            bg, color = (0, 0, 0, 200), (0, 255, 110, 255)
        elif active is False:
            bg, color = (0, 0, 0, 140), (0, 200, 75, 255)
        else:
            bg, color = (0, 0, 0, 70),  (0, 150, 55, 255)
        draw.rectangle([px0, py0, px1, py1], fill=bg)
        draw.text((px0 + pad, py0 + pad), label, fill=color, font=self.font)
