#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_prose.py — 与你同学写作法 · AI 味检查脚本

基于 KKKKhazix/human-writing 的 check_prose.py 魔改（MIT，2026-10-13），
替换硬禁词表为默认清单，冒号/双引号/破折号不禁，排比降为警告。

用法：
    python3 check_prose.py <稿件路径.md>

退出码：0 = 通过（硬违例清零）；1 = 有硬违例，改完再跑。
警告项不判失败，人工判断。

与 human-writing 原版的有意差异：
- 冒号、双引号、破折号不禁（中文写作正常使用它们）
- 「说到底」不禁（它是「本质上」的自然替代词）
- 排比、省字翻案变形降为警告级
"""

import re
import sys
import math

# ---------- 硬禁词（默认 AI 腔清单，命中即 FAIL，可按自己的禁区修改） ----------
HARD_WORDS = (
    "显而易见", "值得注意的是", "不难发现", "令人深思", "毋庸置疑",
    "综上所述", "总而言之", "这意味着", "本质上", "换句话说",
    "赋能", "抓手", "打造闭环",
)

# ---------- 翻案腔经典式（动作级禁令的经典外衣，FAIL） ----------
HARD_FANAN = (
    (r"不是[^，。！？\n]{1,25}[，,][^，。！？\n]{0,12}而是", "不是A，而是B"),
    (r"并非[^，。！？\n]{1,25}[，,][^，。！？\n]{0,12}而是", "并非A，而是B"),
    (r"不在于[^，。！？\n]{1,25}[，,][^，。！？\n]{0,12}而在于", "不在于A，而在于B"),
    (r"与其说[^，。！？\n]{1,30}[，,][^，。！？\n]{0,12}(?:不如说|倒不如|毋宁)", "与其说A，不如说B"),
    (r"你以为[^，。！？\n]{1,30}[，,][^。！？\n]{0,8}其实", "你以为A，其实B"),
    (r"看似[^，。！？\n]{1,25}[，,][^。！？\n]{0,8}(?:实则|其实)", "看似A，实则B"),
    (r"表面(?:上|上看)?[^，。！？\n]{1,25}[，,][^。！？\n]{0,8}(?:实际上|其实|实则)", "表面上A，其实B"),
    (r"答案(?:恰恰)?相反", "答案恰恰相反"),
    (r"回头才发现", "回头才发现"),
    (r"[^，。！？\n]{1,20}不重要[，,][^，。！？\n]{0,10}重要的是", "A不重要，重要的是B"),
    (r"不是[^。\n]{1,25}[。！？]\s*而是", "跨句变形：不是A。而是B"),
)

# ---------- 翻案腔省字/变形（姿势可疑，人工判，WARN） ----------
WARN_FANAN = (
    (r"不是[^，。！？\n]{1,20}[，,]是[^的]", "省字变形：不是A，是B（有真实让步材料则豁免）"),
    (r"(?:一直|原先|本来|当初)?以为[^。！？\n]{1,30}(?:后来才|这才)(?:发现|明白|懂)", "以为体变形"),
)

# ---------- 名词化（WARN） ----------
NOMINALIZATION = (r"(?:进行|实现|开展)了|完成了对", "动词名词化（还原成直接动作）")

CONJUNCTIONS = ("因为", "所以", "但是", "同时", "此外", "然而")

SENTENCE_CV_FLOOR = 0.45   # 句长变异系数下限（低于=句长太齐）
CONJ_PER_SENT_CEIL = 0.40  # 每句连词数上限
MIN_SENTENCES_FOR_STATS = 10


def mask_non_prose(text: str) -> str:
    """剔除非正文部分：代码块、行内代码、URL、frontmatter、标题/引用/表格/图位行。"""
    text = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`\n]+`", "", text)
    text = re.sub(r"https?://\S+", "", text)
    kept = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            kept.append("")
            continue
        if s.startswith(("#", ">", "|", "---", "【图")) or re.match(r"^[-*+]\s", s) or re.match(r"^\d+[.、]\s*", s):
            kept.append("")
            continue
        kept.append(line)
    return "\n".join(kept)


def line_number(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def excerpt(text: str, start: int, width: int = 40) -> str:
    seg = text[start:start + width].replace("\n", " ")
    return seg[:width] + ("…" if len(seg) >= width else "")


def sentences_of(text: str):
    parts = re.split(r"[。！？!?]", text)
    return [p.strip() for p in parts if len(p.strip()) >= 4]


def sentence_length_cv(sents):
    if len(sents) < MIN_SENTENCES_FOR_STATS:
        return None
    lens = [len(s) for s in sents]
    mean = sum(lens) / len(lens)
    if mean == 0:
        return None
    var = sum((x - mean) ** 2 for x in lens) / len(lens)
    return math.sqrt(var) / mean


def paragraphs_of(text: str):
    paras = []
    for block in text.split("\n"):
        s = re.sub(r"^\*\*([^*]+)\*\*", r"\1", block.strip())  # 剥段首加粗，防排比误报
        if len(s) >= 4:
            paras.append(s)
    return paras


def main() -> int:
    if len(sys.argv) != 2:
        print("用法: python3 check_prose.py <稿件路径>")
        return 2
    try:
        raw = open(sys.argv[1], encoding="utf-8").read()
    except OSError as e:
        print(f"读不了文件: {e}")
        return 2

    prose = mask_non_prose(raw)
    han = len(re.findall(r"[\u4e00-\u9fff]", prose))
    sents = sentences_of(prose)
    paras = paragraphs_of(prose)

    hard, warns = [], []

    # 硬禁词
    for w in HARD_WORDS:
        for m in re.finditer(re.escape(w), prose):
            hard.append((line_number(raw, m.start()), f"禁用词「{w}」", excerpt(prose, m.start())))

    # 首先…其次…（组合）
    if "首先" in prose and re.search(r"其次", prose):
        m = re.search(r"首先", prose)
        hard.append((line_number(raw, m.start()), "首先…其次…最后 套路", excerpt(prose, m.start(), 60)))

    # 翻案腔
    for pat, label in HARD_FANAN:
        for m in re.finditer(pat, prose):
            hard.append((line_number(raw, m.start()), f"翻案腔「{label}」", excerpt(prose, m.start())))
    for pat, label in WARN_FANAN:
        for m in re.finditer(pat, prose):
            warns.append((line_number(raw, m.start()), f"翻案腔变形（{label}）", excerpt(prose, m.start())))

    # 名词化
    for m in re.finditer(NOMINALIZATION[0], prose):
        warns.append((line_number(raw, m.start()), NOMINALIZATION[1], excerpt(prose, m.start())))

    # 排比：连续 3+ 段落同开头两字
    run_start, run_len, prev_head = 0, 0, None
    for i, p in enumerate(paras):
        head = p[:2]
        if head == prev_head:
            run_len += 1
        else:
            if run_len >= 3:
                warns.append((0, f"连续 {run_len} 段同开头「{prev_head}…」同构排比", paras[run_start][:40] + "…"))
            run_start, run_len, prev_head = i, 1, head
    if run_len >= 3:
        warns.append((0, f"连续 {run_len} 段同开头「{prev_head}…」同构排比", paras[run_start][:40] + "…"))

    # 单句成段连排（>=4 段每段一句且都短）——节奏鼓点警告
    short_streak, streak_start = 0, 0
    for i, p in enumerate(paras):
        n_sent = len([s for s in re.split(r"[。！？!?]", p) if len(s.strip()) >= 4])
        if n_sent <= 1 and len(p) <= 25:
            if short_streak == 0:
                streak_start = i
            short_streak += 1
        else:
            if short_streak >= 4:
                warns.append((0, f"连续 {short_streak} 个短单句段（敲鼓点节奏）", paras[streak_start][:40] + "…"))
            short_streak = 0

    # 统计层
    cv = sentence_length_cv(sents)
    if cv is not None and cv < SENTENCE_CV_FLOOR:
        warns.append((0, f"句长变异系数 {cv:.2f} < {SENTENCE_CV_FLOOR}（句长太齐，压一句到 10 字内或放开一句过 30 字）", ""))
    if len(sents) >= MIN_SENTENCES_FOR_STATS:
        conj = sum(prose.count(c) for c in CONJUNCTIONS)
        density = conj / len(sents)
        if density > CONJ_PER_SENT_CEIL:
            warns.append((0, f"连词密度 {density:.2f}/句 > {CONJ_PER_SENT_CEIL}（删一半连词，中文小句靠语序自己接）", ""))

    # 报告
    print("=" * 56)
    print("与你同学写作法 · AI 味检查 v2")
    print("=" * 56)
    print(f"稿件：{sys.argv[1]}（正文约 {han} 字，{len(sents)} 句）\n")

    if hard:
        print(f"■ 硬违例 {len(hard)} 处 —— 必须清零")
        for ln, label, ex in hard:
            print(f"  L{ln:<4}{label}")
            if ex:
                print(f"        {ex}")
    else:
        print("■ 硬违例 0 处")

    print()
    if warns:
        print(f"▲ 警告 {len(warns)} 处 —— 人工判断，不强制")
        for ln, label, ex in warns:
            loc = f"L{ln}" if ln else "  "
            print(f"  {loc:<4}{label}")
            if ex:
                print(f"        {ex}")
    else:
        print("▲ 警告 0 处")

    print()
    if hard:
        print("结果：FAIL —— 硬违例未清零，改完重跑")
        return 1
    print("结果：PASS —— 硬违例清零（警告项自行斟酌）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
