"""
编译预处理：展开 \\import{文件名} 为对应文件内容。
"""
import re
from pathlib import Path


def expand_imports(content: str, base_dir: Path | None) -> str:
    """
    将 content 中的 \\import{文件名} 替换为对应文件内容。
    支持递归导入；检测循环导入并抛出异常。
    base_dir: 解析相对路径的基准目录（通常为工作区根或当前文件所在目录）。
    """
    if base_dir is None or not base_dir.is_dir():
        return content

    def _expand(text: str, visited: set[str]) -> str:
        pattern = re.compile(r"\\import\{([^{}]+)\}")
        result = []
        last_end = 0
        for m in pattern.finditer(text):
            result.append(text[last_end : m.start()])
            filename = m.group(1).strip()
            if not filename:
                result.append(m.group(0))
                last_end = m.end()
                continue
            resolved = (base_dir / filename).resolve()
            try:
                canon = str(resolved.resolve())
            except OSError:
                canon = str(resolved)
            if canon in visited:
                raise ValueError(f"循环导入: {filename}")
            visited.add(canon)
            try:
                sub_content = resolved.read_text(encoding="utf-8")
            except FileNotFoundError:
                raise FileNotFoundError(f"导入文件不存在: {filename}")
            except OSError as e:
                raise OSError(f"无法读取导入文件 {filename}: {e}") from e
            expanded = _expand(sub_content, visited)
            visited.discard(canon)
            result.append(expanded)
            last_end = m.end()
        result.append(text[last_end:])
        return "".join(result)

    return _expand(content, set())
