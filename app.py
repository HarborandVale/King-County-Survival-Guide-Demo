# Harbor & Vale — King County Survival Guide (Demo)
# Tier 2 v1 (dashboard/intakes) + Tier 3 v1 (event logging/analytics) + cultural & safety guardrails.

from flask import Flask, request, jsonify, render_template, redirect, url_for, session, abort
from werkzeug.middleware.proxy_fix import ProxyFix
import os, json, time, threading
from collections import deque
from datetime import datetime

app = Flask(__name__)
# HTTPS trust for Render proxy
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# --- Security/session config (demo-safe defaults if env not set) ---
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-not-for-prod")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,  # Render uses HTTPS
)

DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "letmein")  # change in Render

# --- Simple in-memory stores (demo only; resets on redeploy) ---
EVENTS = deque(maxlen=2000)   # [{ts, ip, type, name, meta}]
INTAKES = deque(maxlen=500)   # [{id, ts, name, need, details, status}]
_next_intake_id = {"v": 1}    # poor-man's counter

# --- Simple IP rate limit buckets: scope -> ip -> timestamps deque ---
RATE = {}
RATE_LOCK = threading.Lock()

def check_rate(scope: str, ip: str, max_hits: int, window_sec: int) -> bool:
    now = time.time()
    with RATE_LOCK:
        bucket = RATE.setdefault(scope, {}).setdefault(ip, deque())
        # drop old timestamps
        while bucket and now - bucket[0] > window_sec:
            bucket.popleft()
        if len(bucket) >= max_hits:
            return False
        bucket.append(now)
        return True

# -------- HTTPS redirect (skip for /health & when debugging) ----------
@app.before_request
def enforce_https():
    if app.debug or request.path == "/health":
        return
    if request.headers.get("X-Forwarded-Proto", "http") != "https":
        code = 301 if request.method in ("GET", "HEAD") else 307
        return redirect(request.url.replace("http://", "https://", 1), code=code)

# ------------------------------- Health & robots -------------------------------
@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.route("/robots.txt")
def robots():
    # keep demos out of search indexes
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "server error"}), 500


# ----------------------------------- Tier 1 -----------------------------------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/submit_form", methods=["POST"])
def submit_form():
    data = request.form.to_dict(flat=True)
    name = (data.get("name") or "").strip()[:120]
    need = (data.get("need") or "").strip()[:240]
    details = (data.get("details") or "").strip()[:800]

    # Store minimal intake (no PHI; user controls what they share)
    iid = _next_intake_id["v"]; _next_intake_id["v"] += 1
    INTAKES.appendleft({
        "id": iid,
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "name": name,
        "need": need,
        "details": details,
        "status": "new"
    })
    log_event("intake_submitted", name or "anonymous", {"need": need})
    return jsonify({"status": "success", "id": iid})


# ----------------------------------- Tier 2 -----------------------------------
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
        return [
            {"name": "Lake Union Women's Shelter", "type": "Shelter", "neighborhood": "Downtown", "beds": 3,
             "hours": "Intake 4–8pm", "walk_in": True, "phone": "(206) 555-1212", "address": "123 Pine St, Seattle, WA",
             "website": "https://example.org/shelter", "notes": "ID preferred; LGBTQ+ inclusive",
             "services": ["shelter", "beds", "night intake"]},
            {"name": "Harbor Free Clinic", "type": "Clinic", "neighborhood": "Capitol Hill",
             "hours": "Walk-ins Wed/Fri 1–5pm", "walk_in": True, "phone": "(206) 555-4545",
             "address": "500 Broadway E, Seattle, WA", "website": "https://example.org/clinic",
             "notes": "MAT referrals; naloxone on site", "services": ["medical", "clinic", "mat", "naloxone"]}
        ]

def expand_query_terms(q: str):
    q = (q or "").strip().lower()
    if not q:
        return set()
    terms = set([q])
    for key, syns in SYNONYMS.items():
        if key in q or any(s in q for s in syns):
            terms.update(syns)
    return terms

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
            fields = [
                item.get("name", ""), item.get("notes", ""),
                item.get("type", ""), item.get("neighborhood", "")
            ]
            fields.extend([str(s) for s in item.get("services", [])])
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


# ------------- Tier 2 v1: Case-manager login + dashboard (demo auth) --------------
def logged_in():
    return session.get("authed") is True

def require_login():
    if not logged_in():
        return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pw = (request.form.get("password") or "")
        if pw == DASHBOARD_PASSWORD:
            session["authed"] = True
            log_event("login", "case_manager", {})
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Incorrect password.")
    return render_template("login.html", error=None)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
def dashboard():
    if not logged_in():
        return redirect(url_for("login"))
    # lightweight summaries
    recent_intakes = list(INTAKES)[:50]
    recent_events = list(EVENTS)[:50]
    return render_template("dashboard.html",
                           intakes=recent_intakes,
                           events=recent_events,
                           totals=analytics_summary())

