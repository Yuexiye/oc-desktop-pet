"""scripts/audit_silent_exceptions.py — 静默异常审计

扫描所有 `except Exception` 后跟 `pass` 或空行的位置，
记录到审计报告，便于后续改为可观测降级。

用法:
    python scripts/audit_silent_exceptions.py
    python scripts/audit_silent_exceptions.py --output docs/SILENT-EXCEPTIONS.md
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Optional


def find_silent_exceptions(file_path: Path) -> list[dict]:
    """扫描单个文件，找出静默异常位置"""
    try:
        source = file_path.read_text(encoding='utf-8')
    except Exception:
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [{"file": str(file_path), "line": 0, "error": f"SyntaxError: {e}"}]

    results = []
    lines = source.splitlines()

    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                # 检查 except Exception（不指定具体异常类型）
                if handler.type is None or (
                    isinstance(handler.type, ast.Name) and handler.type.id == "Exception"
                ):
                    # 检查 handler 体是否只有 pass 或为空
                    if handler.body:
                        first_stmt = handler.body[0]
                        # 只检查第一个语句（简化）
                        if isinstance(first_stmt, ast.Pass):
                            results.append({
                                "file": str(file_path),
                                "line": first_stmt.lineno,
                                "type": "pass",
                                "code": lines[first_stmt.lineno - 1].strip() if first_stmt.lineno <= len(lines) else "",
                            })
                        elif isinstance(first_stmt, ast.Expr) and isinstance(first_stmt.value, ast.Constant):
                            # 空字符串或 None
                            if first_stmt.value.value in (None, ""):
                                results.append({
                                    "file": str(file_path),
                                    "line": first_stmt.lineno,
                                    "type": "empty",
                                    "code": lines[first_stmt.lineno - 1].strip() if first_stmt.lineno <= len(lines) else "",
                                })

    return results


def scan_project(root: Path) -> list[dict]:
    """扫描整个项目"""
    results = []
    py_files = []

    for dirpath, dirnames, filenames in os.walk(root):
        # 跳过目录
        dirnames[:] = [d for d in dirnames if d not in ('temp_amadeus', 'temp_alife', '__pycache__', 'third_party_reference', '.git', '.venv', 'node_modules')]
        for f in filenames:
            if f.endswith('.py') and not f.startswith('test_'):
                py_files.append(Path(dirpath) / f)

    print(f"扫描 {len(py_files)} 个 Python 文件...")
    for f in py_files:
        try:
            results.extend(find_silent_exceptions(f))
        except Exception as e:
            print(f"  错误扫描 {f}: {e}")

    return results


def generate_report(results: list[dict], output_path: Path, root: Path) -> None:
    """生成 Markdown 审计报告"""
    # 按文件分组
    by_file = {}
    for r in results:
        fname = r["file"]
        if fname not in by_file:
            by_file[fname] = []
        by_file[fname].append(r)

    lines = [
        "# 静默异常审计报告",
        "",
        f"> 审计日期：{__import__('datetime').datetime.now().strftime('%Y-%m-%d')}",
        f"> 总计：{len(results)} 处静默异常",
        "",
        "---",
        "",
    ]

    for fname, items in sorted(by_file.items()):
        rel = os.path.relpath(fname, root)
        lines.append(f"## {rel} ({len(items)} 处)")
        lines.append("")
        lines.append("| 行号 | 类型 | 代码 |")
        lines.append("|---|---|---|")
        for item in items:
            code = item.get("code", "").replace("|", "\\|")
            lines.append(f"| {item['line']} | {item['type']} | `{code}` |")
        lines.append("")

    output_path.write_text("\n".join(lines), encoding='utf-8')
    print(f"\n报告已写入: {output_path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="静默异常审计")
    parser.add_argument("--output", type=str, default="docs/SILENT-EXCEPTIONS.md", help="报告输出路径")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    results = scan_project(root)

    print(f"\n发现 {len(results)} 处静默异常")

    if results:
        output_path = root / args.output
        os.makedirs(output_path.parent, exist_ok=True)
        generate_report(results, output_path, root)
    else:
        print("未发现静默异常")

    sys.exit(0 if not results else 1)


if __name__ == "__main__":
    main()
