#!/usr/bin/env python3
"""
AP PDF 题目切分 + OCR 识别工具
- 将 PDF 每页切成单独图片
- 生成总目录 index.md
- 调用大模型识别题目文字，保存到 txt
- [P0] 题目边界检测：按题裁剪图片（--parse-questions）
- [P0] 解析 PDF 关联：将解析图片与题目绑定（--answer-pdf）
- [P0] 结构化解析：输出 questions.json（章节/知识点/难度/答案）

多老师支持：
  --teacher-id      教师 ID（chenxi / jiangzhi），写入每条题目
  --subject         科目名称（AP统计 / AP化学），影响解析 prompt
  --source-file     来源文件名（写入题目元数据，默认取 --pdf 文件名）
  --max-questions N 最多提取 N 道题后停止（0=不限）
  --no-chapter-map  禁用章节页码映射（无章节结构的单次考试 PDF 适用）
  --global-json     全局 questions.json 路径，解析后自动合并
"""

import os
import json
import base64
import argparse
from pathlib import Path
from openai import OpenAI

import fitz  # PyMuPDF

from config import UNIAPI_KEY, UNIAPI_BASE, API_MODEL

DEFAULT_MODEL = API_MODEL


def get_client(api_key: str = UNIAPI_KEY, base_url: str = UNIAPI_BASE) -> OpenAI:
    return OpenAI(api_key=api_key, base_url=base_url)


# ── Phase 1: PDF → 图片 ───────────────────────────────────────

