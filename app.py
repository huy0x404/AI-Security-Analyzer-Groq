import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from flask import Flask, jsonify, make_response, render_template_string, request
from flask_cors import CORS
from groq import Groq
import traceback


try:
    import psycopg2
except Exception:
    psycopg2 = None


LAST_DB_ERROR = None


def create_groq_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    return Groq(api_key=api_key)


def with_sslmode_require(database_url):
    parsed = urlparse(database_url)
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    keys = {k.lower() for k, _ in query_pairs}
    if "sslmode" in keys:
        return database_url
    query_pairs.append(("sslmode", "require"))
    return urlunparse(parsed._replace(query=urlencode(query_pairs)))


def get_db_connection():
    global LAST_DB_ERROR
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        LAST_DB_ERROR = "DATABASE_URL is not set"
        return None
    if psycopg2 is None:
        LAST_DB_ERROR = "psycopg2 is not installed/importable"
        return None

    try:
        conn = psycopg2.connect(database_url)
        LAST_DB_ERROR = None
        return conn
    except Exception as first_error:
        # Neon often requires sslmode=require; retry once if URL has no sslmode.
        try:
            fallback_url = with_sslmode_require(database_url)
            if fallback_url != database_url:
                conn = psycopg2.connect(fallback_url)
                LAST_DB_ERROR = None
                return conn
        except Exception:
            pass

        print("DB connection failed:", first_error)
        traceback.print_exc()
        LAST_DB_ERROR = str(first_error)
        return None


def ensure_reports_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                report_data TEXT NOT NULL,
                summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.commit()


def save_report(report_data, summary, source_type="text", filename=None):
    conn = get_db_connection()
    if not conn:
        return None

    try:
        ensure_reports_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reports (report_data, summary)
                VALUES (%s, %s)
                RETURNING id;
                """,
                (str(report_data), summary),
            )
            report_id = cur.fetchone()[0]
            conn.commit()
            return report_id
    except Exception:
        conn.rollback()
        print("Error saving report:")
        traceback.print_exc()
        return None
    finally:
        conn.close()


def get_recent_reports(limit=5):
    conn = get_db_connection()
    if not conn:
        return None

    try:
        ensure_reports_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, created_at
                FROM reports
                ORDER BY id DESC
                LIMIT %s;
                """,
                (limit,),
            )
            rows = cur.fetchall()
            return [
                {
                    "id": row[0],
                    "created_at": row[1].isoformat() if row[1] else None,
                }
                for row in rows
            ]
    except Exception:
        print("Error fetching recent reports:")
        traceback.print_exc()
        return None
    finally:
        conn.close()


