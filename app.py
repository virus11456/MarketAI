import os
import json
import uuid
from datetime import datetime
from pathlib import Path

import requests as http_requests

from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

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


UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)

ALLOWED_TEXT_EXT = {".txt", ".md", ".doc", ".docx", ".pdf", ".csv", ".xlsx", ".xls"}
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".mp4"}
ALLOWED_EXTENSIONS = ALLOWED_TEXT_EXT | ALLOWED_AUDIO_EXT

app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB


# DeepSeek (OpenAI-compatible) — 用於把逐字稿整理成會議記錄
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")


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
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        # 報價單內容多半在表格裡，需一併讀出
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                line = " | ".join(c for c in cells if c)
                if line:
                    parts.append(line)
        return "\n".join(parts)
    elif ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    elif ext == ".csv":
        import csv

        rows = []
        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            for row in reader:
                # 用 | 分隔欄位，避免值內含千分位逗號（如 "142,478"）造成欄位錯位
                rows.append(" | ".join(cell.strip() for cell in row))
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


def _strip_code_fence(content: str) -> str:
    """Remove a leading/trailing Markdown code fence if the model wrapped its output."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:])
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3].strip()
    return content


def call_deepseek(messages: list, temperature: float = 0.3, max_tokens: int | None = None,
                  api_key: str | None = None) -> str:
    """Call the DeepSeek (OpenAI-compatible) chat completions API and return the text content."""
    key = (api_key or "").strip() or DEEPSEEK_API_KEY
    if not key:
        raise RuntimeError("尚未填入 DeepSeek API Key，請點右上角「API 設定」輸入後再試。")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    resp = http_requests.post(
        DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=300,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"DeepSeek API 錯誤（{resp.status_code}）：{resp.text[:300]}")

    return resp.json()["choices"][0]["message"]["content"].strip()


def organize_meeting_notes(transcript: str, api_key: str | None = None) -> str:
    """Use DeepSeek API to turn a raw transcript into structured meeting notes (Markdown)."""
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = f"""你是一位專業的會議記錄整理助理。以下是一段會議錄音的逐字稿，請把它整理成一份清楚、專業的「會議記錄」。

## 逐字稿內容：
{transcript}

## 輸出要求：
1. 全程使用繁體中文。
2. 直接輸出 Markdown 格式的會議記錄，不要加任何開場白或結語、不要用程式碼區塊包起來。
3. 請依照以下結構整理（若逐字稿中沒有相關資訊，該欄位可留「（未提及）」）：

# 會議記錄

**會議主題：** （依內容推斷）
**會議日期：** （逐字稿中有提到就用，否則填 {today}）
**與會人員：** （依內容推斷，逐字稿沒有就寫「未提及」）

## 會議摘要
（用 3-5 句話總結整場會議重點）

## 討論事項
（依主題分點條列，每個議題說明討論內容與結論）

## 決議事項
（條列本次會議確定的決定）

## 待辦事項 (Action Items)
（用表格呈現，欄位：項目 / 負責人 / 預計完成時間。若無負責人或時間填「待定」）

4. 內容要忠於逐字稿，不要虛構不存在的資訊；可以適度潤飾語句讓記錄更通順。
"""

    content = call_deepseek(
        [
            {"role": "system", "content": "你是一位專業、嚴謹的會議記錄整理助理。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        api_key=api_key,
    )
    return _strip_code_fence(content)


def generate_markdown_docx(markdown_text: str, title: str = "會議記錄") -> str:
    """Generate a .docx file from a Markdown string (used for meeting notes)."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    in_table = False
    for raw_line in markdown_text.split("\n"):
        line = raw_line.strip()
        if not line:
            in_table = False
            continue

        if line.startswith("# "):
            heading = doc.add_heading(line[2:].strip(), level=0)
            heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
        elif line.startswith("## "):
            doc.add_heading(line[3:].strip(), level=1)
        elif line.startswith("### "):
            doc.add_heading(line[4:].strip(), level=2)
        elif line.startswith("|") and line.endswith("|"):
            # Skip Markdown table separator rows
            if line.replace("|", "").replace("-", "").replace(":", "").replace(" ", "") == "":
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            doc.add_paragraph("  |  ".join(cells))
            in_table = True
        elif line.startswith("- ") or line.startswith("* "):
            doc.add_paragraph(line[2:].strip(), style="List Bullet")
        else:
            doc.add_paragraph(line)

    filename = f"meeting_notes_{uuid.uuid4().hex[:8]}.docx"
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


