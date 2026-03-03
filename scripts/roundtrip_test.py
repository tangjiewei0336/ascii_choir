#!/usr/bin/env python3
"""Round-trip test: choir -> midi -> choir. Compare with workspaces."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.parser import parse
from src.audio.export_midi import export_score_to_midi
from src.utils.midi_to_choir import midi_to_choir_text


def main():
    workspaces = Path(__file__).parent.parent / "workspaces"
    choir_files = sorted(workspaces.rglob("*.choir"))
    if not choir_files:
        print("No .choir files in workspaces")
        return

    test_dir = Path(__file__).parent.parent / "tmp_roundtrip"
    test_dir.mkdir(exist_ok=True)

    for choir_path in choir_files:
        rel = choir_path.relative_to(workspaces)
        print(f"\n{rel}")

        try:
            text = choir_path.read_text(encoding="utf-8")
            score = parse(text)
        except Exception as e:
            print(f"  Parse error: {e}")
            continue

        mid_path = test_dir / (choir_path.stem + ".mid")
        out_path, err = export_score_to_midi(score, mid_path)
        if err:
            print(f"  Export error: {err}")
            continue
        print(f"  Exported: {out_path}")

        try:
            rebuilt = midi_to_choir_text(Path(out_path))
            rebuilt_path = test_dir / (choir_path.stem + "_rebuilt.choir")
            rebuilt_path.write_text(rebuilt, encoding="utf-8")
            print(f"  Rebuilt: {rebuilt_path}")

            # Quick diff: line count
            orig_lines = len(text.strip().splitlines())
            rebuilt_lines = len(rebuilt.strip().splitlines())
            print(f"  Lines: orig={orig_lines} rebuilt={rebuilt_lines}")
        except Exception as e:
            print(f"  Rebuild error: {e}")
            import traceback
            traceback.print_exc()

    print("\nDone.")


if __name__ == "__main__":
    main()
