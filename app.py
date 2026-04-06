import os
import json
import uuid
from datetime import datetime
from pathlib import Path

import requests as http_requests

from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import anthropic

load_dotenv()

IS_VERCEL = os.getenv("VERCEL", "") == "1"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

if IS_VERCEL:
    UPLOAD_FOLDER = Path("/tmp/uploads")
    OUTPUT_FOLDER = Path("/tmp/outputs")
else:
    UPLOAD_FOLDER = Path(__file__).parent / "uploads"
    OUTPUT_FOLDER = Path(__file__).parent / "outputs"

TEMPLATE_FOLDER = Path(__file__).parent / "company_templates"

UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)

ALLOWED_TEXT_EXT = {".txt", ".md", ".doc", ".docx", ".pdf", ".csv", ".xlsx", ".xls"}
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".mp4"}
ALLOWED_EXTENSIONS = ALLOWED_TEXT_EXT | ALLOWED_AUDIO_EXT

app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB


def load_template(template_type: str) -> dict:
    path = TEMPLATE_FOLDER / f"{template_type}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def transcribe_audio_remote(file_path: str, colab_url: str, initial_prompt: str = "") -> dict:
    """Send audio to remote Colab Whisper API for transcription."""
    url = colab_url.rstrip("/") + "/transcribe"

    with open(file_path, "rb") as f:
        files = {"file": (Path(file_path).name, f)}
        data = {"language": "zh"}
        if initial_prompt:
            data["initial_prompt"] = initial_prompt

        resp = http_requests.post(url, files=files, data=data, timeout=600)

    if resp.status_code != 200:
        error = resp.json().get("error", "Unknown error")
        raise RuntimeError(f"Colab API error: {error}")

    return resp.json()


def transcribe_audio_local(file_path: str, model_size: str = "large-v3", initial_prompt: str = "") -> dict:
    """Transcribe audio file using local OpenAI Whisper."""
    try:
        import whisper
    except ImportError:
        raise RuntimeError(
            "語音轉文字功能需要安裝 openai-whisper。"
            "請執行 pip install -r requirements-local.txt"
        )

    model = whisper.load_model(model_size)

    transcribe_opts = {"language": "zh"}
    if initial_prompt:
        transcribe_opts["initial_prompt"] = initial_prompt

    result = model.transcribe(file_path, **transcribe_opts)

    timestamped_lines = []
    for seg in result.get("segments", []):
        start = int(seg["start"])
        mm, ss = divmod(start, 60)
        timestamped_lines.append(f"[{mm:02d}:{ss:02d}] {seg['text'].strip()}")

    return {
        "text": result["text"],
        "timestamped": "\n".join(timestamped_lines),
        "segments": [
            {
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"].strip(),
            }
            for seg in result.get("segments", [])
        ],
    }


def read_text_file(file_path: str) -> str:
    """Read content from a text file."""
    ext = Path(file_path).suffix.lower()
    if ext == ".docx":
        from docx import Document

        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs)
    elif ext == ".csv":
        import csv

        rows = []
        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            for row in reader:
                rows.append(",".join(row))
        return "\n".join(rows)
    elif ext in (".xlsx", ".xls"):
        from openpyxl import load_workbook

        wb = load_workbook(file_path, read_only=True, data_only=True)
        lines = []
        for sheet in wb.sheetnames:
            ws = wb[sheet]
            lines.append(f"[工作表: {sheet}]")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c) if c is not None else "" for c in row]
                lines.append(",".join(cells))
            lines.append("")
        wb.close()
        return "\n".join(lines)
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()


