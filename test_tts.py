#!/usr/bin/env python3
"""
测试 TTS 模块是否正常工作。
运行: python test_tts.py
"""
import sys
from pathlib import Path

# 添加项目根目录
sys.path.insert(0, str(Path(__file__).resolve().parent))

def main():
    root = Path(__file__).parent
    path = root / "workspaces" / "山手线" / "大崎.choir"

    print("1. 测试大崎.choir 解析与 TTS 提取...")
    if not path.exists():
        print(f"   跳过: 文件不存在 {path}")
    else:
        from src.core.parser import parse
        from src.core.scheduler import schedule_segments
        text = path.read_text(encoding="utf-8")
        score = parse(text)
        segs = schedule_segments(score)
        tts_count = sum(len(s.tts_before) for s in segs)
        print(f"   OK: {len(segs)} 篇章, {tts_count} 个 TTS 事件")
        for i, s in enumerate(segs):
            for t in s.tts_before:
                print(f"      - 篇章 {i} 前: \"{t.text}\" ({t.lang})")

    print("\n2. 检查 edge-tts 和 pydub...")
    try:
        import edge_tts
        import pydub
        print("   OK: 已安装")
    except ImportError as e:
        print(f"   未安装: {e}")
        print("   请运行: pip install edge-tts pydub")
        print("   (pydub 需 ffmpeg 才能读取 mp3)")
        return 0  # 解析部分已通过

    print("\n3. 测试 generate_tts_audio (需要网络)...")
    from src.voice.tts_helper import generate_tts_audio
    result = generate_tts_audio("Hello.", "en-US", 44100)
    if result is None:
        print("   失败: 返回 None（检查网络或 ffmpeg）")
        return 1
    audio, duration = result
    print(f"   OK: {len(audio)} 采样点, {duration:.2f} 秒")

    print("\n全部检查通过。")
    return 0

if __name__ == "__main__":
    sys.exit(main())
