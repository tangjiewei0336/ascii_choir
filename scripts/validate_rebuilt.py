#!/usr/bin/env python3
"""用 validator 检查 rebuilt choir 文件的语法问题"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.validator import validate


def main():
    test_dir = Path(__file__).parent.parent / "tmp_roundtrip"
    if not test_dir.exists():
        print("tmp_roundtrip 不存在，请先运行 roundtrip_test.py")
        return

    choir_files = sorted(test_dir.glob("*_rebuilt.choir"))
    if not choir_files:
        choir_files = sorted(test_dir.glob("*.choir"))
    if not choir_files:
        print("未找到 choir 文件")
        return

    for choir_path in choir_files:
        print(f"\n{'='*60}")
        print(f"文件: {choir_path.name}")
        print("=" * 60)

        text = choir_path.read_text(encoding="utf-8")
        score, diags = validate(text)

        if score is None:
            print("解析失败")
        else:
            print("解析成功")

        errors = [d for d in diags if d.level == "error"]
        warnings = [d for d in diags if d.level == "warning"]

        if errors:
            print(f"\n错误 ({len(errors)} 个):")
            for d in errors:
                ctx = ""
                if d.line and d.column:
                    lines = text.splitlines()
                    if 0 <= d.line - 1 < len(lines):
                        ln = lines[d.line - 1]
                        ctx = f"  -> {repr(ln[:80])}..."
                print(f"  第{d.line}行第{d.column}列: {d.message}{ctx}")

        if warnings:
            print(f"\n警告 ({len(warnings)} 个):")
            for d in warnings[:10]:
                print(f"  第{d.line}行第{d.column}列: {d.message}")
            if len(warnings) > 10:
                print(f"  ... 还有 {len(warnings) - 10} 个警告")

        if not errors and not warnings:
            print("无错误和警告")


if __name__ == "__main__":
    main()
