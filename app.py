import os
from pathlib import Path

from flask import Flask, jsonify, make_response, render_template_string, request
from flask_cors import CORS
from groq import Groq

try:
    import psycopg2
except Exception:
    psycopg2 = None


def create_groq_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return Groq(api_key=api_key)


def get_db_connection():
    database_url = os.getenv("DATABASE_URL")
    if not database_url or psycopg2 is None:
        return None
    try:
        return psycopg2.connect(database_url)
    except Exception:
        return None


def analyze_report(report_data, groq_client):
    if not groq_client:
        return "Error: GROQ_API_KEY is not configured."

    prompt = f"Hãy phân tích báo cáo quét bảo mật sau đây bằng tiếng Việt: {report_data}"
    try:
        completion = groq_client.chat.completions.create(
            model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=1024,
        )
        ai_summary = completion.choices[0].message.content
    except Exception as exc:
        ai_summary = f"Groq Analysis failed: {exc}"

    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO reports (report_data, summary) VALUES (%s, %s)",
                    (str(report_data), ai_summary),
                )
                conn.commit()
        except Exception:
            conn.rollback()
        finally:
            conn.close()

    return ai_summary


def extract_text_from_upload(uploaded_file):
    file_name = (uploaded_file.filename or "").lower()
    raw_bytes = uploaded_file.read()
    if not raw_bytes:
        return ""

    text_extensions = {".txt", ".log", ".json", ".csv", ".xml", ".md", ".html", ".htm", ".py", ".js", ".ts", ".yaml", ".yml"}
    extension = Path(file_name).suffix

    if extension in text_extensions:
        return raw_bytes.decode("utf-8", errors="ignore")

    return raw_bytes.decode("utf-8", errors="ignore")


app = Flask(__name__)
CORS(app)
groq_client = create_groq_client()

