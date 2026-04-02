import os
import json
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv
import anthropic

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")

UPLOAD_FOLDER = Path(__file__).parent / "uploads"
OUTPUT_FOLDER = Path(__file__).parent / "outputs"
TEMPLATE_FOLDER = Path(__file__).parent / "company_templates"

UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)

ALLOWED_TEXT_EXT = {".txt", ".md", ".doc", ".docx", ".pdf"}
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".mp4"}
ALLOWED_EXTENSIONS = ALLOWED_TEXT_EXT | ALLOWED_AUDIO_EXT

app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB


def load_template(template_type: str) -> dict:
    path = TEMPLATE_FOLDER / f"{template_type}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def transcribe_audio(file_path: str) -> str:
    """Transcribe audio file using OpenAI Whisper."""
    import whisper

    model = whisper.load_model("base")
    result = model.transcribe(file_path, language="zh")
    return result["text"]


def read_text_file(file_path: str) -> str:
    """Read content from a text file."""
    ext = Path(file_path).suffix.lower()
    if ext == ".docx":
        from docx import Document

        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs)
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
    return render_template("home.html")


@app.route("/meeting")
def meeting():
    return render_template("index.html")


@app.route("/ad-report")
def ad_report():
    return render_template("ad_report.html")


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
            text = transcribe_audio(str(file_path))
        else:
            text = read_text_file(str(file_path))
    except Exception as e:
        return jsonify({"error": f"檔案處理失敗：{str(e)}"}), 500

    return jsonify({"text": text, "filename": filename})


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