def get_report_by_id(report_id):
    conn = get_db_connection()
    if not conn:
        return None

    try:
        ensure_reports_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, report_data, summary, created_at
                FROM reports
                WHERE id = %s;
                """,
                (report_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "report_data": row[1],
                "summary": row[2],
                "created_at": row[3].isoformat() if row[3] else None,
            }
    except Exception:
        print(f"Error fetching report {report_id}:")
        traceback.print_exc()
        return None
    finally:
        conn.close()


def analyze_report(report_data, groq_client, source_type="text", filename=None):
    # If Groq client is not configured, we still save the input and a fallback summary
    prompt = f"Hãy phân tích báo cáo quét bảo mật sau đây bằng tiếng Việt: {report_data}"
    ai_summary = None

    if groq_client:
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
            print("Groq failure:\n", exc)
            traceback.print_exc()
    else:
        ai_summary = "Groq not configured: saved raw report as summary."

    report_id = None
    try:
        report_id = save_report(report_data, ai_summary, source_type=source_type, filename=filename)
        if report_id is None:
            print("Warning: save_report returned None (report not saved).")
    except Exception as e:
        print("Exception while saving report:", e)
        traceback.print_exc()

    return ai_summary, report_id


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
        .layout {
            display: grid;
            grid-template-columns: 1.4fr 0.8fr;
            gap: 18px;
            align-items: start;
        }
        .history-card {
            background: rgba(2, 6, 23, 0.5);
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 16px;
            padding: 14px;
        }
        .history-title {
            margin: 0 0 10px;
            font-weight: 700;
            color: #dbeafe;
        }
        .history-list {
            display: grid;
            gap: 8px;
        }
        .history-item {
            border: 1px solid rgba(148, 163, 184, 0.18);
            background: rgba(2, 6, 23, 0.6);
            border-radius: 12px;
            padding: 10px;
            cursor: pointer;
            text-align: left;
            color: var(--text);
        }
        .history-item small {
            display: block;
            color: var(--muted);
            margin-top: 4px;
        }
        .history-empty {
            color: var(--muted);
            font-size: 14px;
        }
        @media (max-width: 900px) {
            .layout {
                grid-template-columns: 1fr;
            }
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

        <section class="layout">
            <div class="card">
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
                <div id="reportMeta" class="meta">Nếu Groq API key chưa cấu hình, trang vẫn mở được nhưng phần phân tích sẽ trả thông báo lỗi cấu hình.</div>
            </div>

            <aside class="history-card">
                <h3 class="history-title">Lịch sử 5 report mới nhất</h3>
                <div id="historyList" class="history-list"></div>
                <div id="historyEmpty" class="history-empty" style="display:none;">Chưa có report nào trong database.</div>
            </aside>
        </section>
    </main>

    <script>
        const input = document.getElementById('input');
        const fileInput = document.getElementById('file');
        const result = document.getElementById('result');
        const historyList = document.getElementById('historyList');
        const historyEmpty = document.getElementById('historyEmpty');
        const reportMeta = document.getElementById('reportMeta');

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
                    if (data.report_id) {
                        reportMeta.textContent = 'Đã lưu report ID: #' + data.report_id;
                    } else if (data.db_saved === false) {
                        reportMeta.textContent = 'Phân tích xong nhưng chưa lưu DB: ' + (data.db_error || 'Lỗi không xác định');
                    }
                    await refreshHistory();
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
                if (data.report_id) {
                    reportMeta.textContent = 'Đã lưu report ID: #' + data.report_id;
                } else if (data.db_saved === false) {
                    reportMeta.textContent = 'Phân tích xong nhưng chưa lưu DB: ' + (data.db_error || 'Lỗi không xác định');
                }
                await refreshHistory();
            } catch (error) {
                result.textContent = 'Không thể gọi API: ' + error.message;
            }
        }

        async function refreshHistory() {
            historyList.innerHTML = '';
            historyEmpty.style.display = 'none';
            historyEmpty.textContent = 'Chưa có report nào trong database.';

            try {
                const response = await fetch('/reports/recent?limit=5');
                if (!response.ok) {
                    const errData = await response.json().catch(() => ({}));
                    throw new Error(errData.error || 'Không truy cập được database');
                }
                const data = await response.json();
                const items = data.items || [];

                if (!items.length) {
                    historyEmpty.style.display = 'block';
                    return;
                }

                for (const item of items) {
                    const btn = document.createElement('button');
                    btn.className = 'history-item';
                    const createdAt = item.created_at ? new Date(item.created_at).toLocaleString('vi-VN') : 'Không rõ thời gian';
                    btn.innerHTML = 'Report #' + item.id + '<small>' + createdAt + '</small>';
                    btn.onclick = () => loadReport(item.id);
                    historyList.appendChild(btn);
                }
            } catch (error) {
                historyEmpty.style.display = 'block';
                historyEmpty.textContent = 'Không tải được lịch sử report: ' + error.message;
            }
        }

        async function loadReport(reportId) {
            result.textContent = 'Đang tải report #' + reportId + '...';
            try {
                const response = await fetch('/reports/' + reportId);
                const data = await response.json();
                result.textContent = data.summary || data.error || 'Không có dữ liệu.';
                if (data.id) {
                    reportMeta.textContent = 'Đang xem report ID: #' + data.id;
                }
            } catch (error) {
                result.textContent = 'Không tải được report: ' + error.message;
            }
        }

        refreshHistory();
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


@app.get("/db-health")
def db_health():
    """Attempt a single DB connection and report status. Useful for debugging DATABASE_URL connectivity."""
    conn = None
    try:
        database_url = os.getenv("DATABASE_URL")
        diagnostics = {
            "database_url_present": bool(database_url),
            "psycopg2_available": psycopg2 is not None,
        }
        conn = get_db_connection()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT version();")
                    version = cur.fetchone()
            except Exception:
                version = None
            finally:
                conn.close()
            return jsonify({"db": "connected", "version": version, "diagnostics": diagnostics}), 200
        else:
            return jsonify(
                {
                    "db": "unavailable",
                    "error": "Could not establish connection",
                    "details": LAST_DB_ERROR,
                    "diagnostics": diagnostics,
                }
            ), 503
    except Exception as e:
        return jsonify({"db": "error", "error": str(e)}), 500


@app.route("/generate", methods=["POST", "OPTIONS"])
@app.route("/upload-report", methods=["POST", "OPTIONS"])
def handle_request():
    if request.method == "OPTIONS":
        return make_response(("", 204))

    data = request.get_json(silent=True) or {}
    content = data.get("prompt") or data.get("text")
    if not content:
        return jsonify({"error": "No data provided"}), 400

    summary, report_id = analyze_report(content, groq_client, source_type="text")
    db_saved = report_id is not None
    return jsonify(
        {
            "status": "success" if db_saved else "partial",
            "summary": summary,
            "report_id": report_id,
            "db_saved": db_saved,
            "db_error": None if db_saved else "Could not save report to database",
        }
    )


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
    summary, report_id = analyze_report(prompt, groq_client, source_type="file", filename=file_name)
    db_saved = report_id is not None
    return jsonify(
        {
            "status": "success" if db_saved else "partial",
            "summary": summary,
            "filename": file_name,
            "report_id": report_id,
            "db_saved": db_saved,
            "db_error": None if db_saved else "Could not save report to database",
        }
    )


@app.get("/reports/recent")
def reports_recent():
    limit = request.args.get("limit", default=5, type=int)
    if limit < 1:
        limit = 1
    if limit > 20:
        limit = 20
    items = get_recent_reports(limit=limit)
    if items is None:
        return jsonify({"status": "error", "error": "Database unavailable or query failed", "details": LAST_DB_ERROR}), 503
    return jsonify({"status": "success", "items": items})


@app.get("/reports/<int:report_id>")
def report_detail(report_id):
    report = get_report_by_id(report_id)
    if not report:
        return jsonify({"error": "Report not found"}), 404
    return jsonify(report)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)