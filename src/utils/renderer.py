"""
带歌词简谱渲染器：将 ParsedScore 渲染为 JPG 图片。
支持上下布局（谱在上、词在下）和左右布局（谱在左、词在右）。
遵循真实乐谱：高音点在上方、低音点在下方；使用全角数字保证对齐。
"""
from pathlib import Path
from typing import Optional

from PIL import Image

from src.core.parser import (
    ParsedScore,
    Part,
    BarContent,
    NoteEvent,
    ChordEvent,
    RestEvent,
    GlissEvent,
    TrillEvent,
    _tonality_to_semitones,
    _PC_TO_DEGREE,
)

# 全角数字 ０１２３４５６７８９，保证 CJK 字体下对齐
_FULLWIDTH = "０１２３４５６７８９"
_BAR_FULLWIDTH = "｜"
# 升降号符号：0=无，1=♯，-1=♭
_ACC_SYMBOLS = {1: "♯", -1: "♭"}


def _midi_to_accidental(midi: int, tonality_offset: int) -> int:
    """根据 MIDI 与调性推算升降号。返回 0=无，1=升，-1=降。简谱惯例：Eb 用 3b，D# 用 2#"""
    base = midi - tonality_offset
    pc = base % 12
    if pc in _PC_TO_DEGREE:
        return 0
    if (pc + 1) % 12 in _PC_TO_DEGREE:
        return -1  # 降号（如 Eb=3b）
    if (pc - 1) % 12 in _PC_TO_DEGREE:
        return 1  # 升号（如 D#=2#）
    return 0


def _midi_to_display(midi: int, tonality_offset: int) -> tuple[str, int, int, int]:
    """
    将 MIDI 转为简谱显示。
    返回 (全角数字, 下方点数, 上方点数, accidental)。
    真实乐谱：低音点在下、高音点在上。accidental: 0=无，1=♯，-1=♭
    """
    base = midi - tonality_offset
    pc = base % 12
    octave = base // 12 - 5
    acc = _midi_to_accidental(midi, tonality_offset)
    if acc == 1:
        degree = _PC_TO_DEGREE.get((pc - 1) % 12, 1)
    elif acc == -1:
        degree = _PC_TO_DEGREE.get((pc + 1) % 12, 1)
    else:
        degree = _PC_TO_DEGREE.get(pc, 1)
    s = _FULLWIDTH[degree]
    dots_below = max(0, -octave)
    dots_above = max(0, octave)
    return s, dots_below, dots_above, acc


# 显示结构：单音/休止 (degree_str, dots_below, dots_above, accidental)；和弦 list of that
DisplayItem = tuple[str, int, int, int] | list[tuple[str, int, int, int]]


def _duration_to_beam_level(duration_beats: float, base_duration: float) -> int:
    """根据时值计算符尾数：四分=0，八分=1，十六分=2，三十二分=3"""
    if duration_beats <= 0:
        return 0
    ratio = duration_beats / base_duration
    if ratio >= 0.9:
        return 0
    if ratio >= 0.45:
        return 1
    if ratio >= 0.2:
        return 2
    return 3