def process_with_ai(meeting_text: str, template_type: str) -> dict:
    """Use Claude API to process meeting notes into structured output."""
    client = anthropic.Anthropic()
    template = load_template(template_type)

    sections_desc = "\n".join(
        f"### {s['title']}\n{s['description']}" for s in template["sections"]
    )

    type_labels = {
        "research": "市場研究報告",
        "proposal": "行銷提案書",
        "quotation": "專案報價單",
    }

    prompt = f"""你是一位資深行銷顧問。根據以下會議記錄，產出一份「{type_labels[template_type]}」。

請嚴格按照以下格式架構來整理內容：

{sections_desc}

## 會議記錄內容：
{meeting_text}

## 輸出要求：
1. 使用繁體中文
2. 請以 JSON 格式回覆，格式如下：
{{
  "title": "文件標題",
  "date": "產出日期",
  "sections": [
    {{
      "title": "段落標題",
      "content": "段落內容（支援 Markdown 格式）"
    }}
  ]
}}
3. 內容要專業、具體、可執行
4. 從會議記錄中提取所有相關資訊，不要遺漏重要細節
5. 如果會議記錄中資訊不足，請在該段落標註「[待補充]」
6. 報價單的服務項目明細請用表格呈現

請直接回覆 JSON，不要加任何其他文字。"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()
    # Remove markdown code block wrapper if present
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(lines[1:])
        if response_text.endswith("```"):
            response_text = response_text[:-3].strip()

    return json.loads(response_text)


def generate_docx(result: dict, template_type: str) -> str:
    """Generate a .docx file from the AI result."""
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # Title
    title_para = doc.add_heading(result["title"], level=0)
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Date
    date_para = doc.add_paragraph(f"日期：{result.get('date', datetime.now().strftime('%Y-%m-%d'))}")
    date_para.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_paragraph("")  # spacer

    # Sections
    for section in result["sections"]:
        doc.add_heading(section["title"], level=1)

        content = section["content"]
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("| "):
                # Table row - simplified handling
                doc.add_paragraph(line, style="List Bullet")
            elif line.startswith("- ") or line.startswith("* "):
                doc.add_paragraph(line[2:], style="List Bullet")
            elif line.startswith("## "):
                doc.add_heading(line[3:], level=2)
            elif line.startswith("### "):
                doc.add_heading(line[4:], level=3)
            else:
                doc.add_paragraph(line)

    # Save
    filename = f"{template_type}_{uuid.uuid4().hex[:8]}.docx"
    output_path = OUTPUT_FOLDER / filename
    doc.save(str(output_path))
    return filename


@app.route("/")
def home():
    return render_template("home.html", active_page="dashboard")


@app.route("/meeting")
def meeting():
    return render_template("index.html", active_page="meeting")


@app.route("/ad-report")
def ad_report():
    return render_template("ad_report.html", active_page="ad_report")


@app.route("/work-dispatch")
def work_dispatch():
    return render_template("work_dispatch.html", active_page="work_dispatch")


@app.route("/api/colab-health", methods=["POST"])
def check_colab_health():
    data = request.get_json()
    colab_url = data.get("url", "").strip().rstrip("/")
    if not colab_url:
        return jsonify({"error": "請輸入 Colab API URL"}), 400
    try:
        resp = http_requests.get(f"{colab_url}/health", timeout=10)
        return jsonify(resp.json())
    except Exception as e:
        return jsonify({"error": f"無法連線到 Colab：{str(e)}"}), 500


@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "沒有上傳檔案"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "未選擇檔案"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({"error": f"不支援的檔案格式：{ext}"}), 400

    filename = secure_filename(f"{uuid.uuid4().hex}_{file.filename}")
    file_path = UPLOAD_FOLDER / filename
    file.save(str(file_path))

    # Extract text
    try:
        if ext in ALLOWED_AUDIO_EXT:
            initial_prompt = request.form.get("initial_prompt", "")
            colab_url = request.form.get("colab_url", "").strip()

            if colab_url:
                # Use remote Colab Whisper API
                result = transcribe_audio_remote(str(file_path), colab_url, initial_prompt)
            else:
                # Use local Whisper
                model_size = request.form.get("whisper_model", "large-v3")
                result = transcribe_audio_local(str(file_path), model_size, initial_prompt)

            return jsonify({
                "text": result["text"],
                "timestamped": result["timestamped"],
                "segments": result["segments"],
                "filename": filename,
                "is_audio": True,
            })
        else:
            text = read_text_file(str(file_path))
            return jsonify({"text": text, "filename": filename, "is_audio": False})
    except Exception as e:
        return jsonify({"error": f"檔案處理失敗：{str(e)}"}), 500


@app.route("/api/process", methods=["POST"])
def process_meeting():
    data = request.get_json()
    if not data or "text" not in data:
        return jsonify({"error": "缺少會議記錄內容"}), 400

    text = data["text"]
    doc_types = data.get("types", ["research", "proposal", "quotation"])

    results = {}
    for doc_type in doc_types:
        try:
            result = process_with_ai(text, doc_type)
            results[doc_type] = result
        except Exception as e:
            results[doc_type] = {"error": str(e)}

    return jsonify(results)


@app.route("/api/export/<doc_type>", methods=["POST"])
def export_document(doc_type):
    if doc_type not in ("research", "proposal", "quotation"):
        return jsonify({"error": "無效的文件類型"}), 400

    data = request.get_json()
    if not data:
        return jsonify({"error": "缺少文件資料"}), 400

    try:
        filename = generate_docx(data, doc_type)
        return jsonify({"filename": filename})
    except Exception as e:
        return jsonify({"error": f"匯出失敗：{str(e)}"}), 500


@app.route("/api/download/<filename>")
def download_file(filename):
    file_path = OUTPUT_FOLDER / secure_filename(filename)
    if not file_path.exists():
        return jsonify({"error": "檔案不存在"}), 404
    return send_file(str(file_path), as_attachment=True)


@app.route("/api/templates", methods=["GET"])
def get_templates():
    templates = {}
    for tpl_file in TEMPLATE_FOLDER.glob("*.json"):
        with open(tpl_file, "r", encoding="utf-8") as f:
            templates[tpl_file.stem] = json.load(f)
    return jsonify(templates)


@app.route("/api/templates/<template_type>", methods=["PUT"])
def update_template(template_type):
    if template_type not in ("research", "proposal", "quotation"):
        return jsonify({"error": "無效的模板類型"}), 400

    data = request.get_json()
    path = TEMPLATE_FOLDER / f"{template_type}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return jsonify({"message": "模板已更新"})


PLATFORM_ORDER = ["meta", "google", "line", "tiktok"]
PLATFORM_LABELS = {
    "meta": "Meta (Facebook/Instagram)",
    "google": "Google Ads",
    "line": "LINE Ads",
    "tiktok": "TikTok Ads",
}


def process_ad_report_with_ai(report_info: dict) -> dict:
    """Use Claude API to generate ad monthly report."""
    client = anthropic.Anthropic()

    client_name = report_info.get("client_name", "")
    company_name = report_info.get("company_name", "")
    report_month = report_info.get("report_month", "")
    platforms_data = report_info.get("platforms", {})

    # Build platform data description
    platform_sections = []
    ordered_platforms = []
    extra_platforms = []

    for p in PLATFORM_ORDER:
        if p in platforms_data:
            ordered_platforms.append(p)
    for p in platforms_data:
        if p not in PLATFORM_ORDER:
            extra_platforms.append(p)

    all_platforms = ordered_platforms + extra_platforms

    for p in all_platforms:
        data = platforms_data[p]
        label = PLATFORM_LABELS.get(p, p)
        platform_sections.append(f"### {label}\n{data}")

    platforms_text = "\n\n".join(platform_sections)

    # Build the section order instruction
    platform_order_desc = []
    for p in all_platforms:
        label = PLATFORM_LABELS.get(p, p)
        platform_order_desc.append(label)
    order_text = " → ".join(platform_order_desc)

    prompt = f"""你是一位資深數位廣告顧問。請根據以下廣告後台數據，產出一份完整的廣告月報。

