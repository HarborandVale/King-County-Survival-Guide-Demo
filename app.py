# Harbor & Vale — King County Survival Guide (Demo)
# Minimal Flask app with health check, basic services, and simple AI triage.

from flask import Flask, request, jsonify, render_template, redirect
from werkzeug.middleware.proxy_fix import ProxyFix
import os, json

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
@app.before_request
def enforce_https():
    # Skip in local/debug and for health checks
    if app.debug or request.path == "/health":
        return

    # If Render's proxy says the request isn't HTTPS, redirect to HTTPS
    if request.headers.get("X-Forwarded-Proto", "http") != "https":
        code = 301 if request.method in ("GET", "HEAD") else 307
        return redirect(request.url.replace("http://", "https://", 1), code=code)

# ---------- Health & Error Handling ----------
@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "server error"}), 500


# ---------- Tier 1: Landing + Intake ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/submit_form", methods=["POST"])
def submit_form():
    data = request.form.to_dict(flat=True)
    # In a real app you'd persist this somewhere; here we just confirm receipt.
    print("Form submitted:", data)
    return jsonify({"status": "success"})


# ---------- Tier 2: Services (file-backed with simple filters + safe fallback) ----------
def fetch_services_from_file(q=None, kind=None, neighborhood=None):
    p = os.path.join(os.path.dirname(__file__), "services.json")

    # Try to load from services.json; fall back to demo data if the file doesn't exist yet.
    try:
        with open(p, "r", encoding="utf-8") as f:
            items = json.load(f)
    except FileNotFoundError:
        items = [
            {"name": "Shelter A", "type": "Shelter", "neighborhood": "Downtown", "beds": 2, "status": "Available"},
            {"name": "Clinic B", "type": "Clinic", "neighborhood": "Capitol Hill", "status": "Walk-ins Only"},
        ]

    # Apply simple filters
    def match(x):
        ok = True
        if q:
            ql = q.lower()
            ok = ok and (ql in x.get("name","").lower() or ql in x.get("notes","").lower())
        if kind:
            ok = ok and (x.get("type","").lower() == kind.lower())
        if neighborhood:
            ok = ok and (x.get("neighborhood","").lower() == neighborhood.lower())
        return ok

    return [x for x in items if match(x)]

@app.route("/services")
def services():
    q = request.args.get("q")
    kind = request.args.get("type")
    hood = request.args.get("neighborhood")
    return jsonify(fetch_services_from_file(q, kind, hood))



# ---------- Tier 3: AI Triage (simple keyword logic) ----------
def ai_triage(user_input: str):
    text = (user_input or "").lower().strip()

    # Medical first (catch clinic/doctor/health needs)
    if any(k in text for k in (
        "medical", "doctor", "clinic", "nurse", "health", "sick",
        "injury", "hurt", "wound", "od", "overdose"
    )):
        return {"recommendation": "Visit Clinic B"}

    # Housing & shelter needs
    if any(k in text for k in (
        "housing", "shelter", "bed", "room", "sleep", "unhoused", "tent"
    )):
        return {"recommendation": "Apply to Shelter A"}

    # Fallback
    return {"recommendation": "Call 211"}

@app.route("/ai_triage", methods=["POST"])
def triage():
    payload = request.get_json(silent=True) or {}
    user_input = payload.get("message", "")
    result = ai_triage(user_input)
    # Echo input back for clarity while testing.
    return jsonify({"input": user_input, **result})


# ---------- Local Test Cases (run only when launched locally) ----------
def test_ai_triage():
    assert ai_triage("I need housing") == {"recommendation": "Apply to Shelter A"}
    assert ai_triage("I have a medical emergency") == {"recommendation": "Visit Clinic B"}
    assert ai_triage("Something else") == {"recommendation": "Call 211"}

if __name__ == "__main__":
    # Run sanity tests only for local dev, not on Render/gunicorn import.
    test_ai_triage()
    # Local server for quick checks (Render will use gunicorn start command)
    app.run(host="0.0.0.0", port=5000, debug=False)