def _assign_lyrics_to_notes(
    score: ParsedScore,
) -> tuple[list[list[list[tuple[DisplayItem, Optional[str], Optional[float], bool, bool, list[int], list[int]]]]], list[float]]:
    """
    将 \\lyrics 音节与音符对齐。连音线两个音符都渲染。
    返回: (sections[part_idx][bar_idx][(display_item, lyric, duration_beats, tied_to_next, tied_from_prev, midis, tied_from_prev_midis)], section_base_durations)
    """
    tonality_offset = _tonality_to_semitones(score.settings.tonality)
    sections = score.sections
    section_lyrics = score.section_lyrics or []
    section_settings = score.section_settings or []

    while len(section_lyrics) < len(sections):
        section_lyrics.append([])

    result: list[list[list[tuple[DisplayItem, Optional[str], Optional[float], bool, bool, list[int], list[int]]]]] = []
    base_durations: list[float] = []
    overflow_queues: dict[int, list[str]] = {}
    for sec_idx, sec_parts in enumerate(sections):
        sec_lyrics = section_lyrics[sec_idx] if sec_idx < len(section_lyrics) else []
        sec_settings = section_settings[sec_idx] if sec_idx < len(section_settings) else score.settings
        base_dur = 1.0  # 1 拍（分母定义的拍）
        base_durations.append(base_dur)
        for part_idx, s, _, _, _ in sec_lyrics:
            if part_idx not in overflow_queues:
                overflow_queues[part_idx] = []
            overflow_queues[part_idx].extend(s)

        sec_result: list[list[list[tuple[DisplayItem, Optional[str], Optional[float], bool, bool, list[int], list[int]]]]] = []
        for part_idx, part in enumerate(sec_parts):
            part_queue = overflow_queues.get(part_idx, [])
            overflow_queues[part_idx] = []
            part_result: list[list[tuple[DisplayItem, Optional[str], Optional[float], bool, bool, list[int], list[int]]]] = []
            for bar in part.bars:
                bar_result: list[tuple[DisplayItem, Optional[str], Optional[float], bool, bool, list[int], list[int]]] = []
                for ev in bar.events:
                    if isinstance(ev, RestEvent):
                        bar_result.append((("０", 0, 0, 0), None, ev.duration_beats, False, False, [], []))
                        continue
                    dur = getattr(ev, "duration_beats", None)
                    tied_to = getattr(ev, "tied_to_next", False)
                    tied_from = getattr(ev, "tied_from_prev", False)
                    if isinstance(ev, NoteEvent):
                        disp = _midi_to_display(ev.midi, tonality_offset)
                        lyric = ev.lyric
                        if lyric is None and part_queue:
                            lyric = part_queue.pop(0)
                        tied_midis = [ev.midi] if tied_from else []
                        bar_result.append((disp, lyric, dur, tied_to, tied_from, [ev.midi], tied_midis))
                    elif isinstance(ev, ChordEvent):
                        parts = [_midi_to_display(m, tonality_offset) for m in ev.midis]
                        lyric = ev.lyric
                        if lyric is None and part_queue:
                            lyric = part_queue.pop(0)
                        tied_midis = getattr(ev, "tied_from_prev_midis", [])
                        bar_result.append((parts, lyric, dur, tied_to, tied_from, ev.midis, tied_midis))
                    elif isinstance(ev, GlissEvent):
                        disp = _midi_to_display(ev.start_midi, tonality_offset)
                        bar_result.append((disp, None, ev.duration_beats, False, False, [ev.start_midi, ev.end_midi], []))
                    elif isinstance(ev, TrillEvent):
                        disp = _midi_to_display(ev.main_midi, tonality_offset)
                        alt = ev.lower_midi if ev.lower_midi is not None else ev.upper_midi
                        bar_result.append((disp, None, ev.duration_beats, False, False, [ev.main_midi, alt], []))
                part_result.append(bar_result)
            overflow_queues[part_idx] = part_queue
            sec_result.append(part_result)
        result.append(sec_result)

    return result, base_durations


