#!/usr/bin/env python3
"""
从 speaker_info_bundled.json 中删除所有 voice_samples 字段，减小文件体积。
"""
import json
from pathlib import Path


def remove_voice_samples(obj):
    """递归删除 dict 或 list 中所有的 voice_samples 键"""
    if isinstance(obj, dict):
        obj.pop("voice_samples", None)
        for v in obj.values():
            remove_voice_samples(v)
    elif isinstance(obj, list):
        for item in obj:
            remove_voice_samples(item)


def main():
    root = Path(__file__).resolve().parent.parent
    path = root / "src" / "voice" / "speaker_info_bundled.json"
    if not path.exists():
        print(f"文件不存在: {path}")
        return 1

    print(f"读取 {path}...")
    data = json.loads(path.read_text(encoding="utf-8"))

    print("删除 voice_samples...")
    remove_voice_samples(data)

    print(f"写入 {path}...")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("完成")
    return 0


if __name__ == "__main__":
    exit(main())