@app.route("/api/meeting/organize", methods=["POST"])
def meeting_organize():
    data = request.get_json()
    text = (data or {}).get("text", "").strip()
    if not text:
        return jsonify({"error": "缺少逐字稿內容"}), 400
    try:
        notes = organize_meeting_notes(text, api_key=(data or {}).get("api_key"))
        return jsonify({"notes": notes})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/meeting/export", methods=["POST"])
def meeting_export():
    data = request.get_json()
    notes = (data or {}).get("notes", "").strip()
    title = (data or {}).get("title", "會議記錄")
    if not notes:
        return jsonify({"error": "缺少會議記錄內容"}), 400
    try:
        filename = generate_markdown_docx(notes, title)
        return jsonify({"filename": filename})
    except Exception as e:
        return jsonify({"error": f"匯出失敗：{str(e)}"}), 500


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
            elif IS_VERCEL:
                return jsonify({
                    "error": "雲端版不支援本機語音轉文字。請先在「語音辨識設定」中填入 Colab API URL，或直接上傳文字檔。"
                }), 400
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


@app.route("/api/download/<filename>")
def download_file(filename):
    file_path = OUTPUT_FOLDER / secure_filename(filename)
    if not file_path.exists():
        return jsonify({"error": "檔案不存在"}), 404
    return send_file(str(file_path), as_attachment=True)


PLATFORM_ORDER = ["meta", "google", "line", "tiktok"]
PLATFORM_LABELS = {
    "meta": "Meta (Facebook/Instagram)",
    "google": "Google Ads",
    "line": "LINE Ads",
    "tiktok": "TikTok Ads",
}


