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
def index():
    return render_template("index.html")


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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