## 基本資訊
- 客戶名稱：{client_name}
- 公司名稱：{company_name}
- 報告月份：{report_month}

## 各平台廣告數據：
{platforms_text}

## 月報架構（請嚴格按照以下順序）：

1. **封面** - 包含客戶名稱「{client_name}」、公司名稱「{company_name}」、報告月份「{report_month}」、月報標題
2. **大綱** - 本月報內容目錄
3. **總覽數據成效** - 彙總所有平台的 Total 數據（總花費、總曝光、總點擊、總轉換等），並與上月或目標做比較分析
4. **本月廣告數據洞察**：
   - 廣告受眾洞察：分析哪些受眾表現最好/最差，年齡、性別、地區等維度
   - 廣告素材洞察：分析哪些素材類型/創意方向表現較好，點擊率、互動率等
5. **各平台成效報告**（順序：{order_text}）：
   每個平台包含：
   - 花費與成效總覽
   - 各廣告活動/廣告組合表現（用表格呈現）
   - 關鍵指標分析（CPM、CPC、CTR、CPA、ROAS 等）
   - 該平台優化建議
6. **下月預計調整總結** - 根據本月數據，提出下月的策略調整方向、預算分配建議、素材優化方向
7. **感謝頁** - 專業的結尾感謝語

## 輸出要求：
1. 使用繁體中文
2. 請以 JSON 格式回覆：
{{
  "cover": {{
    "title": "月報標題",
    "client_name": "{client_name}",
    "company_name": "{company_name}",
    "report_month": "{report_month}"
  }},
  "sections": [
    {{
      "id": "段落識別碼（如 outline, total, insights, meta, google 等）",
      "title": "段落標題",
      "content": "段落內容（支援 Markdown 格式，表格請用 Markdown 表格）"
    }}
  ],
  "closing": {{
    "title": "感謝頁標題",
    "content": "感謝語內容"
  }}
}}
3. 數據要精確引用，不要虛構數字
4. 表格要清楚呈現各項指標
5. 洞察要具體、可執行
6. 如果數據不足，請標註「[待補充]」