def process_ad_report_with_ai(report_info: dict, api_key: str | None = None) -> dict:
    """Use DeepSeek to generate an ad monthly report from platform data (incl. CSV exports)."""
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

    # Build per-platform section spec (each platform = one section with 成效 + 素材分析).
    # Sections are numbered: 1 本月總覽, 2..(N+1) 各平台, then 本月洞察, 未來建議調整.
    platform_spec_lines = []
    for idx, p in enumerate(all_platforms, start=2):
        label = PLATFORM_LABELS.get(p, p)
        platform_spec_lines.append(
            f"{idx}. **{label} 成效與素材分析** - 此平台（id: {p}）的成效分析與素材分析（見下方內容要求）。"
        )
    platform_section_spec = "\n".join(platform_spec_lines)
    n_insight = len(all_platforms) + 2
    n_future = len(all_platforms) + 3

    prompt = f"""你是一位資深數位廣告顧問。請根據以下廣告後台數據，產出一份完整的廣告月報。

## 基本資訊
- 客戶名稱：{client_name}
- 公司名稱：{company_name}
- 報告月份：{report_month}

## 各平台廣告數據：
{platforms_text}

## 數據格式說明（資料可能直接來自各平台後台匯出的 CSV，欄位以「 | 」分隔）：
- **Google Ads**：開頭可能有「廣告活動報表」「日期區間」等抬頭列；含「總計：…」彙總列（總計列可作為平台整體數據）。常見欄位對應：費用＝花費、曝光、互動/點擊、點閱率＝CTR、轉換、單次轉換費用＝CPA、轉換價值、「轉換價值/費用」＝ROAS、貨幣代碼為 TWD。請以非總計的各「廣告活動」列做活動別表格。
- **Meta（Facebook / Instagram）**：每列為一個「廣告組合」。常見欄位對應：「花費金額 (TWD)」＝花費、曝光次數、觸及人數、連結點擊次數、成果（其意義依「成果指標」欄而定，例如 messaging_conversation_started 為訊息對話數）、每次成果成本＝CPA、廣告組合名稱常含受眾資訊（如「年齡 : 45y-65y+」「興趣受眾 : 個人護理」可作為受眾洞察）。
- 指標若原始數據沒有，請以公式計算：CTR＝點擊/曝光、CPC＝花費/點擊、CPM＝花費/曝光×1000、CPA＝花費/成果、ROAS＝轉換價值/花費。計算後請標示為「(推算)」。
- 金額數字可能含千分位逗號（如 142,478），請正確解讀為數值。

## 月報架構（請「嚴格」按照以下順序產出 sections，不可增減段落、不可改變順序）：

1. **本月總覽** - 彙總本月所有平台的整體數據（總花費、總曝光、總點擊、總轉換、整體 ROAS 等），用表格呈現各平台對比與加總，並用 2-4 句話總結本月整體表現。
{platform_section_spec}
{n_insight}. **本月洞察** - 跨平台的綜合洞察：哪個平台/受眾/素材方向表現最好與最差、值得注意的趨勢與問題，條列具體發現（每點都要有數據佐證）。
{n_future}. **未來建議調整** - 根據本月數據提出下月的具體調整方向：預算分配建議、受眾調整、素材優化方向、各平台操作建議，條列可執行的行動。

## 各平台成效段落（上述第 2 段起，每個平台一段）內容要求：
每個平台段落都要包含以下兩個子標題（用 Markdown 的 `###`）：
- **### 成效分析**：花費與成效總覽、各廣告活動/廣告組合表現（用 Markdown 表格）、關鍵指標分析（CPM、CPC、CTR、CPA、ROAS 等，沒有原始數據就用公式推算並標「(推算)」）。
- **### 素材分析**：分析該平台各素材/廣告組合/創意方向的表現差異（點擊率、互動率、成果成本等），指出表現好與差的素材並說明可能原因。

## 輸出要求：
1. 使用繁體中文。
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
      "id": "段落識別碼（overview / 平台id（如 meta、google）/ insights / future）",
      "title": "段落標題",
      "content": "段落內容（支援 Markdown 格式，表格請用 Markdown 表格）"
    }}
  ],
  "closing": {{
    "title": "感謝頁標題",
    "content": "感謝語內容"
  }}
}}
3. 數據要精確引用，不要虛構數字。
4. 表格要清楚呈現各項指標。
5. 洞察與建議要具體、可執行。
6. 如果數據不足，請標註「[待補充]」。

請直接回覆 JSON，不要加任何其他文字。"""

    content = call_deepseek(
        [
            {"role": "system", "content": "你是一位資深數位廣告顧問，只會回覆合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=8192,
        api_key=api_key,
    )
    return json.loads(_strip_code_fence(content))


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
        result = process_ad_report_with_ai(data, api_key=data.get("api_key"))
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


def process_work_dispatch_with_ai(quotation_text: str, team_roles: list, project_name: str,
                                  api_key: str | None = None) -> dict:
    """Use DeepSeek to break a quotation into work packages assigned to team roles."""
    roles_desc = "\n".join(
        f"- **{r['name']}** ({r['id']}): {r['desc']}" for r in team_roles
    )

    prompt = f"""你是一位資深專案經理。以下是一份「報價單」內容（可能由 PDF / Word 轉成的純文字，表格會以「欄1 | 欄2 | …」的方式呈現）。請把報價單裡的服務項目拆分成具體、可執行的工作包，並分派給對應的團隊角色。

## 專案名稱：{project_name}

## 報價單內容：
{quotation_text}

## 可用團隊角色：
{roles_desc}

## 拆分原則：
- 報價單的「服務費用明細」通常包含「行銷模組 / 工作項目 / 單價 / 數量 / 期間 / 小計」等欄位。請以每個「行銷模組」底下的「工作項目」為基礎拆成工作包，必要時把一個大模組再拆成數個工作包。
- 盡量保留並標註該項目對應的金額（單價或小計），方便對照報價單。
- 每個工作包指派給最適合的「一個」主要角色；若需跨角色協作，在該工作包 notes 說明。
- 若某項工作不屬於任何現有角色，指定最接近的角色並在 notes 說明。
- 忽略匯款資訊、簽署欄、報價有效期等與執行無關的內容。

## 輸出要求：
1. 全程使用繁體中文。
2. 直接回覆 JSON（不要加任何其他文字、不要用程式碼區塊包起來），格式如下：
{{
  "project_name": "{project_name}",
  "summary": "專案概述（1-2句話）",
  "total_items": 工作包總數,
  "work_packages": [
    {{
      "id": "WP-001",
      "name": "工作包名稱",
      "module": "對應的報價單行銷模組（若有）",
      "amount": "對應金額（如 $18,000；無法對應填空字串）",
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

3. 工作包要具體、可執行，避免太模糊。
4. 標明工作包之間的依賴關係，優先級依時程急迫性與重要性判斷。"""

    content = call_deepseek(
        [
            {"role": "system", "content": "你是一位資深、嚴謹的專案經理，只會回覆合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=8192,
        api_key=api_key,
    )
    return json.loads(_strip_code_fence(content))


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
        if wp.get("module"):
            doc.add_paragraph(f"報價模組：{wp['module']}")
        if wp.get("amount"):
            doc.add_paragraph(f"對應金額：{wp['amount']}")
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
            api_key=data.get("api_key"),
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