def pdf_to_images(pdf_path: str, output_dir: str, dpi: int = 150) -> list[dict]:
    """将 PDF 每页转为图片，返回页面信息列表"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    pages_dir = os.path.join(output_dir, "pages")
    Path(pages_dir).mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    total = doc.page_count
    pages = []

    for i, page in enumerate(doc):
        page_num = i + 1
        img_path = os.path.join(output_dir, f"page_{page_num:04d}.png")
        pages_path = os.path.join(pages_dir, f"page_{page_num:04d}.png")

        if not os.path.exists(img_path):
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat)
            pix.save(img_path)
            # 同时保存到 pages 目录
            pix.save(pages_path)

        pages.append({
            "page": page_num,
            "image": img_path,
            "chapter": "",
            "ocr_txt": "",
        })
        print(f"  [{page_num}/{total}] {img_path}")

    doc.close()
    return pages


def build_index(pages: list[dict], index_path: str):
    """生成总目录 index.md，保留文件中 '## 页面索引' 之前的自定义头部"""
    base = os.path.dirname(index_path)

    header = ""
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as f:
            content = f.read()
        marker = "## 页面索引"
        if marker in content:
            header = content[:content.index(marker)]

    table_lines = [
        "## 页面索引\n\n",
        "| 编号 | 章节 | 图片 | OCR 文本 |\n",
        "|:----:|------|------|----------|\n",
    ]
    for p in pages:
        img_rel  = os.path.relpath(p["image"], base)
        txt_cell = f"[txt]({os.path.relpath(p['ocr_txt'], base)})" if p["ocr_txt"] else "-"
        chapter  = p["chapter"] or "-"
        table_lines.append(f"| {p['page']:04d} | {chapter} | [图片]({img_rel}) | {txt_cell} |\n")

    with open(index_path, "w", encoding="utf-8") as f:
        if header:
            f.write(header)
        else:
            f.write("# AP 题目总目录\n\n")
        f.writelines(table_lines)
    print(f"总目录已生成: {index_path}")


def ocr_image(client: OpenAI, img_path: str, model: str) -> str:
    """调用视觉模型识别图片中的题目文字"""
    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请识别图片中的所有文字内容，保持原有格式和结构。"
                            "数学公式用 LaTeX 格式（行内用 $...$，独立公式用 $$...$$）。"
                            "只输出识别的文字，不要添加任何解释或说明。"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{img_b64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        max_tokens=4096,
    )
    return response.choices[0].message.content


def run_ocr(pages: list[dict], txt_dir: str, model: str, client: OpenAI):
    """批量 OCR，已存在的自动跳过"""
    Path(txt_dir).mkdir(parents=True, exist_ok=True)
    total = len(pages)

    for idx, p in enumerate(pages, 1):
        page_num = p["page"]
        txt_path = os.path.join(txt_dir, f"page_{page_num:04d}.md")

        if os.path.exists(txt_path):
            print(f"  [{idx}/{total}] 跳过（已存在）: page_{page_num:04d}.md")
            p["ocr_txt"] = txt_path
            continue

        print(f"  [{idx}/{total}] OCR 第 {page_num} 页...", end=" ", flush=True)
        try:
            text = ocr_image(client, p["image"], model=model)
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text)
            p["ocr_txt"] = txt_path
            print("✓")
        except Exception as e:
            print(f"✗ 失败: {e}")


def parse_page_range(spec: str, total: int) -> tuple[int, int]:
    if "-" in spec:
        start, end = map(int, spec.split("-", 1))
    else:
        start = end = int(spec)
    return max(1, start), min(total, end)


# ── Phase 2 (P0): 题目边界检测 & 裁剪 & 结构化解析 ────────────

def _encode_image(img_path: str) -> str:
    with open(img_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _parse_json_response(content: str) -> dict | list:
    """从模型响应中提取 JSON（兼容带 markdown 代码块的格式）"""
    content = content.strip()
    if content.startswith("```"):
        parts = content.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            try:
                return json.loads(part)
            except json.JSONDecodeError:
                continue
    return json.loads(content)


def detect_question_boundaries(client: OpenAI, img_path: str, model: str, is_answer_page: bool = False) -> list[dict]:
    """
    3步 CoT 检测页面中各题目的垂直边界位置。

    Step 1 — 页面排版判断：识别列数、题号格式、选项格式、是否有图表
    Step 2 — 题目整体分布：列出每道题的题号和大致位置区间
    Step 3 — 细粒度边界：输出精确的 top_ratio / bottom_ratio

    返回列表，每项格式：
        {
            "question_number":         <int>,
            "top_ratio":               <float 0.0-1.0>,
            "bottom_ratio":            <float 0.0-1.0>,
            "continues_from_previous": <bool>,
            "continues_to_next":       <bool>,
        }
    若页面无题目（封面/目录页/纯答案页），返回空列表。
    """
    img_b64 = _encode_image(img_path)
    img_content = {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{img_b64}", "detail": "high"},
    }

    # ── Step 1: 页面排版判断 ──────────────────────────────────
    if is_answer_page:
        step1_prompt = (
            "请仔细观察这张解析页面，描述其排版特征：\n"
            "1. 页面是单栏还是双栏？\n"
            "2. 解析的题号格式是什么？（如'习题1'、'1.'、'Q1'、纯数字等）\n"
            "3. 每条解析的起始标志是什么？（标题行、分隔线、缩进等）\n"
            "4. 页面上大约有几条解析？\n"
            "5. 是否有图表、化学结构式或公式？\n"
            "请直接描述，不要输出 JSON。"
        )
    else:
        step1_prompt = (
            "请仔细观察这张 AP 题目页面，描述其排版特征：\n"
            "1. 页面是单栏还是双栏？\n"
            "2. 题号格式是什么？（如'习题1'、'1.'、'Question 1'、纯数字等）\n"
            "3. 选项格式是什么？（如'(A)(B)(C)(D)'、'A. B. C. D.'、字母加括号等）\n"
            "4. 选项有几个？（4个还是5个）\n"
            "5. 页面上大约有几道题？\n"
            "6. 是否有图表、图片、化学结构式或公式嵌入题目中？\n"
            "请直接描述，不要输出 JSON。"
        )

    try:
        r1 = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": step1_prompt}, img_content,
            ]}],
            max_tokens=512,
        )
        layout_desc = r1.choices[0].message.content.strip()
        print(f"    [排版] {layout_desc[:80].replace(chr(10), ' ')}...")
    except Exception as e:
        print(f"✗ Step1 排版判断失败: {e}")
        layout_desc = ""

    # ── Step 2: 题目整体分布 ──────────────────────────────────
    if is_answer_page:
        step2_prompt = (
            f"根据以下排版描述：\n{layout_desc}\n\n"
            "请列出这张解析页面上每条解析的题号和大致垂直位置（用页面高度百分比表示）。\n"
            "格式：题号X 约在 Y%-Z% 位置\n"
            "注意：\n"
            "- 只列出能看到完整起始标志的解析\n"
            "- 如果某条解析从上一页延续过来（页面顶部没有题号标志），标注'续上页'\n"
            "- 如果某条解析延续到下一页（页面底部没有结束），标注'续下页'\n"
            "请直接列出，不要输出 JSON。"
        )
    else:
        step2_prompt = (
            f"根据以下排版描述：\n{layout_desc}\n\n"
            "请列出这张题目页面上每道题的题号和大致垂直位置（用页面高度百分比表示）。\n"
            "格式：题号X 约在 Y%-Z% 位置\n"
            "注意：\n"
            "- 只列出能看到完整题干+选项的题目\n"
            "- 如果某道题从上一页延续过来（页面顶部只有选项没有题干），标注'续上页'\n"
            "- 如果某道题延续到下一页（页面底部题目不完整），标注'续下页'\n"
            "- 如果页面是封面/目录/空白页，直接说'无题目'\n"
            "请直接列出，不要输出 JSON。"
        )

    try:
        r2 = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": step1_prompt}, img_content,
                ]},
                {"role": "assistant", "content": layout_desc},
                {"role": "user", "content": step2_prompt},
            ],
            max_tokens=1024,
        )
        distribution_desc = r2.choices[0].message.content.strip()
        print(f"    [分布] {distribution_desc[:80].replace(chr(10), ' ')}...")
    except Exception as e:
        print(f"✗ Step2 分布分析失败: {e}")
        distribution_desc = ""

    # ── Step 3: 细粒度边界输出 ────────────────────────────────
    if is_answer_page:
        step3_prompt = (
            "根据上面的分析，现在输出精确的解析边界。\n\n"
            "规则：\n"
            "- top_ratio：解析起始标题行的顶部（宁可多包含一点上方空白）\n"
            "- bottom_ratio：解析最后一行文字的底部（宁可多包含一点下方空白）\n"
            "- continues_from_previous：该解析是否从上一页延续（页面顶部无题号标志）\n"
            "- continues_to_next：该解析是否延续到下一页（页面底部解析不完整）\n\n"
            "返回严格 JSON，不要有任何其他文字：\n"
            '{"questions": [{"question_number": <int>, "top_ratio": <0.0-1.0>, '
            '"bottom_ratio": <0.0-1.0>, "continues_from_previous": <bool>, "continues_to_next": <bool>}]}\n'
            "无解析内容返回 {\"questions\": []}"
        )
    else:
        step3_prompt = (
            "根据上面的分析，现在输出精确的题目边界。\n\n"
            "规则：\n"
            "- top_ratio：题号行的顶部（必须包含题号，宁可多包含一点上方空白）\n"
            "- bottom_ratio：最后一个选项的底部（必须包含所有选项，宁可多包含一点下方空白）\n"
            "- continues_from_previous：该题是否从上一页延续（页面顶部只有选项没有题干）\n"
            "- continues_to_next：该题是否延续到下一页（页面底部选项不完整）\n\n"
            "返回严格 JSON，不要有任何其他文字：\n"
            '{"questions": [{"question_number": <int>, "top_ratio": <0.0-1.0>, '
            '"bottom_ratio": <0.0-1.0>, "continues_from_previous": <bool>, "continues_to_next": <bool>}]}\n'
            "封面/目录/无题目页返回 {\"questions\": []}"
        )

    try:
        r3 = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": [
                    {"type": "text", "text": step1_prompt}, img_content,
                ]},
                {"role": "assistant", "content": layout_desc},
                {"role": "user", "content": step2_prompt},
                {"role": "assistant", "content": distribution_desc},
                {"role": "user", "content": step3_prompt},
            ],
            max_tokens=2048,
        )
        result = _parse_json_response(r3.choices[0].message.content)
        return result.get("questions", [])
    except Exception as e:
        print(f"✗ Step3 边界输出失败: {e}")
        return []


def crop_image_region(img_path: str, top_ratio: float, bottom_ratio: float, out_path: str) -> bool:
    """裁剪图片的指定垂直区域（依赖 Pillow）"""
    try:
        from PIL import Image
        img    = Image.open(img_path)
        width, height = img.size
        top    = int(max(0.0, top_ratio)  * height)
        bottom = int(min(1.0, bottom_ratio) * height)
        if bottom <= top:
            bottom = top + 1
        cropped = img.crop((0, top, width, bottom))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        cropped.save(out_path)
        return True
    except Exception as e:
        print(f"✗ 裁剪失败 {out_path}: {e}")
        return False


def concat_images_vertical(img_paths: list[str], out_path: str) -> bool:
    """垂直拼接多张图片（用于跨页题目，依赖 Pillow）"""
    try:
        from PIL import Image
        images       = [Image.open(p) for p in img_paths]
        total_height = sum(img.height for img in images)
        max_width    = max(img.width  for img in images)
        combined     = Image.new("RGB", (max_width, total_height), color=(255, 255, 255))
        y_offset = 0
        for img in images:
            combined.paste(img, (0, y_offset))
            y_offset += img.height
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        combined.save(out_path)
        return True
    except Exception as e:
        print(f"✗ 图片拼接失败 {out_path}: {e}")
        return False


def parse_question_metadata(
    client: OpenAI,
    q_img_path: str,
    a_img_path: str | None,
    model: str,
    subject: str = "AP统计",
) -> dict:
    """
    3步 CoT 结构化解析单道题目：
        Step 1 — OCR：忠实提取图片中所有文字（题干、选项、图表描述）
        Step 2 — 推理：逐步分析，得出正确答案和解析
        Step 3 — 结构化：输出 JSON

    返回：chapter / topic_tags / difficulty / question_type /
          correct_answer / question_text / options / answer_text
    """
    if "统计" in subject or "statistics" in subject.lower():
        chapter_hint    = "如：描述统计/概率分布/推断统计/回归分析/抽样分布等"
        difficulty_hint = "1=基础概念，2=简单计算，3=综合应用，4=复杂推断，5=综合难题"
    elif "化学" in subject or "chemistry" in subject.lower():
        chapter_hint    = "如：原子结构/化学键/分子极性/热化学/化学平衡/电化学等"
        difficulty_hint = "1=基础概念，2=简单计算，3=综合应用，4=复杂推理，5=综合难题"
    else:
        chapter_hint    = "根据题目内容判断所属章节"
        difficulty_hint = "1=基础，2=简单，3=中等，4=较难，5=难题"

    has_answer_img = bool(a_img_path and os.path.exists(a_img_path))

    # 构建图片内容块
    q_img_block = {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{_encode_image(q_img_path)}", "detail": "high"},
    }
    a_img_block = None
    if has_answer_img:
        a_img_block = {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{_encode_image(a_img_path)}", "detail": "high"},
        }

    # ── Step 1: OCR 提取 ──────────────────────────────────────
    step1_user_content: list[dict] = [
        {"type": "text", "text": (
            "请忠实提取这道题目图片中的所有文字内容，包括：\n"
            "- 题号（如果有）\n"
            "- 完整题干（包括所有条件、数据、图表描述）\n"
            "- 所有选项（A/B/C/D/E，一字不漏）\n"
            "- 如果有图表/图片，描述其内容（坐标轴、数据点、趋势等）\n"
            "- 数学公式用 LaTeX 格式（行内 $...$，独立 $$...$$）\n\n"
            "只输出提取的文字，不要分析或推断，不要输出 JSON。"
        )},
        q_img_block,
    ]
    if has_answer_img:
        step1_user_content += [
            {"type": "text", "text": "以下是该题的解析图片，同样请忠实提取所有文字："},
            a_img_block,
        ]

    try:
        r1 = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": step1_user_content}],
            max_tokens=2048,
        )
        ocr_text = r1.choices[0].message.content.strip()
    except Exception as e:
        print(f"✗ Step1 OCR 失败: {e}")
        return {}

    # ── Step 2: 推理分析 ──────────────────────────────────────
    if has_answer_img:
        step2_prompt = (
            f"这是一道 {subject} 题目，以下是从图片中提取的文字：\n\n"
            f"{ocr_text}\n\n"
            "解析图片中已包含答案和解析。请：\n"
            "1. 从解析中找出正确答案（A/B/C/D/E）\n"
            "2. 用自己的话总结解析思路（2-4句话）\n"
            "3. 判断所属章节和核心知识点\n"
            "4. 评估难度（1-5）\n\n"
            "请逐步分析，不要输出 JSON。"
        )
    else:
        step2_prompt = (
            f"这是一道 {subject} 题目，以下是从图片中提取的文字：\n\n"
            f"{ocr_text}\n\n"
            "请逐步分析：\n"
            "1. 题目考查的核心知识点是什么？\n"
            "2. 逐一分析每个选项，判断正误\n"
            "3. 得出正确答案（A/B/C/D/E），如果图片不完整导致无法确定则说明原因\n"
            "4. 判断所属章节\n"
            "5. 评估难度（1-5）\n\n"
            "请逐步推理，不要输出 JSON。"
        )

    try:
        r2 = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": step1_user_content},
                {"role": "assistant", "content": ocr_text},
                {"role": "user", "content": step2_prompt},
            ],
            max_tokens=2048,
        )
        reasoning = r2.choices[0].message.content.strip()
    except Exception as e:
        print(f"✗ Step2 推理失败: {e}")
        return {}

    # ── Step 3: 结构化输出 ────────────────────────────────────
    step3_prompt = (
        "根据以上分析，输出严格 JSON，不要有任何其他文字：\n"
        "{\n"
        f'  "chapter": "<所属章节，{chapter_hint}>",\n'
        '  "topic_tags": ["<核心知识点1>", "<核心知识点2>"],\n'
        f'  "difficulty": <{difficulty_hint}>,\n'
        '  "question_type": "<单选|多选|自由作答>",\n'
        '  "correct_answer": "<A|B|C|D|E，若确实无法判断则填 null>",\n'
        '  "question_text": "<完整题干，LaTeX 公式保留>",\n'
        '  "options": {"A": "...", "B": "...", "C": "...", "D": "..."},\n'
        '  "answer_text": "<解析文字，2-5句，说明为什么选这个答案>"\n'
        "}\n"
        "注意：topic_tags 最多 3 个；options 只包含实际存在的选项键。"
    )

    try:
        r3 = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": step1_user_content},
                {"role": "assistant", "content": ocr_text},
                {"role": "user", "content": step2_prompt},
                {"role": "assistant", "content": reasoning},
                {"role": "user", "content": step3_prompt},
            ],
            max_tokens=2048,
        )
        result = _parse_json_response(r3.choices[0].message.content)
        if isinstance(result, list):
            result = result[0] if result else {}
        return result if isinstance(result, dict) else {}
    except Exception as e:
        print(f"✗ Step3 结构化输出失败: {e}")
        return {}


def _merge_global_json(global_path: str, new_entries: list[dict], teacher_id: str):
    """将本次解析结果合并写入全局 questions.json（按 teacher_id 覆盖旧条目）"""
    existing: list[dict] = []
    if os.path.exists(global_path):
        with open(global_path, encoding="utf-8") as f:
            existing = json.load(f)
    # 移除同一 teacher_id 的旧条目（本次全量替换）
    if teacher_id:
        existing = [q for q in existing if q.get("teacher_id") != teacher_id]
    merged = sorted(existing + new_entries, key=lambda q: q["id"])
    with open(global_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"全局题目库已更新: {global_path}（共 {len(merged)} 条）")


def run_parse_questions(
    q_pages: list[dict],
    a_pages: list[dict],
    out_dir: str,
    client: OpenAI,
    model: str,
    teacher_id: str = "",
    subject: str = "",
    source_file: str = "",
    max_questions: int = 0,
    use_chapter_map: bool = True,
    global_json_path: str | None = None,
):
    """
    P0 核心流程：
        Step 1 — 题目/解析页面边界检测
        Step 2 — 按题裁剪图片（含跨页拼接）
        Step 3 — AI 结构化解析，生成 questions.json

    teacher_id / subject / source_file  写入每条题目元数据。
    max_questions > 0 时，收集/解析到足够题目后提前停止。
    use_chapter_map=True 时按 CHAPTER_START_PAGES 建章节索引（AP统计专项突破专用）。
    global_json_path 若提供，解析完成后同步更新全局 questions.json。
    """
    q_img_dir           = os.path.join(out_dir, "images", "questions")
    a_img_dir           = os.path.join(out_dir, "images", "answers")
    questions_json_path = os.path.join(out_dir, "questions.json")

    Path(q_img_dir).mkdir(parents=True, exist_ok=True)
    Path(a_img_dir).mkdir(parents=True, exist_ok=True)

    # 加载已有 questions.json（支持断点续跑）
    if os.path.exists(questions_json_path):
        with open(questions_json_path, encoding="utf-8") as f:
            existing = json.load(f)
        questions_db: list[dict] = existing
        parsed_images: set[str] = {q["question_image"] for q in existing}
        next_id = max(q["id"] for q in existing) + 1 if existing else 1
        print(f"加载已有题目库: {len(questions_db)} 道题，下一个 id={next_id}")
    else:
        questions_db  = []
        parsed_images = set()
        next_id       = 1

    # ── 章节页码映射（AP统计专项突破专用） ────────────────────
    CHAPTER_START_PAGES = [
        4,    # Exploring Data
        42,   # Sampling, Surveys and Experiments
        57,   # Probability and Probability Distribution
        79,   # Statistic and Sampling Distribution
        85,   # Sampling Distribution of Sample Mean/Proportion & CLT
        91,   # Sampling Distribution of Two Independent Means
        95,   # Parameter Estimation (General)
        101,  # Interval Estimation for One Population Parameter
        107,  # Interval Estimation for Two Population Parameters
        110,  # Regression Line Interval Estimation & Sample Size
        115,  # Hypothesis Testing
        127,  # Inference about One Population Parameter
        132,  # Inference for Two Population Parameters
    ]

    def _chapter_idx(page_num: int) -> int:
        if not use_chapter_map:
            return 0
        idx = 0
        for i, start in enumerate(CHAPTER_START_PAGES):
            if page_num >= start:
                idx = i
        return idx

    # ── Step 1: 检测题目 PDF 边界 ─────────────────────────────
    print("\n[Step 1] 检测题目 PDF 边界...")
    q_segments: dict[tuple, list[dict]] = {}

    for idx, page in enumerate(q_pages, 1):
        # 已收集到足够的新题目时提前停止扫描
        if max_questions > 0 and len(q_segments) >= max_questions:
            print(f"  已收集 {len(q_segments)} 道题，达到上限 {max_questions}，停止扫描")
            break

        print(f"  [{idx}/{len(q_pages)}] 第 {page['page']} 页...", end=" ", flush=True)
        boundaries = detect_question_boundaries(client, page["image"], model)
        if not boundaries:
            print("无题目")
            continue
        nums    = [b["question_number"] for b in boundaries]
        ch_idx  = _chapter_idx(page["page"])
        print(f"找到 {len(boundaries)} 道: {nums}  [章节{ch_idx}]")
        for b_idx, b in enumerate(boundaries):
            q_num   = b["question_number"]
            seg_key = (ch_idx, page["page"], b_idx)
            q_segments.setdefault(seg_key, []).append({
                "page":                    page["page"],
                "img":                     page["image"],
                "top":                     b.get("top_ratio", 0.0),
                "bottom":                  b.get("bottom_ratio", 1.0),
                "q_num":                   q_num,
                "continues_from_previous": b.get("continues_from_previous", False),
                "continues_to_next":       b.get("continues_to_next", False),
            })

    # ── Step 1b: 检测解析 PDF 边界 ───────────────────────────
    a_segments: dict[tuple, list[dict]] = {}
    if a_pages:
        print("\n[Step 1b] 检测解析 PDF 边界...")
        for idx, page in enumerate(a_pages, 1):
            print(f"  [{idx}/{len(a_pages)}] 第 {page['page']} 页...", end=" ", flush=True)
            boundaries = detect_question_boundaries(client, page["image"], model, is_answer_page=True)
            if not boundaries:
                print("无内容")
                continue
            nums   = [b["question_number"] for b in boundaries]
            ch_idx = _chapter_idx(page["page"])
            print(f"找到 {len(boundaries)} 条解析: {nums}  [章节{ch_idx}]")
            for b_idx, b in enumerate(boundaries):
                q_num   = b["question_number"]
                seg_key = (ch_idx, page["page"], b_idx)
                a_segments.setdefault(seg_key, []).append({
                    "page":   page["page"],
                    "img":    page["image"],
                    "top":    b.get("top_ratio", 0.0),
                    "bottom": b.get("bottom_ratio", 1.0),
                    "q_num":  q_num,
                })

    # ── Step 2: 裁剪题目图片 ──────────────────────────────────
    print("\n[Step 2] 裁剪题目图片...")
    q_img_map: dict[tuple, str] = {}
    global_id = next_id

    # 统计每页每题号的出现次数，用于添加下角标
    page_qnum_count: dict[tuple, int] = {}
    for seg_key in sorted(q_segments.keys()):
        ch_idx, page_num, b_idx = seg_key
        segs = q_segments[seg_key]
        if segs:
            q_num = segs[0].get("q_num", 0)
            key = (page_num, q_num)
            page_qnum_count[key] = page_qnum_count.get(key, 0) + 1

    page_qnum_used: dict[tuple, int] = {}
    for seg_key in sorted(q_segments.keys()):
        ch_idx, page_num, b_idx = seg_key
        segs = q_segments[seg_key]
        if not segs:
            continue

        q_num = segs[0].get("q_num", 0)
        key = (page_num, q_num)
        subscript = page_qnum_used.get(key, 0) + 1
        page_qnum_used[key] = subscript

        # 新命名：p页码_q题号_下角标.png
        if page_qnum_count[key] > 1:
            img_name = f"p{page_num:04d}_q{q_num}_{subscript}.png"
        else:
            img_name = f"p{page_num:04d}_q{q_num}.png"
        img_path = os.path.join(q_img_dir, img_name)

        if os.path.exists(img_path):
            print(f"  {img_name} 已存在，跳过")
            q_img_map[seg_key] = img_path
            global_id += 1
            continue

        if len(segs) == 1:
            seg = segs[0]
            ok = crop_image_region(seg["img"], seg["top"], seg["bottom"], img_path)
        else:
            tmp_paths = []
            for i, seg in enumerate(segs):
                tmp = os.path.join(q_img_dir, f"tmp_{global_id}_{i}.png")
                if crop_image_region(seg["img"], seg["top"], seg["bottom"], tmp):
                    tmp_paths.append(tmp)
            ok = concat_images_vertical(tmp_paths, img_path) if tmp_paths else False
            for p in tmp_paths:
                if os.path.exists(p):
                    os.remove(p)
        if ok:
            print(f"  {img_name} ✓")
            q_img_map[seg_key] = img_path
            global_id += 1

    print("\n[Step 2b] 裁剪解析图片...")
    a_img_map: dict[tuple, str] = {}
    a_global_id = next_id

    # 统计每页每题号的出现次数
    a_page_qnum_count: dict[tuple, int] = {}
    for seg_key in sorted(a_segments.keys()):
        ch_idx, page_num, b_idx = seg_key
        segs = a_segments[seg_key]
        if segs:
            q_num = segs[0].get("q_num", 0)
            key = (page_num, q_num)
            a_page_qnum_count[key] = a_page_qnum_count.get(key, 0) + 1

    a_page_qnum_used: dict[tuple, int] = {}
    for seg_key in sorted(a_segments.keys()):
        ch_idx, page_num, b_idx = seg_key
        segs = a_segments[seg_key]
        if not segs:
            continue

        q_num = segs[0].get("q_num", 0)
        key = (page_num, q_num)
        subscript = a_page_qnum_used.get(key, 0) + 1
        a_page_qnum_used[key] = subscript

        if a_page_qnum_count[key] > 1:
            img_name = f"p{page_num:04d}_a{q_num}_{subscript}.png"
        else:
            img_name = f"p{page_num:04d}_a{q_num}.png"
        img_path = os.path.join(a_img_dir, img_name)

        if os.path.exists(img_path):
            print(f"  {img_name} 已存在，跳过")
            a_img_map[seg_key] = img_path
            a_global_id += 1
            continue

        if len(segs) == 1:
            seg = segs[0]
            ok = crop_image_region(seg["img"], seg["top"], seg["bottom"], img_path)
        else:
            tmp_paths = []
            for i, seg in enumerate(segs):
                tmp = os.path.join(a_img_dir, f"tmp_{a_global_id}_{i}.png")
                if crop_image_region(seg["img"], seg["top"], seg["bottom"], tmp):
                    tmp_paths.append(tmp)
            ok = concat_images_vertical(tmp_paths, img_path) if tmp_paths else False
            for p in tmp_paths:
                if os.path.exists(p):
                    os.remove(p)
        if ok:
            print(f"  {img_name} ✓")
            a_img_map[seg_key] = img_path
            a_global_id += 1

    # ── Step 3: 结构化解析 ────────────────────────────────────
    print("\n[Step 3] AI 结构化解析题目...")
    cur_id        = next_id
    parsed_count  = 0

    for seg_key in sorted(q_img_map.keys()):
        if max_questions > 0 and parsed_count >= max_questions:
            print(f"  已解析 {parsed_count} 道，达到上限 {max_questions}，停止")
            break

        q_img = q_img_map[seg_key]
        if q_img in parsed_images:
            print(f"  {os.path.basename(q_img)} 已解析，跳过")
            cur_id        += 1
            parsed_count  += 1
            continue

        a_img = a_img_map.get(seg_key)
        print(f"  解析 {os.path.basename(q_img)}...", end=" ", flush=True)
        meta = parse_question_metadata(client, q_img, a_img, model, subject=subject)

        entry: dict = {
            "id":             cur_id,
            "teacher_id":     teacher_id,
            "subject":        subject,
            "source_file":    source_file,
            "chapter":        meta.get("chapter", ""),
            "topic_tags":     meta.get("topic_tags", []),
            "difficulty":     meta.get("difficulty", 3),
            "question_type":  meta.get("question_type", "单选"),
            "correct_answer": meta.get("correct_answer"),
            "question_image": q_img,
            "answer_image":   a_img,
            "question_text":  meta.get("question_text", ""),
            "options":        meta.get("options"),
            "answer_text":    meta.get("answer_text", ""),
        }
        questions_db.append(entry)
        parsed_images.add(q_img)
        cur_id       += 1
        parsed_count += 1
        print(f"✓  id:{entry['id']}  难度:{entry['difficulty']}  章节:{entry['chapter']}")

        # 每道题解析后立即保存（断点续跑安全）
        with open(questions_json_path, "w", encoding="utf-8") as f:
            json.dump(
                sorted(questions_db, key=lambda q: q["id"]),
                f, ensure_ascii=False, indent=2,
            )

    total = len(questions_db)
    print(f"\n题目库已保存: {questions_json_path}（共 {total} 道题）")

    # 同步更新全局 questions.json
    if global_json_path:
        Path(global_json_path).parent.mkdir(parents=True, exist_ok=True)
        _merge_global_json(global_json_path, questions_db, teacher_id)

    return sorted(questions_db, key=lambda q: q["id"])


# ── 主程序入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AP PDF 题目切分 + OCR + 结构化解析")
    parser.add_argument("--pdf",             default="AP统计专项突破.pdf", help="题目 PDF 文件路径")
    parser.add_argument("--answer-pdf",      default=None,                help="解析 PDF 文件路径")
    parser.add_argument("--out",             default="output",             help="输出根目录（建议按老师：output/chenxi）")
    parser.add_argument("--dpi",             type=int, default=150,        help="图片分辨率（默认 150）")
    parser.add_argument("--ocr",             action="store_true",          help="执行按页 OCR 识别")
    parser.add_argument("--parse-questions", action="store_true",          help="P0：题目边界检测 + 裁剪 + 结构化解析")
    parser.add_argument("--model",           default=DEFAULT_MODEL,        help=f"模型（默认 {DEFAULT_MODEL}）")
    parser.add_argument("--api-key",         default=UNIAPI_KEY,           help="API Key")
    parser.add_argument("--base-url",        default=UNIAPI_BASE,          help="API Base URL")
    parser.add_argument("--pages",           default=None,                 help="页范围，如 1-50 或 5")
    # 多老师参数
    parser.add_argument("--teacher-id",      default="",                   help="教师 ID（chenxi / jiangzhi）")
    parser.add_argument("--subject",         default="",                   help="科目名称（AP统计 / AP化学）")
    parser.add_argument("--source-file",     default="",                   help="来源文件名（默认取 --pdf 文件名）")
    parser.add_argument("--max-questions",   type=int, default=0,          help="最多提取题目数，0=不限")
    parser.add_argument("--no-chapter-map",  action="store_true",          help="禁用章节页码映射（单次考试 MC 卷适用）")
    parser.add_argument("--global-json",     default=None,                 help="全局 questions.json 路径，解析后自动合并")
    args = parser.parse_args()

    source_file = args.source_file or Path(args.pdf).name

    out_dir    = args.out
    img_dir    = os.path.join(out_dir, "images")
    txt_dir    = os.path.join(out_dir, "texts")
    index_path = os.path.join(out_dir, "index.md")
    state_path = os.path.join(out_dir, "state.json")

    # ── 加载或生成题目 PDF 页面列表 ──────────────────────────
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            all_pages = json.load(f)
        print(f"加载已有状态: {len(all_pages)} 页")
    else:
        print(f"切分 PDF: {args.pdf}")
        all_pages = pdf_to_images(args.pdf, img_dir, dpi=args.dpi)

    # 页范围过滤
    if args.pages:
        start, end = parse_page_range(args.pages, len(all_pages))
        pages = [p for p in all_pages if start <= p["page"] <= end]
        print(f"处理范围: 第 {start}–{end} 页，共 {len(pages)} 页")
    else:
        pages = all_pages

    # ── 按页 OCR ─────────────────────────────────────────────
    if args.ocr:
        print(f"\n开始 OCR（模型: {args.model}）")
        client = get_client(api_key=args.api_key, base_url=args.base_url)
        run_ocr(pages, txt_dir, model=args.model, client=client)
        page_map = {p["page"]: p for p in pages}
        for p in all_pages:
            if p["page"] in page_map:
                p["ocr_txt"] = page_map[p["page"]]["ocr_txt"]

    # ── P0：题目切分 & 结构化解析 ────────────────────────────
    if args.parse_questions:
        client = get_client(api_key=args.api_key, base_url=args.base_url)

        a_all_pages: list[dict] = []
        if args.answer_pdf:
            a_state_path = os.path.join(out_dir, "state_answers.json")
            a_img_dir    = os.path.join(out_dir, "images_answers")

            if os.path.exists(a_state_path):
                with open(a_state_path, encoding="utf-8") as f:
                    a_all_pages = json.load(f)
                print(f"加载解析 PDF 状态: {len(a_all_pages)} 页")
            else:
                print(f"\n切分解析 PDF: {args.answer_pdf}")
                a_all_pages = pdf_to_images(args.answer_pdf, a_img_dir, dpi=args.dpi)
                with open(a_state_path, "w", encoding="utf-8") as f:
                    json.dump(a_all_pages, f, ensure_ascii=False, indent=2)

            if args.pages:
                start, end  = parse_page_range(args.pages, len(a_all_pages))
                a_all_pages = [p for p in a_all_pages if start <= p["page"] <= end]
                print(f"解析 PDF 范围: 第 {start}–{end} 页，共 {len(a_all_pages)} 页")

        run_parse_questions(
            q_pages          = pages,
            a_pages          = a_all_pages,
            out_dir          = out_dir,
            client           = client,
            model            = args.model,
            teacher_id       = args.teacher_id,
            subject          = args.subject,
            source_file      = source_file,
            max_questions    = args.max_questions,
            use_chapter_map  = not args.no_chapter_map,
            global_json_path = args.global_json,
        )

    # ── 保存状态 & 生成目录 ───────────────────────────────────
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(all_pages, f, ensure_ascii=False, indent=2)

    build_index(all_pages, index_path)
    print("\n完成。")


if __name__ == "__main__":
    main()