請直接回覆 JSON，不要加任何其他文字。"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(lines[1:])
        if response_text.endswith("```"):
            response_text = response_text[:-3].strip()

    return json.loads(response_text)


def generate_ad_report_docx(result: dict) -> str:
    """Generate a .docx ad report file."""
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # Cover page
    cover = result.get("cover", {})
    for _ in range(6):
        doc.add_paragraph("")

    title_para = doc.add_heading(cover.get("title", "廣告月報"), level=0)
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph("")
    client_p = doc.add_paragraph(f"客戶：{cover.get('client_name', '')}")
    client_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    company_p = doc.add_paragraph(f"製作：{cover.get('company_name', '')}")
    company_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    month_p = doc.add_paragraph(f"報告月份：{cover.get('report_month', '')}")
    month_p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_page_break()

    # Sections
    for section in result.get("sections", []):
        doc.add_heading(section["title"], level=1)
        content = section.get("content", "")

        for line in content.split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                continue
            if line_stripped.startswith("| ") and line_stripped.endswith("|"):
                if line_stripped.replace("|", "").replace("-", "").replace(":", "").replace(" ", "") == "":
                    continue  # skip separator
                cells = [c.strip() for c in line_stripped.split("|") if c.strip()]
                doc.add_paragraph("  |  ".join(cells))
            elif line_stripped.startswith("- ") or line_stripped.startswith("* "):
                doc.add_paragraph(line_stripped[2:], style="List Bullet")
            elif line_stripped.startswith("### "):
                doc.add_heading(line_stripped[4:], level=3)
            elif line_stripped.startswith("## "):
                doc.add_heading(line_stripped[3:], level=2)
            else:
                doc.add_paragraph(line_stripped)

    # Closing page
    doc.add_page_break()
    closing = result.get("closing", {})
    for _ in range(6):
        doc.add_paragraph("")
    closing_title = doc.add_heading(closing.get("title", "Thank You"), level=0)
    closing_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("")
    closing_content = doc.add_paragraph(closing.get("content", ""))
    closing_content.alignment = WD_ALIGN_PARAGRAPH.CENTER

    filename = f"ad_report_{uuid.uuid4().hex[:8]}.docx"
    output_path = OUTPUT_FOLDER / filename
    doc.save(str(output_path))
    return filename


