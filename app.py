# Harbor & Vale — King County Survival Guide (Demo)
# Foundation build: HTTPS redirect, health checks, file-backed services with filters/synonyms,
# and robust AI triage (crisis handling + categories).

from flask import Flask, request, jsonify, render_template, redirect
from werkzeug.middleware.proxy_fix import ProxyFix
import os, json

app = Flask(__name__)
# Honor Render's proxy headers so HTTPS detection works
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# -------- HTTPS redirect (safe for Render; skip for /health & when debugging) --------
@app.before_request
def enforce_https():
    if app.debug or request.path == "/health":
        return
    if request.headers.get("X-Forwarded-Proto", "http") != "https":
        code = 301 if request.method in ("GET", "HEAD") else 307
        return redirect(request.url.replace("http://", "https://", 1), code=code)

# --------------------------------- Health & errors -----------------------------------
@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "server error"}), 500


# ----------------------------------- Tier 1 ------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/submit_form", methods=["POST"])
def submit_form():
    data = request.form.to_dict(flat=True)
    print("Form submitted:", data)
    return jsonify({"status": "success"})


# ----------------------------------- Tier 2 ------------------------------------------
# File-backed services with simple filters + synonym expansion (no DB yet)
SYNONYMS = {
    "id": ["id", "ids", "identification", "license", "dmv", "birth certificate"],
    "showers": ["shower", "showers", "hygiene", "laundry"],
    "food": ["food", "meal", "meals", "groceries", "food bank", "soup"],
    "transport": ["transport", "transportation", "bus", "orca", "transit", "ticket", "pass"],
    "detox": ["detox", "withdrawal", "sobering", "sobering center"],
    "mental": ["mental", "counseling", "therapy", "psychiatry", "behavioral"]
}

def load_services():
    path = os.path.join(os.path.dirname(__file__), "services.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Safe fallback demo data
        return [
            {"name": "Lake Union Women's Shelter", "type": "Shelter", "neighborhood": "Downtown", "beds": 3,
             "hours": "Intake 4–8pm", "walk_in": True, "phone": "(206) 555-1212", "address": "123 Pine St, Seattle, WA",
             "website": "https://example.org/shelter", "notes": "ID preferred; LGBTQ+ inclusive",
             "services": ["shelter", "beds"]},
            {"name": "Harbor Free Clinic", "type": "Clinic", "neighborhood": "Capitol Hill",
             "hours": "Walk-ins Wed/Fri 1–5pm", "walk_in": True, "phone": "(206) 555-4545",
             "address": "500 Broadway E, Seattle, WA", "website": "https://example.org/clinic",
             "notes": "MAT referrals; naloxone on site", "services": ["medical", "clinic", "mat", "naloxone"]}
        ]

def expand_query_terms(q: str):
    """Return a set of lowercase terms to match, expanding basic synonyms."""
    q = (q or "").strip().lower()
    if not q:
        return set()
    terms = set([q])
    for key, syns in SYNONYMS.items():
        if key in q or any(s in q for s in syns):
            terms.update(syns)
    return terms

# ---------- Tier 2: Services (file-backed with simple filters + synonym expansion) ----------
def fetch_services(q=None, kind=None, neighborhood=None, walk_in_only=None):
    items = load_services()
    qterms = expand_query_terms(q)

    def matches(item):
        ok = True

        if kind:
            ok = ok and item.get("type", "").lower() == kind.lower()

        if neighborhood:
            ok = ok and item.get("neighborhood", "").lower() == neighborhood.lower()

        if walk_in_only is True:
            ok = ok and bool(item.get("walk_in", False))

        if qterms:
            # Build a searchable string from several fields + services list
            fields = [
                item.get("name", ""),
                item.get("notes", ""),
                item.get("type", ""),
                item.get("neighborhood", "")
            ]
            services = item.get("services", [])
            fields.extend([str(s) for s in services])

            hay = " ".join(fields).lower()
            if not any(term in hay for term in qterms):
                return False

        return ok

    return [x for x in items if matches(x)]

@app.route("/services")
def services():
    q = request.args.get("q")
    kind = request.args.get("type")
    hood = request.args.get("neighborhood")
    walk = request.args.get("walk_in")
    walk_only = True if (walk and walk.lower() in ("1", "true", "yes")) else None
    return jsonify(fetch_services(q, kind, hood, walk_only))