@app.post("/intake/resolve")
def intake_resolve():
    if not logged_in():
        abort(403)
    iid = request.form.get("id", "")
    updated = False
    for item in INTAKES:
        if str(item.get("id")) == str(iid):
            item["status"] = "resolved"
            updated = True
            break
    if updated:
        log_event("intake_resolved", "case_manager", {"id": iid})
    return redirect(url_for("dashboard"))


# ----------------------------------- Tier 3 -----------------------------------
# Robust, simple triage with crisis routing; rate-limited
def ai_triage(user_input: str):
    text = (user_input or "").lower().strip()

    # Crisis / safety language
    crisis_suicide = any(k in text for k in ("suicide", "kill myself", "end my life", "self harm", "self-harm"))
    crisis_violence = any(k in text for k in ("attack", "violence", "assault", "in danger", "threat", "stalker"))
    crisis_od = any(k in text for k in ("overdose", "od", "not breathing", "unconscious", "seizure"))

    if crisis_od or crisis_violence:
        return {"category": "emergency", "recommendation": "Emergency: call 911"}
    if crisis_suicide:
        return {"category": "mental_crisis", "recommendation": "Crisis: call/text 988 (Suicide & Crisis Lifeline)"}

    # Medical
    if any(k in text for k in ("medical", "doctor", "clinic", "nurse", "health", "sick", "injury", "hurt", "wound")):
        return {"category": "medical", "recommendation": "Visit Harbor Free Clinic"}

    # Housing / Shelter
    if any(k in text for k in ("housing", "shelter", "bed", "room", "sleep", "unhoused", "tent")):
        return {"category": "housing", "recommendation": "Apply to Lake Union Women's Shelter"}

    # IDs / documents
    if any(k in text for k in ("id", "ids", "identification", "license", "dmv", "birth certificate", "documents")):
        return {"category": "id", "recommendation": "Go to Seattle ID Assistance Center"}

    # Food / meals
    if any(k in text for k in ("food", "meal", "meals", "groceries", "food bank", "hungry")):
        return {"category": "food", "recommendation": "Visit Pike Place Food Bank"}

    # Detox / Substance
    if any(k in text for k in ("detox", "withdrawal", "sobering", "sobering center", "fentanyl", "alcohol", "heroin")):
        return {"category": "detox", "recommendation": "Call First Step Detox for intake"}

    # Mental health (non-crisis)
    if any(k in text for k in ("mental", "anxiety", "depression", "counseling", "therapy", "psychiatry")):
        return {"category": "mental_health", "recommendation": "Start at Harbor Free Clinic for referral"}

    # Fallback
    return {"category": "general", "recommendation": "Call 211 for local resources"}

@app.route("/ai_triage", methods=["POST"])
def triage():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "ip")
    if not check_rate("triage", ip, max_hits=30, window_sec=300):
        return jsonify({"error": "rate_limited"}), 429
    payload = request.get_json(silent=True) or {}
    user_input = payload.get("message", "")
    result = ai_triage(user_input)
    log_event("triage", "user", {"category": result.get("category")})
    return jsonify({"input": user_input, **result})


# --- Tier 3: Event logging + analytics (no PII) ---
ALLOWED_EVENT_TYPES = {
    "call_click","website_click","directions_click","copy_address",
    "search","filter","triage","intake_submitted","intake_resolved","login"
}

def log_event(evt_type: str, name: str, meta: dict):
    if evt_type not in ALLOWED_EVENT_TYPES:
        return
    EVENTS.appendleft({
        "ts": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "ip": request.headers.get("X-Forwarded-For", request.remote_addr or "ip"),
        "type": evt_type,
        "name": (name or "")[:120],
        "meta": meta or {}
    })

@app.post("/event")
def event():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "ip")
    if not check_rate("event", ip, max_hits=60, window_sec=300):
        return jsonify({"error": "rate_limited"}), 429
    data = request.get_json(silent=True) or {}
    evt_type = str(data.get("type",""))
    name = str(data.get("name",""))
    meta = data.get("meta") or {}
    log_event(evt_type, name, meta)
    return jsonify({"ok": True})

def analytics_summary():
    counts = {}
    top_services = {}
    for e in EVENTS:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
        if e["type"] in ("call_click","website_click","directions_click","copy_address"):
            nm = e.get("name","")
            top_services[nm] = top_services.get(nm, 0) + 1
    return {"counts": counts, "top_services": sorted(top_services.items(), key=lambda x: -x[1])[:10], "events": len(EVENTS), "intakes": len(INTAKES)}

@app.get("/analytics")
def analytics():
    return jsonify(analytics_summary())


# ----------------------------- Local sanity tests ------------------------------------
def test_ai_triage():
    assert ai_triage("I need housing")["category"] == "housing"
    assert ai_triage("I have a medical emergency")["category"] == "medical"
    assert ai_triage("I want ID")["category"] == "id"
    assert ai_triage("I'm hungry")["category"] == "food"
    assert ai_triage("detox please")["category"] == "detox"
    assert "emergency" in ai_triage("overdose")["category"] or "Crisis" in ai_triage("suicide")["recommendation"]

if __name__ == "__main__":
    test_ai_triage()
    app.run(host="0.0.0.0", port=5000, debug=False)