@app.route("/api/ad-report/process", methods=["POST"])
def process_ad_report():
    data = request.get_json()
    if not data:
        return jsonify({"error": "缺少報告資料"}), 400

    required = ["client_name", "company_name", "report_month", "platforms"]
    for field in required:
        if not data.get(field):
            return jsonify({"error": f"缺少必填欄位：{field}"}), 400

    if not data["platforms"]:
        return jsonify({"error": "請至少輸入一個平台的數據"}), 400

    try:
        result = process_ad_report_with_ai(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


DEFAULT_ROLES = [
    {"id": "finance", "name": "財務", "icon": "💰", "desc": "預算管理、請款、發票"},
    {"id": "design", "name": "美術", "icon": "🎨", "desc": "視覺設計、素材製作"},
    {"id": "planning", "name": "企劃", "icon": "📋", "desc": "策略規劃、內容企劃、專案管理"},
    {"id": "webdev", "name": "網站工程師", "icon": "💻", "desc": "網站開發、Landing Page、技術串接"},
    {"id": "ads", "name": "廣告投放", "icon": "📢", "desc": "廣告投放、優化、成效追蹤"},
    {"id": "seo", "name": "SEO", "icon": "🔍", "desc": "搜尋引擎優化、內容優化、技術 SEO"},
    {"id": "social", "name": "社群經營", "icon": "📱", "desc": "社群內容、互動管理、KOL 合作"},
    {"id": "video", "name": "影音製作", "icon": "🎬", "desc": "影片企劃、拍攝、剪輯"},
]


def process_work_dispatch_with_ai(quotation_text: str, team_roles: list, project_name: str) -> dict:
    """Use Claude API to break quotation into work packages assigned to team roles."""
    client = anthropic.Anthropic()

    roles_desc = "\n".join(
        f"- **{r['name']}** ({r['id']}): {r['desc']}" for r in team_roles
    )

    prompt = f"""你是一位資深專案經理。請根據以下報價單/提案內容，將工作拆分成具體的工作包，並分派給對應的團隊角色。

## 專案名稱：{project_name}

## 報價單/提案內容：
{quotation_text}

## 可用團隊角色：
{roles_desc}

## 輸出要求：
1. 使用繁體中文
2. 請以 JSON 格式回覆：
{{
  "project_name": "{project_name}",
  "summary": "專案概述（1-2句話）",
  "total_items": 工作包總數,
  "work_packages": [
    {{
      "id": "WP-001",
      "name": "工作包名稱",
      "description": "具體工作說明",
      "assigned_to": "角色 id",
      "assigned_role_name": "角色名稱",
      "deliverables": ["交付物1", "交付物2"],
      "priority": "high/medium/low",
      "estimated_days": 預估工作天數,
      "dependencies": ["依賴的工作包 id，如 WP-001"],
      "notes": "備註或注意事項"
    }}
  ],
  "timeline_suggestion": "建議時程安排說明",
  "role_summary": [
    {{
      "role_id": "角色 id",
      "role_name": "角色名稱",
      "package_count": 負責的工作包數,
      "total_days": 預估總工作天數,
      "packages": ["WP-001", "WP-002"]
    }}
  ],
  "notes": "其他整體注意事項或建議"
}}

3. 工作包要具體、可執行，避免太模糊
4. 每個工作包只分配給一個主要角色（如需跨角色協作，在 notes 中說明）
5. 標明工作包之間的依賴關係
6. 優先級根據時程急迫性和重要性判斷
7. 如果報價單中的某項工作不屬於任何現有角色，請指定最接近的角色並在 notes 中說明

請直接回覆 JSON，不要加任何其他文字。"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith("```"):
        lines = response_text.split("\n")
        response_text = "\n".join(lines[1:])
        if response_text.endswith("```"):
            response_text = response_text[:-3].strip()

    return json.loads(response_text)


def generate_dispatch_docx(result: dict) -> str:
    """Generate a .docx work dispatch document."""
    from docx import Document
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    title = doc.add_heading(f"工作包分派表 - {result.get('project_name', '')}", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    doc.add_paragraph(f"專案概述：{result.get('summary', '')}")
    doc.add_paragraph(f"工作包總數：{result.get('total_items', 0)}")
    doc.add_paragraph("")

    # Role summary
    doc.add_heading("角色工作量總覽", level=1)
    for role in result.get("role_summary", []):
        doc.add_paragraph(
            f"{role['role_name']}：{role['package_count']} 個工作包，"
            f"預估 {role['total_days']} 工作天",
            style="List Bullet"
        )

    doc.add_paragraph("")

    # Work packages
    doc.add_heading("工作包明細", level=1)
    for wp in result.get("work_packages", []):
        doc.add_heading(f"{wp['id']} - {wp['name']}", level=2)
        doc.add_paragraph(f"負責角色：{wp.get('assigned_role_name', '')}")
        doc.add_paragraph(f"優先級：{wp.get('priority', '')}")
        doc.add_paragraph(f"預估天數：{wp.get('estimated_days', '')} 天")
        doc.add_paragraph(f"說明：{wp.get('description', '')}")

        if wp.get("deliverables"):
            doc.add_paragraph("交付物：")
            for d in wp["deliverables"]:
                doc.add_paragraph(d, style="List Bullet")

        if wp.get("dependencies"):
            doc.add_paragraph(f"依賴：{', '.join(wp['dependencies'])}")
        if wp.get("notes"):
            doc.add_paragraph(f"備註：{wp['notes']}")

        doc.add_paragraph("")

    # Timeline
    if result.get("timeline_suggestion"):
        doc.add_heading("建議時程", level=1)
        doc.add_paragraph(result["timeline_suggestion"])

    # Notes
    if result.get("notes"):
        doc.add_heading("整體注意事項", level=1)
        doc.add_paragraph(result["notes"])

    filename = f"work_dispatch_{uuid.uuid4().hex[:8]}.docx"
    output_path = OUTPUT_FOLDER / filename
    doc.save(str(output_path))
    return filename


@app.route("/api/work-dispatch/roles", methods=["GET"])
def get_default_roles():
    return jsonify(DEFAULT_ROLES)


@app.route("/api/work-dispatch/process", methods=["POST"])
def process_work_dispatch():
    data = request.get_json()
    if not data:
        return jsonify({"error": "缺少資料"}), 400

    if not data.get("quotation_text"):
        return jsonify({"error": "請輸入報價單或提案內容"}), 400
    if not data.get("roles"):
        return jsonify({"error": "請至少選擇一個團隊角色"}), 400

    try:
        result = process_work_dispatch_with_ai(
            data["quotation_text"],
            data["roles"],
            data.get("project_name", "未命名專案"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/work-dispatch/export", methods=["POST"])
def export_work_dispatch():
    data = request.get_json()
    if not data:
        return jsonify({"error": "缺少資料"}), 400

    try:
        filename = generate_dispatch_docx(data)
        return jsonify({"filename": filename})
    except Exception as e:
        return jsonify({"error": f"匯出失敗：{str(e)}"}), 500


@app.route("/api/ad-report/export", methods=["POST"])
def export_ad_report():
    data = request.get_json()
    if not data:
        return jsonify({"error": "缺少報告資料"}), 400

    try:
        filename = generate_ad_report_docx(data)
        return jsonify({"filename": filename})
    except Exception as e:
        return jsonify({"error": f"匯出失敗：{str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
