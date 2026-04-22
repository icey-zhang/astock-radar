"""
把 Markdown 报告里的 AI 分析区块同步到 HTML dashboard 的 window.AI_ANALYSIS 全局。

用法:
    python scripts/sync_ai_to_html.py \
        --md reports/2026-04-22.md \
        --html reports/2026-04-22_dashboard.html

工作原理:
    - HTML 模板里有一段占位:
        window.AI_ANALYSIS = /* AI_ANALYSIS_START */{}/* AI_ANALYSIS_END */;
    - 本脚本解析 MD,提取每只股票 AI 分析区块,转 HTML
    - 把 {} 替换成 {"600519": "<p>...</p>", "002594": "<p>...</p>"}
    - 刷新浏览器即可看到 AI 分析显示

可重入:再跑一次得到同样结果(每次都替换 START...END 之间的整个对象)
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path


# ============================== MD 解析 ==============================
STOCK_HEADER_RE = re.compile(r"^##\s+(\d{6})\s*·\s*(.+?)\s*$", re.MULTILINE)


def split_md_by_stock(md_content: str) -> dict[str, str]:
    """按每只股票切段。返回 {code: stock_section_text}。"""
    sections: dict[str, str] = {}
    matches = list(STOCK_HEADER_RE.finditer(md_content))
    for i, m in enumerate(matches):
        code = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_content)
        sections[code] = md_content[start:end]
    return sections


def extract_ai_block(stock_section: str) -> str | None:
    """
    从单只股票 MD 段提取 AI 分析正文。
    返回:
        None - 仍是占位符(未填充)
        ""   - 找不到 AI 区块
        str  - AI 分析的 markdown 原文
    """
    header_re = re.compile(r"###\s*🤖\s*AI\s*综合分析.*?$", re.MULTILINE)
    h = header_re.search(stock_section)
    if not h:
        return ""

    body_start = h.end()
    terminator = re.search(r"^\s*(?:---|##\s)", stock_section[body_start:], re.MULTILINE)
    body_end = body_start + terminator.start() if terminator else len(stock_section)
    body = stock_section[body_start:body_end].strip()

    # 检查是否仍是占位符
    if "CLAUDE_FILL_START" in body or "CLAUDE_FILL_END" in body:
        return None
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL).strip()
    if not body:
        return None

    return body


# ============================== 轻量 Markdown → HTML ==============================
def md_to_html(md_text: str) -> str:
    """简化 markdown 渲染:段落、列表、粗体、斜体、行内代码、引用、链接。"""
    lines = md_text.split("\n")
    out: list[str] = []
    in_list = False
    in_blockquote = False
    current_para: list[str] = []

    def flush_para():
        nonlocal current_para
        if current_para:
            text = " ".join(current_para).strip()
            if text:
                out.append(f"<p>{_inline(text)}</p>")
            current_para = []

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def close_bq():
        nonlocal in_blockquote
        if in_blockquote:
            out.append("</blockquote>")
            in_blockquote = False

    for raw in lines:
        line = raw.rstrip()

        if not line.strip():
            flush_para()
            close_list()
            close_bq()
            continue

        m = re.match(r"^\s*[-*]\s+(.+)$", line)
        if m:
            flush_para()
            close_bq()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        m = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if m:
            flush_para()
            close_bq()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        m = re.match(r"^>\s?(.*)$", line)
        if m:
            flush_para()
            close_list()
            if not in_blockquote:
                out.append("<blockquote>")
                in_blockquote = True
            out.append(f"<p>{_inline(m.group(1))}</p>")
            continue

        m = re.match(r"^#{3,6}\s+(.+)$", line)
        if m:
            flush_para()
            close_list()
            close_bq()
            out.append(f"<p><strong>{_inline(m.group(1))}</strong></p>")
            continue

        close_list()
        close_bq()
        current_para.append(line.strip())

    flush_para()
    close_list()
    close_bq()
    return "\n".join(out)


def _inline(text: str) -> str:
    placeholders: list[tuple[str, str]] = []

    def _protect(match):
        ph = f"\x00P{len(placeholders)}\x00"
        placeholders.append((ph, f"<code>{html.escape(match.group(1))}</code>"))
        return ph

    text = re.sub(r"`([^`]+)`", _protect, text)
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2))}" target="_blank" rel="noopener">{m.group(1)}</a>',
        text,
    )

    for ph, repl in placeholders:
        text = text.replace(ph, repl)
    return text


# ============================== HTML 注入 ==============================
AI_ANALYSIS_RE = re.compile(
    r"(/\*\s*AI_ANALYSIS_START\s*\*/)(.*?)(/\*\s*AI_ANALYSIS_END\s*\*/)",
    re.DOTALL,
)


def inject_ai_analysis(html_content: str, ai_map: dict[str, str]) -> tuple[str, bool]:
    """把 ai_map 注入到 window.AI_ANALYSIS 位置。返回 (new_html, success)。"""
    m = AI_ANALYSIS_RE.search(html_content)
    if not m:
        return html_content, False

    # 用 json.dumps 确保字符串里的引号、换行被正确转义成 JS 合法语法
    js_obj = json.dumps(ai_map, ensure_ascii=False)
    replacement = f"{m.group(1)}{js_obj}{m.group(3)}"
    return html_content[:m.start()] + replacement + html_content[m.end():], True


# ============================== 主流程 ==============================
def main():
    ap = argparse.ArgumentParser(description="把 MD 里的 AI 分析同步到 HTML dashboard")
    ap.add_argument("--md", required=True)
    ap.add_argument("--html", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    md_path = Path(args.md)
    html_path = Path(args.html)

    if not md_path.exists():
        print(f"ERROR: MD 不存在: {md_path}", file=sys.stderr)
        sys.exit(1)
    if not html_path.exists():
        print(f"ERROR: HTML 不存在: {html_path}", file=sys.stderr)
        sys.exit(1)

    md_text = md_path.read_text(encoding="utf-8")
    html_text = html_path.read_text(encoding="utf-8")

    stock_sections = split_md_by_stock(md_text)
    if not stock_sections:
        print("WARN: MD 里没解析到股票 section", file=sys.stderr)
        sys.exit(0)

    ai_map: dict[str, str] = {}
    stats = {"filled": 0, "placeholder": 0, "missing": 0}

    for code, section in stock_sections.items():
        ai_md = extract_ai_block(section)
        if ai_md is None:
            stats["placeholder"] += 1
            continue
        if not ai_md:
            stats["missing"] += 1
            continue

        ai_map[code] = md_to_html(ai_md)
        stats["filled"] += 1
        if args.dry_run:
            print(f"  [dry] {code}: {len(ai_md)} 字符 → {len(ai_map[code])} HTML")

    print(f"\n[summary] 已准备 {stats['filled']} 只 · "
          f"占位跳过 {stats['placeholder']} · AI 块缺失 {stats['missing']}")

    if not ai_map:
        print("[done] 无可同步内容,HTML 未修改")
        return

    if args.dry_run:
        print("[dry-run] 不写入 HTML")
        return

    new_html, ok = inject_ai_analysis(html_text, ai_map)
    if not ok:
        print("ERROR: HTML 里没找到 /* AI_ANALYSIS_START */ 标记。"
              "是否用旧版 render_dashboard.py 生成的 HTML?", file=sys.stderr)
        sys.exit(1)

    html_path.write_text(new_html, encoding="utf-8")
    print(f"[done] HTML 已更新 -> {html_path}")


if __name__ == "__main__":
    main()
