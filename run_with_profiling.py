#!/usr/bin/env python3
"""
带性能分析的启动脚本。运行后拖动/滚动编辑器，然后关闭窗口。
分析结果会保存到 profile_editor.prof，可用以下命令查看：

  python -m pstats profile_editor.prof
  # 或使用 snakeviz 可视化（需先 pip install snakeviz）：
  python -m snakeviz profile_editor.prof
"""
import cProfile
import pstats
import sys

if __name__ == "__main__":
    prof = cProfile.Profile()
    prof.enable()
    try:
        from src.ui.gui import main
        main()
    finally:
        prof.disable()
        prof.dump_stats("profile_editor.prof")
        print("分析结果已保存到 profile_editor.prof", file=sys.stderr)
        # 打印最耗时的 30 个函数
        pstats.Stats(prof).strip_dirs().sort_stats("cumulative").print_stats(30)
