import os

from flask import Flask, jsonify, make_response, request
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


app = Flask(__name__)
CORS(app)
groq_client = create_groq_client()


@app.get("/")
def index():
    return jsonify(
        {
            "service": "AI Security Analyzer Groq",
            "status": "ok",
            "endpoints": ["/healthz", "/generate", "/upload-report"],
        }
    )


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


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)