"""
MIDI -> ASCII Choir (.choir) converter.
- Determine tonality/key, time signature, tempo
- Convert MIDI notes to simplified notation with rhythm
- Supports triplets (三连音), quintuplets (五连音), 16th notes
- Ties (连音线) for notes spanning bars
- Bar-fill rest when quantization shortens a bar
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Optional

from mido import MidiFile

from src.instruments.instrument_registry import get_all_instruments, midi_to_simplified_notation


@dataclass
class NoteEvent:
    start_beat: Fraction
    duration: Fraction
    midis: list[int]


_MAJOR_SCALE_PCS = {0, 2, 4, 5, 7, 9, 11}

# Representable durations: 1/16, 1/8, 1/4, 1/2, 1; 1/3, 2/3; 1/5, 2/5, 3/5, 4/5
# Plus longer: 3/2, 2, 3, 4. Finer for rest fill: 1/32, 1/64, ...
_REPRESENTABLE = [
    Fraction(1, 1024), Fraction(1, 512), Fraction(1, 256), Fraction(1, 128), Fraction(1, 64),
    Fraction(1, 32), Fraction(1, 16), Fraction(1, 12), Fraction(1, 8), Fraction(1, 6), Fraction(1, 5),
    Fraction(1, 4), Fraction(2, 5), Fraction(1, 3), Fraction(3, 8), Fraction(3, 5),
    Fraction(1, 2), Fraction(2, 3), Fraction(3, 4), Fraction(4, 5),
    Fraction(1), Fraction(3, 2), Fraction(2), Fraction(5, 2), Fraction(3), Fraction(4),
]
_TOLERANCE = Fraction(1, 64)
_VALIDATOR_TOL = Fraction(1, 1000)  # validator 使用 0.001 容差


def _underscores_for_fraction(d: Fraction) -> int:
    """1/2->1, 1/4->2, 1/8->3, 1/16->4, 1/32->5, ..."""
    if d >= 1:
        return 0
    x = float(d)
    u = 1
    while u <= 12 and abs(x - 1 / (2**u)) > 0.0001:
        u += 1
    if u <= 12 and abs(x - 1 / (2**u)) <= 0.0001:
        return u
    return 4


def _largest_representable_le(x: Fraction) -> Fraction | None:
    """返回 <= x 的最大可表示时值，用于补休止时不超拍"""
    if x <= 0:
        return None
    for r in reversed(_REPRESENTABLE):
        if r <= x:
            return r
    return None


# 3/4、4/5 的 suffix 为 ""，parser 会解析为 1 拍，导致超拍。clamp 时需避开。
_AMBIGUOUS_SUFFIX = {Fraction(3, 4), Fraction(4, 5)}


def _largest_representable_le_safe(x: Fraction) -> Fraction | None:
    """同 _largest_representable_le，但避开 3/4、4/5（suffix 歧义）。"""
    if x <= 0:
        return None
    for r in reversed(_REPRESENTABLE):
        if r <= x and r not in _AMBIGUOUS_SUFFIX:
            return r
    return None


def _quantize_duration(d: Fraction) -> Fraction:
    """Quantize duration to nearest representable. Approximate smaller values to 1/16."""
    if d <= 0:
        return Fraction(1, 16)
    if d < Fraction(1, 32):
        return Fraction(1, 16)
    best = _REPRESENTABLE[0]
    best_err = abs(d - best)
    for r in _REPRESENTABLE:
        if r > d * 2:
            break
        err = abs(d - r)
        if err < best_err:
            best_err = err
            best = r
    return best


def _quantize_position(pos: Fraction, grid: int = 240) -> Fraction:
    """Quantize position to grid (240 = LCM-ish for 16,3,5)."""
    u = int(round(float(pos) * grid))
    return Fraction(u, grid)


def _parse_key_signature(key: Optional[str]) -> tuple[int, str] | None:
    if not key:
        return None
    k = key.strip()
    is_minor = False
    if k.endswith("m") and len(k) >= 2:
        is_minor = True
        k = k[:-1]
    base = k.upper()
    if len(base) >= 2 and base[1] in ("#", "B"):
        letter = base[0]
        acc = base[1]
    else:
        letter = base[0]
        acc = ""
    pc_map = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    if letter not in pc_map:
        return None
    pc = pc_map[letter]
    if acc == "#":
        pc = (pc + 1) % 12
    elif acc == "B":
        pc = (pc - 1) % 12
    if acc == "#":
        tonality = f"#{letter}"
    elif acc == "B":
        tonality = f"b{letter}"
    else:
        tonality = letter
    return pc, tonality


def _infer_key_from_pitches(pitches: list[int]) -> tuple[int, str]:
    if not pitches:
        return 0, "C"
    counts = [0] * 12
    for m in pitches:
        counts[m % 12] += 1
    best_pc = 0
    best_score = -1
    for tonic in range(12):
        score = 0
        for pc in _MAJOR_SCALE_PCS:
            score += counts[(pc + tonic) % 12]
        if score > best_score:
            best_score = score
            best_pc = tonic
    pc_to_label = {
        0: "C", 1: "#C", 2: "D", 3: "#D", 4: "E", 5: "F",
        6: "#F", 7: "G", 8: "#G", 9: "A", 10: "#A", 11: "B",
    }
    return best_pc, pc_to_label[best_pc]


def _chord_str(midis: list[int], tonality_offset: int) -> str:
    if not midis:
        return "0"
    parts = [midi_to_simplified_notation(m, tonality_offset) for m in sorted(midis)]
    return "/".join(parts)


def _decompose_duration(d: Fraction) -> list[tuple[Fraction, bool]] | None:
    """若 d 可分解为两个可表示时值之和，返回 [(a, True), (b, False)] 用于连音线表示；否则 None。
    优先用 2 的幂次（1/2, 1/4, 1/8...）避免歧义。"""
    if d <= 0:
        return None
    binary = [Fraction(1, 2**k) for k in range(0, 11)]  # 1, 1/2, 1/4, ...
    for a in binary:
        if a >= d:
            continue
        b = d - a
        if b in _REPRESENTABLE:
            return [(a, True), (b, False)]
    for a in reversed(_REPRESENTABLE):
        if a >= d or a in binary:
            continue
        b = d - a
        if b in _REPRESENTABLE:
            return [(a, True), (b, False)]
    return None


def _duration_to_rest_tokens(d: Fraction) -> list[str]:
    """将时值编码为休止 token 列表。可直接表示（如 1/2→0_）则单 token；可分解时用连音线（如 1.5→0 ~0_）。"""
    # 仅当 d 有明确后缀时才用单 token（排除 3/2、5/2 等，它们会误编码为 0-）
    if d in _REPRESENTABLE and (d < 1 or d == int(d)):
        return ["0" + _duration_to_suffix(d, False)]
    decomp = _decompose_duration(d)
    if decomp:
        result: list[str] = []
        for i, (part, _) in enumerate(decomp):
            suffix = _duration_to_suffix(part, tied_from_prev=False)
            result.append(("~" if i > 0 else "") + "0" + suffix)
        return result
    return ["0" + _duration_to_suffix(d, False)]


def _duration_to_suffix(d: Fraction, tied_from_prev: bool = False) -> str:
    """Encode duration as suffix: -, _, __, ___... Tuplets use ( )n. 可用连音线 ~ 分解时值。"""
    sym = "~" if tied_from_prev else ""
    if d >= 1:
        dashes = int(d) - 1
        return sym + "-" * min(dashes, 4)
    if d == Fraction(1, 2):
        return sym + "_"
    if d == Fraction(1, 4):
        return sym + "__"
    if d == Fraction(1, 8):
        return sym + "___"
    if d == Fraction(1, 16):
        return sym + "____"
    if d in (Fraction(1, 3), Fraction(1, 5)):
        return sym + ""
    if d == Fraction(2, 3):
        return sym + "_"
    if d == Fraction(1, 6):
        return sym + "___"
    if d == Fraction(1, 12):
        return sym + "____"
    if d == Fraction(2, 5):
        return sym + "_"
    if d == Fraction(3, 5):
        return sym + "_"
    if d == Fraction(4, 5):
        return sym + ""
    if d == Fraction(3, 8):
        return sym + "_"
    if d == Fraction(3, 4):
        return sym + ""
    if d >= Fraction(1, 2):
        return sym + "_"
    if d >= Fraction(1, 4):
        return sym + "__"
    if d >= Fraction(1, 8):
        return sym + "___"
    if d >= Fraction(1, 16):
        return sym + "____"
    n = 5
    while n <= 12:
        if abs(float(d) - 1 / (2**n)) < 0.0001:
            return sym + "_" * n
        n += 1
    return sym + "____"


def _split_event_across_bars(
    event: NoteEvent,
    beats_per_bar: Fraction,
) -> list[tuple[Fraction, Fraction, list[int], bool]]:
    """Split event at bar boundaries. Returns [(start, dur, midis, tied_from_prev), ...]"""
    result: list[tuple[Fraction, Fraction, list[int], bool]] = []
    start = event.start_beat
    remaining = event.duration
    first = True
    while remaining > 0:
        bar_idx = int(start // beats_per_bar) if beats_per_bar > 0 else 0
        bar_start = bar_idx * beats_per_bar
        pos_in_bar = start - bar_start
        left_in_bar = beats_per_bar - pos_in_bar
        seg = min(remaining, left_in_bar)
        if seg > 0:
            result.append((start, seg, event.midis, not first))
            first = False
        start += seg
        remaining -= seg
    return result


def _detect_tuplet_group(
    events: list[tuple[Fraction, Fraction, list[int], bool]],
    beats_per_bar: Fraction,
) -> list[list[tuple[Fraction, Fraction, list[int], bool]]]:
    """Group consecutive events into tuplets (3 or 5) when duration matches.
    Also (a b c)_: 3 consecutive 1/2 segments (may include rest) -> triplet with _ suffix."""
    groups: list[list[tuple[Fraction, Fraction, list[int], bool]]] = []
    i = 0
    while i < len(events):
        start, dur, midis, tied = events[i]
        n = 1
        if dur == Fraction(1, 3):
            while i + n < len(events):
                s2, d2, m2, t2 = events[i + n]
                if d2 == Fraction(1, 3) and abs((s2 + d2) - (start + dur * n)) < _TOLERANCE:
                    n += 1
                else:
                    break
            groups.append(events[i : i + n])
            i += n
            continue
        if dur == Fraction(1, 5):
            while i + n < len(events):
                s2, d2, m2, t2 = events[i + n]
                if d2 == Fraction(1, 5) and abs((s2 + d2) - (start + dur * n)) < _TOLERANCE:
                    n += 1
                else:
                    break
            groups.append(events[i : i + n])
            i += n
            continue
        # (a b c)_: 3 consecutive 1/2 segments (may include rest) -> triplet with _ suffix
        if dur == Fraction(1, 2) and i + 2 < len(events):
            s2, d2, m2, t2 = events[i + 1]
            s3, d3, m3, t3 = events[i + 2]
            if (
                d2 == Fraction(1, 2)
                and d3 == Fraction(1, 2)
                and abs((s2 + d2) - s3) < _TOLERANCE
                and abs((s3 + d3) - (start + Fraction(3, 2))) < _TOLERANCE
            ):
                groups.append(events[i : i + 3])
                i += 3
                continue
        groups.append([events[i]])
        i += 1
    return groups


def _encode_bar_events(
    segments: list[tuple[Fraction, Fraction, list[int], bool]],
    beats_per_bar: Fraction,
    tonality_offset: int,
    bar_start: Fraction = Fraction(0),
) -> tuple[list[str], Fraction]:
    """
    Encode segments into choir tokens. Returns (tokens, used_beats).
    Clamps segments to avoid overshoot; adds leading rest if first note is late; adds rest to fill bar.
    """
    tokens: list[str] = []
    used = Fraction(0)

    # Leading rest: if first segment starts after bar_start, add rest(s) to fill the gap
    if segments:
        first_start = segments[0][0]
        gap_start = first_start - bar_start
        while gap_start > _VALIDATOR_TOL:
            if gap_start in _REPRESENTABLE or _decompose_duration(gap_start):
                for t in _duration_to_rest_tokens(gap_start):
                    tokens.append(t)
                used += gap_start
                break
            rest_dur = _largest_representable_le(gap_start)
            if rest_dur is None or rest_dur <= 0:
                rest_dur = Fraction(1, 16)
            suffix = _duration_to_suffix(rest_dur, False)
            tokens.append("0" + suffix)
            used += rest_dur
            gap_start -= rest_dur

    groups = _detect_tuplet_group(segments, beats_per_bar)

    for group in groups:
        space_left = beats_per_bar - used
        if space_left <= _VALIDATOR_TOL:
            break

        if len(group) >= 1 and group[0][1] == Fraction(1, 3):
            # Triplet: each note = 1/3 beat
            max_notes = min(len(group), max(0, int(space_left / Fraction(1, 3))))
            if max_notes <= 0:
                break
            sub = group[:max_notes]
            syms = [_chord_str(m, tonality_offset) for _, _, m, tied in sub]
            tokens.append("(" + " ".join(syms) + ")3")
            used += len(sub) * Fraction(1, 3)
        elif len(group) >= 1 and group[0][1] == Fraction(1, 5):
            max_notes = min(len(group), max(0, int(space_left / Fraction(1, 5))))
            if max_notes <= 0:
                break
            sub = group[:max_notes]
            syms = [_chord_str(m, tonality_offset) for _, _, m, tied in sub]
            tokens.append("(" + " ".join(syms) + ")5")
            used += len(sub) * Fraction(1, 5)
        elif len(group) == 3 and group[0][1] == Fraction(1, 2):
            # (a b c)_: 3 eighths, may include rest
            syms = [_chord_str(m, tonality_offset) for _, _, m, tied in group]
            tokens.append("(" + " ".join(syms) + ")_")
            used += Fraction(3, 2)
        else:
            for start, dur, midis, tied in group:
                space_left = beats_per_bar - used
                if space_left <= _VALIDATOR_TOL:
                    break
                clamp_dur = min(dur, space_left)
                rep_dur = _largest_representable_le_safe(clamp_dur) or _largest_representable_le(clamp_dur) or _quantize_duration(clamp_dur)
                if rep_dur > space_left:
                    rep_dur = _largest_representable_le_safe(space_left) or _largest_representable_le(space_left) or rep_dur
                chord = _chord_str(midis, tonality_offset)
                suffix = _duration_to_suffix(rep_dur, tied)
                if chord.startswith("0") or chord == "0":
                    tokens.append("0" + suffix)
                else:
                    tokens.append(chord + suffix)
                used += rep_dur

    # Bar fill: 用 0~ 表示填满小节剩余拍数（parser 支持 0~ = 休止到小节末）
    gap = beats_per_bar - used
    if gap > _VALIDATOR_TOL:
        tokens.append("0~")
        used = beats_per_bar

    return tokens, used


def _build_line(
    events: list[NoteEvent],
    beats_per_bar: Fraction,
    tonality_offset: int,
    total_beats: Fraction,
) -> str:
    """Build choir line with ties, tuplets, 16th, and bar-fill rest."""
    if beats_per_bar <= 0:
        beats_per_bar = Fraction(4)

    # Quantize events
    q_events: list[NoteEvent] = []
    for ev in events:
        q_start = _quantize_position(ev.start_beat)
        q_dur = _quantize_duration(ev.duration)
        if q_dur <= 0:
            continue
        q_events.append(NoteEvent(start_beat=q_start, duration=q_dur, midis=ev.midis))

    # Split by bars and collect segments per bar
    all_segments: list[tuple[int, Fraction, Fraction, list[int], bool]] = []
    for ev in sorted(q_events, key=lambda e: (e.start_beat, -len(e.midis))):
        for start, dur, midis, tied in _split_event_across_bars(ev, beats_per_bar):
            bar_idx = int(start // beats_per_bar) if beats_per_bar > 0 else 0
            all_segments.append((bar_idx, start, dur, midis, tied))

    # Group by bar
    bars: dict[int, list[tuple[Fraction, Fraction, list[int], bool]]] = defaultdict(list)
    for bar_idx, start, dur, midis, tied in all_segments:
        bars[bar_idx].append((start, dur, midis, tied))

    # Sort segments within each bar
    for bar_idx in bars:
        bars[bar_idx].sort(key=lambda x: x[0])

    # Fill gaps with rest segments (preserves "rest inside tuplet" like (2/4 3/5 0)_)
    for bar_idx in bars:
        bar_start = bar_idx * beats_per_bar
        bar_end = bar_start + beats_per_bar
        segs = bars[bar_idx]
        filled: list[tuple[Fraction, Fraction, list[int], bool]] = []
        used_end = bar_start
        for start, dur, midis, tied in segs:
            gap = start - used_end
            if gap > _TOLERANCE:
                filled.append((used_end, gap, [], False))
            filled.append((start, dur, midis, tied))
            used_end = start + dur
        bars[bar_idx] = filled

    # Compute number of bars
    num_bars = int((total_beats + beats_per_bar - 1) // beats_per_bar) if total_beats > 0 else 1
    num_bars = max(num_bars, max(bars.keys()) + 1) if bars else 1

    # Encode each bar
    result_tokens: list[str] = []
    for bar_idx in range(num_bars):
        result_tokens.append("|")
        segs = bars.get(bar_idx, [])
        if segs:
            bar_start = bar_idx * beats_per_bar
            bar_tokens, _ = _encode_bar_events(segs, beats_per_bar, tonality_offset, bar_start)
            result_tokens.extend(bar_tokens)
        else:
            result_tokens.append("0" + "-" * (int(beats_per_bar) - 1) if beats_per_bar >= 1 else "0")
    result_tokens.append("|")

    return " ".join(result_tokens)


def _collect_notes(mid: MidiFile):
    ticks_per_beat = mid.ticks_per_beat
    notes_by_channel: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    # 同一 (channel, note) 可重叠：用队列存储多个 note_on 的 start，note_off 时配对最早的（FIFO）
    active: dict[tuple[int, int], list[int]] = defaultdict(list)
    program_by_channel: dict[int, int] = {}
    tempo = None
    time_sig = None
    key_sig = None

    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == "set_tempo" and tempo is None:
                tempo = msg.tempo
            elif msg.type == "time_signature" and time_sig is None:
                time_sig = (msg.numerator, msg.denominator)
            elif msg.type == "key_signature" and key_sig is None:
                key_sig = msg.key
            elif msg.type == "program_change":
                program_by_channel[msg.channel] = msg.program
            elif msg.type == "note_on" and msg.velocity > 0:
                active[(msg.channel, msg.note)].append(abs_tick)
            elif msg.type == "note_off":
                stack = active[(msg.channel, msg.note)]
                start = stack.pop(0) if stack else None
                if start is not None and abs_tick > start:
                    notes_by_channel[msg.channel].append((start, abs_tick, msg.note))
            elif msg.type == "note_on" and msg.velocity == 0:
                stack = active[(msg.channel, msg.note)]
                start = stack.pop(0) if stack else None
                if start is not None and abs_tick > start:
                    notes_by_channel[msg.channel].append((start, abs_tick, msg.note))

    return notes_by_channel, program_by_channel, tempo, time_sig, key_sig, ticks_per_beat


def _program_to_instrument(program: int, is_drum: bool) -> str:
    if is_drum:
        return "drums"
    if 0 <= program <= 7:
        return "grand_piano"
    if 24 <= program <= 31:
        return "guitar"
    if 32 <= program <= 39:
        return "bass_electric"
    if 40 <= program <= 51:
        return "violin"
    if 56 <= program <= 63:
        return "trumpet"
    if 68 <= program <= 71:
        return "oboe"
    if 64 <= program <= 71:
        return "alto_sax"
    if 72 <= program <= 79:
        return "clarinet"
    if 80 <= program <= 87:
        return "tenor_sax"
    return "grand_piano"


def _assign_voices(events: list[NoteEvent]) -> list[list[NoteEvent]]:
    voices: list[list[NoteEvent]] = []
    voice_ends: list[Fraction] = []
    for ev in sorted(events, key=lambda e: (e.start_beat, -e.duration)):
        assigned = False
        for i, end in enumerate(voice_ends):
            if ev.start_beat >= end - _TOLERANCE:
                voices[i].append(ev)
                voice_ends[i] = ev.start_beat + ev.duration
                assigned = True
                break
        if not assigned:
            voices.append([ev])
            voice_ends.append(ev.start_beat + ev.duration)
    return voices


def _token_duration(tok: str, base: Fraction = Fraction(1)) -> Fraction:
    """Parse a choir token and return its duration in beats. base=1 for quarter."""
    if not tok or tok == "|":
        return Fraction(0)
    if tok.startswith("(") and ")" in tok:
        # Tuplet: (a b c)3, (a b c d e)5, or (a b c)_ (3 eighths)
        close = tok.index(")")
        inner = tok[1:close]
        suffix = tok[close + 1 :].lstrip()
        count = len([x for x in inner.split() if x and not x.isspace()])
        if suffix.startswith("_"):
            # (a b c)_ = 3 eighths = 1.5 beats
            return count * Fraction(1, 2)
        n = int(suffix) if suffix and suffix[0].isdigit() else 3
        if n == 3:
            return count * Fraction(1, 3)
        if n == 5:
            return count * Fraction(1, 5)
        return count * Fraction(1, n) if n > 0 else Fraction(0)
    # Strip tie ~, accidentals, octave dots (e.g. ~0_, ..#6 -> 6)
    core = tok.lstrip("~").lstrip("#b^").lstrip(".").lstrip("#b^").rstrip("~")
    ext = sum(1 for c in core if c == "-")
    shrt = sum(1 for c in core if c == "_")
    core = core.rstrip("-_")
    if core.endswith("."):
        core = core.rstrip(".")
    if not core or core[0] not in "0123456789":
        return Fraction(0)
    return base * (1 + ext) * (Fraction(1, 2) ** shrt)


def _merge_consecutive_tuplets(tokens: list[str]) -> list[str]:
    """
    后处理：合并连续的单音三/五连音、八分、十六分。
    (1)3 (2)3 (5)3 (1)3 (2)3 (5)3 → (1 2 5 1 2 5)3
    1_ 2_ 5_ 1_ 2_ 5_ → (1 2 5 1 2 5)_
    1__ 2__ 5__ → (1 2 5)__
    """
    result: list[str] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        # (x)3 或 (x)5：单音连音（inner 无空格），合并连续同类型
        if t.startswith("(") and ")" in t:
            close = t.index(")")
            inner = t[1:close]
            suff = t[close + 1 :].lstrip()
            if suff in ("3", "5") and " " not in inner and len(inner) >= 1:
                group = [inner]
                j = i + 1
                while j < len(tokens):
                    nxt = tokens[j]
                    if nxt.startswith("(") and ")" in nxt:
                        nc = nxt.index(")")
                        ninner = nxt[1:nc]
                        nsuff = nxt[nc + 1 :].lstrip()
                        if nsuff == suff and " " not in ninner and len(ninner) >= 1:
                            group.append(ninner)
                            j += 1
                        else:
                            break
                    else:
                        break
                result.append("(" + " ".join(group) + ")" + suff)
                i = j
                continue

        # x_ 八分：合并连续单音八分（不含 ~ 开头的连音延续）
        if (
            t != "|"
            and t.endswith("_")
            and not t.endswith("__")
            and not t.startswith("~")
        ):
            group = [t[:-1]]
            j = i + 1
            while j < len(tokens):
                nxt = tokens[j]
                if (
                    nxt != "|"
                    and nxt.endswith("_")
                    and not nxt.endswith("__")
                    and not nxt.startswith("~")
                ):
                    group.append(nxt[:-1])
                    j += 1
                else:
                    break
            if len(group) >= 2:
                result.append("(" + " ".join(group) + ")_")
                i = j
                continue

        # x__ 十六分：合并连续单音十六分
        if (
            t != "|"
            and t.endswith("__")
            and not t.endswith("___")
            and not t.startswith("~")
        ):
            group = [t[:-2]]
            j = i + 1
            while j < len(tokens):
                nxt = tokens[j]
                if (
                    nxt != "|"
                    and nxt.endswith("__")
                    and not nxt.endswith("___")
                    and not nxt.startswith("~")
                ):
                    group.append(nxt[:-2])
                    j += 1
                else:
                    break
            if len(group) >= 2:
                result.append("(" + " ".join(group) + ")__")
                i = j
                continue

        result.append(t)
        i += 1
    return result


def _fix_bar_durations(
    bodies: list[list[str]],
    beats_per_bar: Fraction,
) -> list[list[str]]:
    """Post-process: add rest to any bar that sums to less than beats_per_bar."""
    tol = Fraction(1, 1000)
    result: list[list[str]] = []
    for tokens in bodies:
        bars: list[list[str]] = []
        current: list[str] = []
        for t in tokens:
            if t == "|":
                if current:
                    bars.append(current)
                current = []
            else:
                current.append(t)
        if current:
            bars.append(current)

        fixed: list[str] = []
        for bar in bars:
            if "0~" in bar:
                total = beats_per_bar
            else:
                total = sum(_token_duration(t) for t in bar)
            gap = beats_per_bar - total
            new_bar = list(bar)
            while gap > tol:
                new_bar.append("0~")
                break
            fixed.append("|")
            fixed.extend(new_bar)
        if fixed:
            fixed.append("|")
        result.append(fixed)
    return result


def _drop_empty_bars(bodies: list[list[str]]) -> list[list[str]]:
    if not bodies:
        return bodies
    bars_per_line: list[list[list[str]]] = []
    for tokens in bodies:
        bars: list[list[str]] = []
        current: list[str] = []
        for t in tokens:
            if t == "|":
                if current:
                    bars.append(current)
                current = []
            else:
                current.append(t)
        if current:
            bars.append(current)
        bars_per_line.append(bars)

    max_bars = max(len(b) for b in bars_per_line) if bars_per_line else 0
    keep: list[bool] = []
    for i in range(max_bars):
        any_note = False
        for line_bars in bars_per_line:
            if i >= len(line_bars):
                continue
            for t in line_bars[i]:
                if not t.startswith("0"):
                    any_note = True
                    break
            if any_note:
                break
        keep.append(any_note)

    rebuilt: list[list[str]] = []
    for line_bars in bars_per_line:
        out: list[str] = []
        for i, bar in enumerate(line_bars):
            if i < len(keep) and not keep[i]:
                continue
            out.append("|")
            out.extend(bar)
        if out:
            out.append("|")
            rebuilt.append(out)
        else:
            rebuilt.append([])
    return rebuilt


def midi_to_choir_text(mid_path: Path) -> str:
    mid = MidiFile(mid_path)
    notes_by_channel, program_by_channel, tempo, time_sig, key_sig, ticks_per_beat = _collect_notes(mid)

    all_pitches = [m for ch in notes_by_channel for _, _, m in notes_by_channel[ch]]
    key_info = _parse_key_signature(key_sig) or _infer_key_from_pitches(all_pitches)
    tonality_offset, tonality_label = key_info
    if tonality_label == "C":
        tonality_label = "0"

    numerator, denominator = time_sig or (4, 4)
    beats_per_bar = Fraction(numerator, 1)
    beat_factor = Fraction(4, denominator)

    bpm = 120
    if tempo:
        bpm = int(round(60_000_000 / tempo))

    instruments = get_all_instruments()

    parts: list[tuple[str, list[NoteEvent]]] = []
    total_beats = Fraction(0)

    try:
        from src.instruments.instrument_registry import can_play_note, can_play_chord
    except ImportError:
        can_play_note = can_play_chord = None

    for ch, items in sorted(notes_by_channel.items(), key=lambda x: x[0]):
        is_drum = ch == 9
        program = program_by_channel.get(ch, 0)
        inst = _program_to_instrument(program, is_drum)
        if inst not in instruments:
            inst = "grand_piano"
        events: list[NoteEvent] = []
        start_map: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for start, end, midi in items:
            start_map[start].append((end, midi))
        for start, entries in start_map.items():
            durations: dict[int, list[int]] = defaultdict(list)
            for end, midi in entries:
                durations[end - start].append(midi)
            for dur_ticks, midis in durations.items():
                start_beat = _quantize_position((start / ticks_per_beat) * beat_factor)
                dur_beat = _quantize_duration(Fraction(dur_ticks, ticks_per_beat) * beat_factor)
                if dur_beat <= 0:
                    continue
                events.append(NoteEvent(start_beat=start_beat, duration=dur_beat, midis=midis))
                end_beat = start_beat + dur_beat
                if end_beat > total_beats:
                    total_beats = end_beat
        if events and can_play_note and can_play_chord:
            for ev in events:
                if len(ev.midis) == 1:
                    if not can_play_note(inst, ev.midis[0]):
                        inst = "grand_piano"
                        break
                elif not can_play_chord(inst, ev.midis):
                    inst = "grand_piano"
                    break
        if events:
            parts.append((inst, events))

    header = [
        f"\\tonality{{{tonality_label}}}",
        f"\\beat{{{numerator}/{denominator}}}",
        f"\\bpm{{{bpm}}}",
        "",
    ]

    lines: list[tuple[str, list[str]]] = []
    for inst, events in parts:
        voices = _assign_voices(events)
        for voice in voices:
            line = _build_line(voice, beats_per_bar, tonality_offset, total_beats)
            prefix = f"& [{inst}]"
            tokens = line.split()
            lines.append((prefix, tokens))

    if lines:
        bodies = _drop_empty_bars([tokens for _, tokens in lines])
        bodies = _fix_bar_durations(bodies, beats_per_bar)
        bodies = [_merge_consecutive_tuplets(b) for b in bodies]
        lines = [(prefix, body) for (prefix, _), body in zip(lines, bodies) if body]

    output_lines: list[str] = []
    multi = len(lines) > 1
    for prefix, tokens in lines:
        body = " ".join(tokens)
        if multi:
            output_lines.append(f"{prefix} {body}")
        else:
            output_lines.append(body)

    return "\n".join(header + output_lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert MIDI files to ASCII Choir format")
    parser.add_argument("--input", default="src/midi", help="Input MIDI file or directory")
    parser.add_argument("--output", default="src/midi", help="Output directory")
    args = parser.parse_args()

    in_path = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    midi_files = []
    if in_path.is_file():
        midi_files = [in_path]
    else:
        midi_files = sorted(in_path.glob("*.mid"))
    if not midi_files:
        raise SystemExit("No MIDI files found")

    for midi_path in midi_files:
        choir_text = midi_to_choir_text(midi_path)
        out_path = out_dir / (midi_path.stem + ".choir")
        out_path.write_text(choir_text, encoding="utf-8")


if __name__ == "__main__":
    main()