HOME_PAGE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Security Analyzer Groq</title>
    <style>
        :root {
            color-scheme: dark;
            --bg: #0b1020;
            --panel: rgba(16, 24, 40, 0.88);
            --panel-border: rgba(148, 163, 184, 0.18);
            --text: #e5eefc;
            --muted: #94a3b8;
            --accent: #22c55e;
            --accent-2: #60a5fa;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            min-height: 100vh;
            font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at top left, rgba(34, 197, 94, 0.18), transparent 28%),
                radial-gradient(circle at top right, rgba(96, 165, 250, 0.18), transparent 30%),
                linear-gradient(180deg, #090d18 0%, var(--bg) 100%);
        }
        .wrap {
            width: min(960px, calc(100% - 32px));
            margin: 0 auto;
            padding: 48px 0 56px;
        }
        .hero {
            display: grid;
            gap: 14px;
            margin-bottom: 24px;
        }
        .eyebrow {
            color: var(--accent);
            text-transform: uppercase;
            letter-spacing: 0.18em;
            font-size: 12px;
            font-weight: 700;
        }
        h1 {
            margin: 0;
            font-size: clamp(2rem, 4vw, 3.6rem);
            line-height: 1.05;
        }
        p {
            margin: 0;
            color: var(--muted);
            max-width: 68ch;
            line-height: 1.6;
        }
        .card {
            background: var(--panel);
            border: 1px solid var(--panel-border);
            border-radius: 20px;
            padding: 20px;
            backdrop-filter: blur(14px);
            box-shadow: 0 28px 80px rgba(0, 0, 0, 0.32);
        }
        label {
            display: block;
            margin: 0 0 10px;
            font-weight: 600;
        }
        textarea {
            width: 100%;
            min-height: 210px;
            resize: vertical;
            border-radius: 16px;
            border: 1px solid rgba(148, 163, 184, 0.24);
            background: rgba(2, 6, 23, 0.75);
            color: var(--text);
            padding: 16px;
            font: inherit;
            outline: none;
        }
        textarea:focus {
            border-color: rgba(96, 165, 250, 0.9);
            box-shadow: 0 0 0 4px rgba(96, 165, 250, 0.14);
        }
        .actions {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            margin-top: 14px;
        }
        button {
            border: 0;
            border-radius: 999px;
            padding: 12px 18px;
            font: inherit;
            font-weight: 700;
            cursor: pointer;
            transition: transform 0.15s ease, opacity 0.15s ease;
        }
        button:hover { transform: translateY(-1px); }
        .primary { background: linear-gradient(135deg, var(--accent), var(--accent-2)); color: #08111f; }
        .secondary { background: rgba(148, 163, 184, 0.14); color: var(--text); }
        .result {
            margin-top: 18px;
            white-space: pre-wrap;
            line-height: 1.6;
            color: #dbeafe;
            background: rgba(2, 6, 23, 0.65);
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 16px;
            padding: 16px;
            min-height: 84px;
        }
        .file-row {
            margin-top: 16px;
            display: grid;
            gap: 10px;
        }
        input[type="file"] {
            width: 100%;
            border: 1px dashed rgba(148, 163, 184, 0.34);
            border-radius: 14px;
            padding: 14px;
            background: rgba(2, 6, 23, 0.45);
            color: var(--muted);
        }
        .hint {
            color: var(--muted);
            font-size: 13px;
        }
        .meta {
            margin-top: 12px;
            color: var(--muted);
            font-size: 14px;
        }
    </style>
</head>
<body>
    <main class="wrap">
        <section class="hero">
            <div class="eyebrow">AI Security Analyzer Groq</div>
            <h1>Phân tích báo cáo bảo mật ngay trên web.</h1>
            <p>Nhập prompt hoặc nội dung báo cáo, gửi đến Groq, và xem kết quả trực tiếp trên Render. Endpoint API vẫn dùng được tại <code>/generate</code>.</p>
        </section>

        <section class="card">
            <label for="input">Nội dung cần phân tích</label>
            <textarea id="input" placeholder="Dán báo cáo bảo mật hoặc mô tả rủi ro tại đây..."></textarea>
            <div class="file-row">
                <label for="file">Hoặc tải file lên để phân tích</label>
                <input id="file" type="file" />
                <div class="hint">Hỗ trợ tốt nhất cho file văn bản như .txt, .log, .json, .csv, .xml, .md, .py, .js.</div>
            </div>
            <div class="actions">
                <button class="primary" onclick="analyze()">Phân tích</button>
                <button class="secondary" onclick="fillExample()">Dùng ví dụ</button>
            </div>
            <div id="result" class="result">Kết quả sẽ hiển thị ở đây.</div>
            <div class="meta">Nếu Groq API key chưa cấu hình, trang vẫn mở được nhưng phần phân tích sẽ trả thông báo lỗi cấu hình.</div>
        </section>
    </main>

    <script>
        const input = document.getElementById('input');
        const fileInput = document.getElementById('file');
        const result = document.getElementById('result');

        function fillExample() {
            input.value = 'Hãy phân tích rủi ro bảo mật của mật khẩu yếu, thiếu MFA và lỗi phân quyền trong ứng dụng web.';
        }

        async function analyze() {
            const file = fileInput.files && fileInput.files[0];
            const value = input.value.trim();

            if (file) {
                result.textContent = 'Đang tải và phân tích file...';
                const formData = new FormData();
                formData.append('file', file);

                try {
                    const response = await fetch('/analyze-file', {
                        method: 'POST',
                        body: formData
                    });
                    const data = await response.json();
                    result.textContent = data.summary || data.error || 'Không nhận được kết quả.';
                } catch (error) {
                    result.textContent = 'Không thể upload file: ' + error.message;
                }
                return;
            }

            if (!value) {
                result.textContent = 'Vui lòng nhập nội dung trước khi phân tích.';
                return;
            }

            result.textContent = 'Đang phân tích...';
            try {
                const response = await fetch('/generate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ prompt: value })
                });
                const data = await response.json();
                result.textContent = data.summary || data.error || 'Không nhận được kết quả.';
            } catch (error) {
                result.textContent = 'Không thể gọi API: ' + error.message;
            }
        }
    </script>
</body>
</html>
"""


@app.get("/")
def index():
        return render_template_string(HOME_PAGE)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.route("/generate", methods=["POST", "OPTIONS"])
@app.route("/upload-report", methods=["POST", "OPTIONS"])
def handle_request():
    if request.method == "OPTIONS":
        return make_response(("", 204))

    data = request.get_json(silent=True) or {}
    content = data.get("prompt") or data.get("text")
    if not content:
        return jsonify({"error": "No data provided"}), 400

    summary = analyze_report(content, groq_client)
    return jsonify({"status": "success", "summary": summary})


@app.route("/analyze-file", methods=["POST", "OPTIONS"])
def analyze_file():
    if request.method == "OPTIONS":
        return make_response(("", 204))

    uploaded_file = request.files.get("file")
    if not uploaded_file:
        return jsonify({"error": "No file provided"}), 400

    file_text = extract_text_from_upload(uploaded_file).strip()
    if not file_text:
        return jsonify({"error": "File is empty or could not be read"}), 400

    file_name = uploaded_file.filename or "uploaded file"
    prompt = f"Hãy phân tích file bảo mật sau đây bằng tiếng Việt. Tên file: {file_name}. Nội dung:\n{file_text}"
    summary = analyze_report(prompt, groq_client)
    return jsonify({"status": "success", "summary": summary, "filename": file_name})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)