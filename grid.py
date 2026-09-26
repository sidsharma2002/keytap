import string

from PIL import Image, ImageDraw, ImageFont

from config import ARGS, CURSOR_MODE


class GridRenderer:
    """Composites the grid overlay onto a PIL frame. Stateless except for font."""

    def __init__(self, win_w, win_h):
        self.font = self._load_font(win_w, win_h)
        self.elem_font = self._load_font_fixed(10)

    def _load_font_fixed(self, size):
        for path in [
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/Monaco.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
        return ImageFont.load_default()

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

    def composite(self, frame, cursor_row, cursor_col, input_buf, elements=None, highlight_bounds=None):
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

        if elements:
            self._draw_elements(draw, elements)

        if highlight_bounds:
            x1, y1, x2, y2 = highlight_bounds
            draw.rectangle([x1, y1, x2, y2], fill=(255, 200, 0, 25))
            draw.rectangle([x1, y1, x2, y2], outline=(255, 200, 0, 230), width=2)

        return Image.alpha_composite(base, overlay).convert("RGB")

    def _draw_elements(self, draw, elements):
        show_bounds = elements[0].get('show_bounds', True) if elements else True
        for el in elements:
            x1, y1, x2, y2 = el['wx1'], el['wy1'], el['wx2'], el['wy2']
            if el['active']:
                if show_bounds:
                    outline = (0, 210, 255, 220) if el['clickable'] else (160, 160, 255, 180)
                    draw.rectangle([x1, y1, x2, y2], fill=(0, 210, 255, 22))
                    draw.rectangle([x1, y1, x2, y2], outline=outline, width=2)
                self._draw_element_label(draw, x1, y1, el['display_label'], active=True)
            else:
                if show_bounds:
                    draw.rectangle([x1, y1, x2, y2], outline=(80, 80, 80, 100), width=1)
                self._draw_element_label(draw, x1, y1, el['display_label'], active=False)

    def _draw_element_label(self, draw, x0, y0, label, active=True):
        pad = 4
        try:
            bbox = self.elem_font.getbbox(label)
            bl, bt, br, bb = bbox
            tw, th = br - bl, bb - bt
        except AttributeError:
            bl, bt, tw, th = 0, 0, len(label) * 9, 14
        px0, py0 = x0 + 2, y0 + 2
        px1, py1 = px0 + tw + pad * 2, py0 + th + pad * 2
        # offset text by -bl, -bt so glyph sits flush inside the pill
        tx, ty = px0 + pad - bl, py0 + pad - bt
        if active:
            draw.rectangle([px0, py0, px1, py1], fill=(0, 0, 0, 255))
            draw.text((tx, ty), label, fill=(255, 230, 0, 255), font=self.elem_font)
        else:
            draw.rectangle([px0, py0, px1, py1], fill=(0, 0, 0, 160))
            draw.text((tx, ty), label, fill=(80, 80, 80, 200), font=self.elem_font)

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
