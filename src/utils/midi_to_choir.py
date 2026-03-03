"""
MIDI -> ASCII Choir (.choir) converter.
- Determine tonality/key, time signature, tempo
- Convert MIDI notes to simplified notation with rhythm
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


def _parse_key_signature(key: Optional[str]) -> tuple[int, str] | None:
    if not key:
        return None
    k = key.strip()
    # mido uses e.g. "C", "F#", "Bb", "Am", "F#m"
    is_minor = False
    if k.endswith("m") and len(k) >= 2:
        is_minor = True
        k = k[:-1]
    base = k.upper()
    # Normalize flats/sharps in base
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
    elif acc == "B":  # flat
        pc = (pc - 1) % 12
    # Output tonality string (prefer #/b prefix style)
    if acc == "#":
        tonality = f"#{letter}"
    elif acc == "B":
        tonality = f"b{letter}"
    else:
        tonality = letter
    # Minor keys still use tonic offset for simplified notation
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
    # Map tonic to tonality label (prefer flats for 1,3,6,8,10?)
    pc_to_label = {
        0: "C",
        1: "#C",
        2: "D",
        3: "#D",
        4: "E",
        5: "F",
        6: "#F",
        7: "G",
        8: "#G",
        9: "A",
        10: "#A",
        11: "B",
    }
    return best_pc, pc_to_label[best_pc]


def _quantize(value: float, denom: int = 16) -> Fraction:
    return Fraction(int(round(value * denom)), denom)


def _chord_str(midis: list[int], tonality_offset: int) -> str:
    parts = [midi_to_simplified_notation(m, tonality_offset) for m in sorted(midis)]
    return "/".join(parts)


def _split_event_across_bars(
    event: NoteEvent,
    beats_per_bar: Fraction,
) -> list[tuple[Fraction, Fraction, list[int]]]:
    result: list[tuple[Fraction, Fraction, list[int]]] = []
    start = event.start_beat
    remaining = event.duration
    while remaining > 0:
        pos_in_bar = start % beats_per_bar
        left_in_bar = beats_per_bar - pos_in_bar
        seg = remaining if remaining <= left_in_bar else left_in_bar
        result.append((start, seg, event.midis))
        start += seg
        remaining -= seg
    return result


def _build_line(
    events: list[NoteEvent],
    beats_per_bar: Fraction,
    tonality_offset: int,
    total_beats: Fraction,
) -> str:
    # Simple syntax: quantize to 1/16 beat, no ~. Use "_" and "-" only.
    units_per_beat = 16
    units_per_bar = int(beats_per_bar * units_per_beat) if beats_per_bar > 0 else 64

    total_units = int(_quantize(float(total_beats), units_per_beat) * units_per_beat)
    if total_units <= 0:
        total_units = units_per_bar
    if total_units % units_per_bar != 0:
        total_units += units_per_bar - (total_units % units_per_bar)

    timeline: list[str] = ["0"] * total_units
    for ev in sorted(events, key=lambda e: (e.start_beat, -len(e.midis))):
        start_u = int(ev.start_beat * units_per_beat)
        dur_u = int(max(1, ev.duration * units_per_beat))
        if start_u >= total_units:
            continue
        end_u = min(total_units, start_u + dur_u)
        chord = _chord_str(ev.midis, tonality_offset)
        for u in range(start_u, end_u):
            if timeline[u] == "0":
                timeline[u] = chord

    def encode_run(sym: str, run_units: int) -> list[str]:
        tokens: list[str] = []
        while run_units > 0:
            if run_units >= 64:
                tokens.append(sym + "---")
                run_units -= 64
            elif run_units >= 48:
                tokens.append(sym + "--")
                run_units -= 48
            elif run_units >= 32:
                tokens.append(sym + "-")
                run_units -= 32
            elif run_units >= 16:
                tokens.append(sym)
                run_units -= 16
            elif run_units >= 8:
                tokens.append(sym + "_")
                run_units -= 8
            elif run_units >= 4:
                tokens.append(sym + "__")
                run_units -= 4
            elif run_units >= 2:
                tokens.append(sym + "___")
                run_units -= 2
            else:
                tokens.append(sym + "____")
                run_units -= 1
        return tokens

    tokens: list[str] = []
    for bar_start in range(0, total_units, units_per_bar):
        tokens.append("|")
        u = bar_start
        bar_end = bar_start + units_per_bar
        while u < bar_end:
            sym = timeline[u]
            run = 1
            while u + run < bar_end and timeline[u + run] == sym:
                run += 1
            tokens.extend(encode_run(sym, run))
            u += run
    tokens.append("|")
    return " ".join(tokens)


def _collect_notes(mid: MidiFile):
    ticks_per_beat = mid.ticks_per_beat
    notes_by_channel: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    active: dict[tuple[int, int], int] = {}
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
                active[(msg.channel, msg.note)] = abs_tick
            elif msg.type == "note_off":
                start = active.pop((msg.channel, msg.note), None)
                if start is not None and abs_tick > start:
                    notes_by_channel[msg.channel].append((start, abs_tick, msg.note))
            elif msg.type == "note_on" and msg.velocity == 0:
                start = active.pop((msg.channel, msg.note), None)
                if start is not None and abs_tick > start:
                    notes_by_channel[msg.channel].append((start, abs_tick, msg.note))

    return notes_by_channel, program_by_channel, tempo, time_sig, key_sig, ticks_per_beat


def _program_to_instrument(program: int, is_drum: bool) -> str:
    if is_drum:
        return "drums"
    # GM program ranges
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
    # Interval partitioning: assign to minimal voices without overlap
    voices: list[list[NoteEvent]] = []
    voice_ends: list[Fraction] = []
    for ev in sorted(events, key=lambda e: (e.start_beat, -e.duration)):
        assigned = False
        for i, end in enumerate(voice_ends):
            if ev.start_beat >= end:
                voices[i].append(ev)
                voice_ends[i] = ev.start_beat + ev.duration
                assigned = True
                break
        if not assigned:
            voices.append([ev])
            voice_ends.append(ev.start_beat + ev.duration)
    return voices


def _drop_empty_bars(bodies: list[list[str]]) -> list[list[str]]:
    # Remove bars where all parts are rests (only 0____ tokens)
    if not bodies:
        return bodies
    # Split into bars per line
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

    # Rebuild bodies
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

    numerator, denominator = time_sig or (4, 4)
    beats_per_bar = Fraction(numerator, 1)
    beat_factor = Fraction(4, denominator)

    bpm = 120
    if tempo:
        bpm = int(round(60_000_000 / tempo))

    instruments = get_all_instruments()

    parts: list[tuple[str, list[NoteEvent]]] = []
    total_beats = Fraction(0)

    for ch, items in sorted(notes_by_channel.items(), key=lambda x: x[0]):
        is_drum = ch == 9
        program = program_by_channel.get(ch, 0)
        inst = _program_to_instrument(program, is_drum)
        if inst not in instruments:
            inst = "grand_piano"
        events: list[NoteEvent] = []
        # Group by start -> duration buckets
        start_map: dict[int, list[tuple[int, int]]] = defaultdict(list)
        for start, end, midi in items:
            start_map[start].append((end, midi))
        for start, entries in start_map.items():
            durations: dict[int, list[int]] = defaultdict(list)
            for end, midi in entries:
                durations[end - start].append(midi)
            for dur_ticks, midis in durations.items():
                start_beat = _quantize((start / ticks_per_beat) * beat_factor)
                dur_beat = _quantize((dur_ticks / ticks_per_beat) * beat_factor)
                if dur_beat <= 0:
                    continue
                events.append(NoteEvent(start_beat=start_beat, duration=dur_beat, midis=midis))
                end_beat = start_beat + dur_beat
                if end_beat > total_beats:
                    total_beats = end_beat
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

    # Drop bars that are all rests across all parts
    if lines:
        bodies = _drop_empty_bars([tokens for _, tokens in lines])
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