def render_to_pil(
    score: ParsedScore,
    layout: str = "vertical",
    font_size: int = 20,
) -> "Image.Image":
    """
    渲染带歌词的简谱为 PIL Image，用于实时预览。
    layout: "vertical" 上下布局（谱在上词在下），"horizontal" 左右布局（谱在左词在右）
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise ImportError("请安装 Pillow: pip install Pillow")

    assigned, base_durations = _assign_lyrics_to_notes(score)
    sections = score.sections

    # 优先使用支持中日文的字体
    _font_paths = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
    ]
    font = font_small = font_acc = None
    for fp in _font_paths:
        try:
            if Path(fp).exists():
                font = ImageFont.truetype(fp, font_size)
                font_small = ImageFont.truetype(fp, font_size - 4)
                font_acc = ImageFont.truetype(fp, max(10, font_size // 2))  # 升降号小符号
                break
        except OSError:
            continue
    if font is None:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size - 4)
            font_acc = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", max(10, font_size // 2))
        except OSError:
            font = ImageFont.load_default()
            font_small = font_acc = font

    pad = 20
    line_height = font_size + 8
    lyric_height = font_size + 4

    # 和弦用缩小字体：2音 0.85 3音 0.75 4音 0.65
    def _chord_font(n: int):
        scale = [0, 0, 0.85, 0.75, 0.65][min(n, 4)]
        sz = max(10, int(font_size * scale))
        try:
            for fp in _font_paths:
                if Path(fp).exists():
                    return ImageFont.truetype(fp, sz)
        except OSError:
            pass
        return font

    def measure(s: str, f=font) -> tuple[int, int]:
        bbox = font.getbbox(s) if hasattr(font, "getbbox") else font.getsize(s)
        if hasattr(font, "getbbox"):
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        return bbox[0], bbox[1]

    # 每行：(DisplayItem, lyric, duration_beats, tied_to_next, tied_from_prev, midis, tied_from_prev_midis)
    rows: list[tuple[list[tuple[DisplayItem | str, Optional[str], Optional[float], bool, bool, list[int], list[int]]], int]] = []
    for sec_idx, sec_parts in enumerate(sections):
        for part_idx, part in enumerate(sec_parts):
            if part_idx >= len(assigned[sec_idx]):
                continue
            part_assigned = assigned[sec_idx][part_idx]
            row: list[tuple[DisplayItem | str, Optional[str], Optional[float], bool, bool, list[int], list[int]]] = []
            for bar_idx, bar_data in enumerate(part_assigned):
                row.append(("|", None, None, False, False, [], []))
                for disp, lyric, dur, tied_to, tied_from, midis, tied_midis in bar_data:
                    row.append((disp, lyric, dur, tied_to, tied_from, midis, tied_midis))
            row.append(("|", None, None, False, False, [], []))
            rows.append((row, sec_idx))
            if part_idx < len(sec_parts) - 1:
                rows.append(([], sec_idx))

    if not rows:
        rows = [([(("１", 0, 0, 0), None, None, False, False, [], []), (("２", 0, 0, 0), None, None, False, False, [], []), (("３", 0, 0, 0), None, None, False, False, [], []), (("４", 0, 0, 0), None, None, False, False, [], [])], 0)]

    gap = 10
    SECTION_GAP = 36  # 乐章之间的垂直间距
    beam_spacing = 4  # 符尾横线间距
    beam_line_height = 2  # 每层符尾高度
    dot_r = 2
    dot_offset = 4
    draw_offset_y = -5  # 字体偏上修正

    def _draw_single_note(draw, x: int, y: int, s: str, dots_below: int, dots_above: int, font, r=dot_r, off=dot_offset, text_offset_y: int = 0, accidental: int = 0, font_acc=None) -> tuple[int, int]:
        """绘制单个音符（含高低音点、升降号）。升降号以小符号绘于数字左上角，不改变数字位置"""
        acc_sym = _ACC_SYMBOLS.get(accidental, "")
        if acc_sym and font_acc:
            acc_bbox = font_acc.getbbox(acc_sym) if hasattr(font_acc, "getbbox") else (0, 0, font_acc.getsize(acc_sym)[0], font_acc.getsize(acc_sym)[1])
            acc_h = (acc_bbox[3] - acc_bbox[1]) if hasattr(font_acc, "getbbox") else font_acc.getsize(acc_sym)[1]
            draw.text((x, y - acc_h - 1), acc_sym, fill=(0, 0, 0), font=font_acc)
        bbox = font.getbbox(s) if hasattr(font, "getbbox") else (0, 0, font.getsize(s)[0], font.getsize(s)[1])
        if hasattr(font, "getbbox"):
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        else:
            w, h = bbox[0], bbox[1]
        draw.text((x, y + text_offset_y), s, fill=(0, 0, 0), font=font)
        num_center_x = x + w // 2
        num_top = y
        num_bottom = y + h
        for _ in range(dots_above):
            draw.ellipse(
                (num_center_x - r, num_top - off - r * 2,
                 num_center_x + r, num_top - off),
                fill=(0, 0, 0),
            )
            num_top -= off + r * 2
        for _ in range(dots_below):
            draw.ellipse(
                (num_center_x - r, num_bottom + off,
                 num_center_x + r, num_bottom + off + r * 2),
                fill=(0, 0, 0),
            )
            num_bottom += off + r * 2
        return w, h

    sustain_line_width = measure("１")[0]  # 增时线每拍宽度

    def _measure_item(item: DisplayItem | str, dur: Optional[float] = None, base_dur: float = 1.0) -> int:
        if item == "|":
            return measure(_BAR_FULLWIDTH)[0]
        if isinstance(item, list):
            n = min(len(item), 4)
            cf = _chord_font(n)
            def _w(s):
                b = cf.getbbox(s) if hasattr(cf, "getbbox") else None
                return b[2] - b[0] if b else cf.getsize(s)[0]
            w = max(_w(s) for s, _, _, _ in item[:4]) if item else measure("１")[0]
        else:
            w = measure(item[0])[0]
        # 增时线：每多一拍加一条横线宽度
        if dur is not None and base_dur > 0 and dur > base_dur:
            extra = max(0, int((dur / base_dur) - 1))
            w += extra * sustain_line_width
        return w

    def _get_note_positions(disp: DisplayItem | str, x: int, y: int, ch: int) -> tuple[list[int], int]:
        """返回 (各音符的 y 坐标列表, 宽度)。用于连音弧线定位，顺序与和弦音符一致"""
        ch = ch or line_height
        row_center = y + ch // 2
        items = [disp] if isinstance(disp, tuple) else disp
        if not isinstance(disp, list) or len(items) <= 1:
            w = measure(items[0][0])[0] if isinstance(disp, tuple) else measure(disp[0][0])[0]
            return [row_center], w
        n = min(len(items), 4)
        cf = _chord_font(n)
        scale = [0, 0, 0.85, 0.75, 0.65][n]
        single_h = (cf.getbbox("１")[3] - cf.getbbox("１")[1]) if hasattr(cf, "getbbox") else int(font_size * scale) + 4
        step_scale = [0, 0, 1.0, 0.95, 0.85][n]
        step = max(6, int(single_h * step_scale))
        total_h = step * (n - 1) + single_h
        start_y = row_center - total_h // 2
        def _cw(s):
            b = cf.getbbox(s) if hasattr(cf, "getbbox") else None
            return b[2] - b[0] if b else cf.getsize(s)[0]
        single_w = max(_cw(s) for s, _, _, _ in items[:4])
        ys = [start_y + i * step + single_h // 2 for i in range(n)]
        return ys, single_w

    def _chord_height(item: DisplayItem | str) -> int:
        """和弦占用高度（堆叠时，字体已压缩）"""
        if not isinstance(item, list) or len(item) <= 1:
            return line_height
        n = min(len(item), 4)
        cf = _chord_font(n)
        single_h = (cf.getbbox("１")[3] - cf.getbbox("１")[1]) if hasattr(cf, "getbbox") else font_size + 4
        step_scale = [0, 0, 1.0, 0.95, 0.85][n]
        step = max(6, int(single_h * step_scale))
        return step * (n - 1) + single_h

    def _draw_note(draw, x: int, y: int, item: DisplayItem | str, font, font_small, content_h: int = 0, num_offset_y: int = 0) -> int:
        """num_offset_y: 仅对音符数字应用的垂直偏移（修正字体偏上）"""
        ch = content_h or line_height
        if item == "|":
            bw, bh = measure(_BAR_FULLWIDTH)[0], measure(_BAR_FULLWIDTH)[1]
            draw_y = y + ch // 2 - bh // 2
            draw.text((x, draw_y), _BAR_FULLWIDTH, fill=(0, 0, 0), font=font)
            return bw
        items: list[tuple[str, int, int]] = [item] if isinstance(item, tuple) else item
        row_center = y + ch // 2
        if len(items) <= 1:
            s, db, da, acc = items[0][0], items[0][1], items[0][2], items[0][3] if len(items[0]) > 3 else 0
            w = measure(s)[0]
            h = (font.getbbox(s)[3] - font.getbbox(s)[1]) if hasattr(font, "getbbox") else font_size + 4
            base_y = row_center - h // 2
            _draw_single_note(draw, x, base_y, s, db, da, font, text_offset_y=num_offset_y, accidental=acc, font_acc=font_acc)
            return w
        # 和弦：上下堆叠，最多 4 个，字体与点按比例缩小
        notes = items[:4]
        n = len(notes)
        cf = _chord_font(n)
        scale = [0, 0, 0.85, 0.75, 0.65][n]
        cr = max(1, int(dot_r * scale))
        coff = max(2, int(dot_offset * scale))
        def _cw(s):
            b = cf.getbbox(s) if hasattr(cf, "getbbox") else None
            return b[2] - b[0] if b else cf.getsize(s)[0]
        single_w = max(_cw(s) for s, _, _, _ in notes)
        single_h = (cf.getbbox("１")[3] - cf.getbbox("１")[1]) if hasattr(cf, "getbbox") else int(font_size * scale) + 4
        step_scale = [0, 0, 1.0, 0.95, 0.85][n]
        step = max(6, int(single_h * step_scale))
        total_h = step * (n - 1) + single_h
        start_y = row_center - total_h // 2
        for i, note_item in enumerate(notes):
            s, dots_below, dots_above = note_item[0], note_item[1], note_item[2]
            acc = note_item[3] if len(note_item) > 3 else 0
            ny = start_y + i * step
            cx = x + (single_w - _cw(s)) // 2
            _draw_single_note(draw, cx, ny, s, dots_below, dots_above, cf, r=cr, off=coff, text_offset_y=num_offset_y, accidental=acc, font_acc=font_acc)
        return single_w

    def row_width(row_items: list, sec_idx: int) -> int:
        base_dur = base_durations[sec_idx] if sec_idx < len(base_durations) else 1.0
        return sum(_measure_item(d, dur, base_dur) + gap for d, _, dur, _, _, _, _ in row_items)

    def _bar_widths_in_row(row_items: list, sec_idx: int) -> list[int]:
        """按小节拆分，返回每小节内容宽度（不含小节线）"""
        base_dur = base_durations[sec_idx] if sec_idx < len(base_durations) else 1.0
        widths: list[int] = []
        current = 0
        for d, _, dur, _, _, _, _ in row_items:
            if d == "|":
                if current > 0:
                    widths.append(current)
                current = 0
            else:
                if current > 0:
                    current += gap
                current += _measure_item(d, dur, base_dur)
        if current > 0:
            widths.append(current)
        return widths

    # 每乐章内小节号对齐：按小节取各声部最大宽度
    section_bar_widths: dict[int, list[int]] = {}
    for row_items, sec_idx in rows:
        if not row_items:
            continue
        bw = _bar_widths_in_row(row_items, sec_idx)
        if sec_idx not in section_bar_widths:
            section_bar_widths[sec_idx] = [0] * len(bw)
        for j, w in enumerate(bw):
            if j < len(section_bar_widths[sec_idx]):
                section_bar_widths[sec_idx][j] = max(section_bar_widths[sec_idx][j], w)
            else:
                section_bar_widths[sec_idx].append(w)

    bar_width = measure(_BAR_FULLWIDTH)[0]

    def _iter_items_with_x(row_items: list, sec_idx: int):
        """按小节对齐后的 x 坐标迭代每个 item"""
        base_dur = base_durations[sec_idx] if sec_idx < len(base_durations) else 1.0
        widths = section_bar_widths.get(sec_idx, [])
        x = pad
        bar_start = None
        bar_idx = 0
        for item in row_items:
            disp, lyric, dur, tied_to, tied_from, midis, tied_midis = item
            if disp == "|":
                if bar_start is not None and bar_idx < len(widths):
                    bar_content = x - bar_start
                    pad_amt = widths[bar_idx] - bar_content
                    if pad_amt > 0:
                        x += pad_amt
                yield (x, item)
                x += bar_width + gap
                bar_start = x
                if bar_start is not None:  # 非首个 | 时，结束当前小节，递增 bar_idx
                    bar_idx += 1
            else:
                w = _measure_item(disp, dur, base_dur)
                yield (x, item)
                x += w + gap

    def _has_accidental(disp: DisplayItem | str) -> bool:
        """检查是否有升降号"""
        if disp == "|" or not disp:
            return False
        items = [disp] if isinstance(disp, tuple) else disp
        for it in (items[:4] if isinstance(items, list) else [items]):
            if len(it) > 3 and it[3] != 0:
                return True
        return False

    def row_height(row_items: list, sec_idx: int) -> int:
        has_lyric = any(ly for _, ly, _, _, _, _, _ in row_items if ly)
        has_acc = any(_has_accidental(d) for d, _, _, _, _, _, _ in row_items if d != "|")
        base = line_height + (max(8, font_size // 2) if has_acc else 0)
        chord_h = max((_chord_height(d) for d, _, _, _, _, _, _ in row_items if isinstance(d, list) and len(d) > 1), default=0)
        base_dur = base_durations[sec_idx] if sec_idx < len(base_durations) else 1.0
        max_beam = 0
        for _, _, dur, _, _, _, _ in row_items:
            if dur is not None and dur > 0:
                max_beam = max(max_beam, _duration_to_beam_level(dur, base_dur))
        beam_extra = max_beam * (beam_spacing + beam_line_height) if max_beam > 0 else 0
        return max(base, chord_h) + beam_extra + (lyric_height if has_lyric else 0)

    # 小节对齐后每行宽度 = 各小节目标宽度之和 + 小节线
    def _aligned_row_width(sec_idx: int) -> int:
        wds = section_bar_widths.get(sec_idx, [])
        return len(wds) * (bar_width + gap) + sum(wds) if wds else 0

    max_w = max((_aligned_row_width(si) for r, si in rows if r), default=400)
    _prev_s = None
    _section_gaps = 0
    for _r, _si in rows:
        if not _r:
            continue
        if _prev_s is not None and _si > _prev_s:
            _section_gaps += SECTION_GAP
        _prev_s = _si
    total_h = sum(row_height(r, si) for r, si in rows) + 20 + _section_gaps

    if layout == "horizontal":
        lyric_col_w = max(
            sum(measure(ly or "")[0] + gap for _, ly, _, _, _, _, _ in r if ly) for r, _ in rows if r
        ) if rows else 200
        img_w = max_w + 30 + lyric_col_w + pad * 2
    else:
        img_w = max_w + pad * 2
    img_h = total_h + pad * 2

    img = Image.new("RGB", (img_w, img_h), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    lyric_col_start = max_w + 30 + pad if layout == "horizontal" else 0

    y = pad
    prev_sec_idx: int | None = None
    for row_items, sec_idx in rows:
        if not row_items:
            y += 10
            continue
        if prev_sec_idx is not None and sec_idx > prev_sec_idx:
            y += SECTION_GAP
        prev_sec_idx = sec_idx
        line_h = row_height(row_items, sec_idx)
        has_lyric = any(ly for _, ly, _, _, _, _, _ in row_items if ly)
        content_h = line_h - (lyric_height if has_lyric else 0)
        base_dur = base_durations[sec_idx] if sec_idx < len(base_durations) else 1.0

        # 收集音符位置与符尾层数，用于绘制符杠（使用小节对齐后的 x）
        note_positions: list[tuple[int, int, int]] = []  # (x, w, beam_level)
        for x_pos, (disp, lyric, dur, _, _, _, _) in _iter_items_with_x(row_items, sec_idx):
            if disp != "|":
                beam_level = _duration_to_beam_level(dur, base_dur) if dur is not None and dur > 0 else 0
                note_positions.append((x_pos, _measure_item(disp, dur, base_dur), beam_level))

        # 绘制音符、增时线、连音弧线、歌词
        sustain_line_thick = max(1, font_size // 12)
        tie_arc_height = max(4, font_size // 5)
        prev_tied_x = prev_tied_w = prev_disp = prev_midis = prev_ys = None
        if layout == "vertical":
            for x, (disp, lyric, dur, tied_to, tied_from, midis, tied_midis) in _iter_items_with_x(row_items, sec_idx):
                if disp != "|" and tied_from and prev_tied_x is not None:
                    curr_ys, _ = _get_note_positions(disp, x, y, content_h)
                    if tied_midis and prev_midis is not None and prev_ys is not None:
                        for m in tied_midis:
                            try:
                                prev_idx = prev_midis.index(m)
                                curr_idx = midis.index(m)
                                arc_y = (prev_ys[prev_idx] + curr_ys[curr_idx]) // 2
                                x1, x2 = prev_tied_x + prev_tied_w, x
                                draw.arc((x1, arc_y - tie_arc_height, x2, arc_y + tie_arc_height), 180, 0, fill=(0, 0, 0), width=max(1, sustain_line_thick))
                            except ValueError:
                                pass
                    else:
                        arc_y = y + content_h // 2
                        x1, x2 = prev_tied_x + prev_tied_w, x
                        draw.arc((x1, arc_y - tie_arc_height, x2, arc_y + tie_arc_height), 180, 0, fill=(0, 0, 0), width=max(1, sustain_line_thick))
                note_w = _draw_note(draw, x, y, disp, font, font_small, content_h, draw_offset_y)
                if disp != "|":
                    if tied_to:
                        prev_tied_x, prev_tied_w = x, note_w
                        prev_ys, _ = _get_note_positions(disp, x, y, content_h)
                        prev_disp, prev_midis = disp, midis
                    else:
                        prev_tied_x = prev_tied_w = prev_disp = prev_midis = prev_ys = None
                # 小节线 | 不重置 prev_tied，以支持跨小节连音
                # 增时线（横线延音）：1- 一条线，1-- 两条线
                if disp != "|" and dur is not None and base_dur > 0 and dur > base_dur:
                    extra = int((dur / base_dur) - 1)
                    sustain_y = y + content_h // 2
                    for i in range(extra):
                        x1 = x + note_w + i * sustain_line_width
                        x2 = x + note_w + (i + 1) * sustain_line_width
                        draw.line([(x1, sustain_y), (x2, sustain_y)], fill=(0, 0, 0), width=max(1, sustain_line_thick))
                if lyric:
                    draw.text((x, y + content_h), lyric, fill=(80, 80, 80), font=font_small)
        else:
            lyric_x = lyric_col_start
            for x, (disp, lyric, dur, tied_to, tied_from, midis, tied_midis) in _iter_items_with_x(row_items, sec_idx):
                if disp != "|" and tied_from and prev_tied_x is not None:
                    curr_ys, _ = _get_note_positions(disp, x, y, content_h)
                    if tied_midis and prev_midis is not None and prev_ys is not None:
                        for m in tied_midis:
                            try:
                                prev_idx = prev_midis.index(m)
                                curr_idx = midis.index(m)
                                arc_y = (prev_ys[prev_idx] + curr_ys[curr_idx]) // 2
                                x1, x2 = prev_tied_x + prev_tied_w, x
                                draw.arc((x1, arc_y - tie_arc_height, x2, arc_y + tie_arc_height), 180, 0, fill=(0, 0, 0), width=max(1, sustain_line_thick))
                            except ValueError:
                                pass
                    else:
                        arc_y = y + content_h // 2
                        x1, x2 = prev_tied_x + prev_tied_w, x
                        draw.arc((x1, arc_y - tie_arc_height, x2, arc_y + tie_arc_height), 180, 0, fill=(0, 0, 0), width=max(1, sustain_line_thick))
                note_w = _draw_note(draw, x, y, disp, font, font_small, content_h, draw_offset_y)
                if disp != "|":
                    if tied_to:
                        prev_tied_x, prev_tied_w = x, note_w
                        prev_ys, _ = _get_note_positions(disp, x, y, content_h)
                        prev_disp, prev_midis = disp, midis
                    else:
                        prev_tied_x = prev_tied_w = prev_disp = prev_midis = prev_ys = None
                if disp != "|" and dur is not None and base_dur > 0 and dur > base_dur:
                    extra = int((dur / base_dur) - 1)
                    sustain_y = y + content_h // 2
                    for i in range(extra):
                        x1 = x + note_w + i * sustain_line_width
                        x2 = x + note_w + (i + 1) * sustain_line_width
                        draw.line([(x1, sustain_y), (x2, sustain_y)], fill=(0, 0, 0), width=sustain_line_thick)
                if disp == "|":
                    draw.text((lyric_x, y), _BAR_FULLWIDTH, fill=(100, 100, 100), font=font_small)
                elif lyric:
                    draw.text((lyric_x, y), lyric, fill=(80, 80, 80), font=font_small)
                lyric_x += (measure(_BAR_FULLWIDTH if disp == "|" else (lyric or ""), font_small)[0]) + gap

        # 绘制符杠：按小节分组，每组内找连续符尾组并画线
        bar_groups: list[list[tuple[int, int, int]]] = []
        current: list[tuple[int, int, int]] = []
        pos_idx = 0
        for disp, _, _, _, _, _, _ in row_items:
            if disp == "|":
                if current:
                    bar_groups.append(current)
                    current = []
            else:
                if pos_idx < len(note_positions):
                    current.append(note_positions[pos_idx])
                    pos_idx += 1
        if current:
            bar_groups.append(current)

        # 对每个小节内的音符找连续符尾组
        beam_base_y = y + content_h - beam_spacing  # 第一层符杠 y（符杠在音符下方）
        for bar_notes in bar_groups:
            i = 0
            while i < len(bar_notes):
                if bar_notes[i][2] < 1:
                    i += 1
                    continue
                j = i
                while j < len(bar_notes) and bar_notes[j][2] >= 1:
                    j += 1
                group = bar_notes[i:j]
                max_level = max(n[2] for n in group)
                for level in range(1, max_level + 1):
                    # 该层有符尾的音符范围
                    sub = [(nx, nw, bl) for nx, nw, bl in group if bl >= level]
                    if not sub:
                        continue
                    x1 = sub[0][0]
                    x2 = sub[-1][0] + sub[-1][1]
                    by = int(beam_base_y + (level - 1) * (beam_spacing + beam_line_height))
                    draw.line([(x1, by), (x2, by)], fill=(0, 0, 0), width=max(1, beam_line_height))
                i = j

        y += line_h

    return img


def render_to_image(
    score: ParsedScore,
    output_path: str | Path,
    layout: str = "vertical",
    font_size: int = 24,
) -> Path:
    """
    渲染带歌词的简谱为 JPG 文件。
    layout: "vertical" 上下布局（谱在上词在下），"horizontal" 左右布局（谱在左词在右）
    """
    img = render_to_pil(score, layout=layout, font_size=font_size)
    out = Path(output_path)
    if out.suffix.lower() not in (".jpg", ".jpeg"):
        out = out.with_suffix(".jpg")
    img.save(str(out), "JPEG", quality=90)
    return out
