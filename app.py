# Harbor & Vale — King County Survival Guide (Demo)
# PWA + Map + Partner Mode + Deep Links + QR/Poster + CSV/Sheet Data Loader
# Tier 2 dashboard + Tier 3 analytics + Guided Intake + Reporting + Filters
# Demo Profile/Goals/Uploads (session/memory only; non-persistent)

from flask import (
    Flask, request, jsonify, render_template, redirect, url_for,
    session, abort, send_file
)
from werkzeug.middleware.proxy_fix import ProxyFix
import os, json, time, threading, csv, io, qrcode, secrets
from collections import deque
from datetime import datetime
import requests

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ----------------- Config / Secrets -----------------
app.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(16))
app.config.update(
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,  # 5MB demo uploads
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
)

DASHBOARD_PASSWORD    = os.environ.get("DASHBOARD_PASSWORD", "letmein")
DASHBOARD_RO_PASSWORD = os.environ.get("DASHBOARD_RO_PASSWORD", "")
SHEET_CSV_URL         = os.environ.get("SHEET_CSV_URL", "").strip()
PARTNER_ALLOWLIST     = [x.strip() for x in os.environ.get("PARTNER_ALLOWLIST", "").split(",") if x.strip()]
# Optional JSON like: {"Harbor Clinic":{"type":"Clinic","neighborhood":"Capitol Hill"}}
PARTNER_FILTERS_JSON  = os.environ.get("PARTNER_FILTERS", "{}")
try:
    PARTNER_FILTERS = json.loads(PARTNER_FILTERS_JSON or "{}")
except Exception:
    PARTNER_FILTERS = {}

# ----------------- Demo stores (memory) -----------------
EVENTS  = deque(maxlen=5000)     # [{t,ts,ip?,type,name,meta}]
INTAKES = deque(maxlen=1000)     # [{id,ts,name,need,details,status,...}]
REPORTS = deque(maxlen=1000)     # [{ts,service,slug,category,suggestion,email?}]
USERS   = {}                     # demo user profiles keyed by email
_next_intake_id = {"v": 1}
RATE, RATE_LOCK = {}, threading.Lock()

def check_rate(scope: str, ip: str, max_hits: int, window_sec: int) -> bool:
    now = time.time()
    with RATE_LOCK:
        bucket = RATE.setdefault(scope, {}).setdefault(ip, deque())
        while bucket and now - bucket[0] > window_sec:
            bucket.popleft()
        if len(bucket) >= max_hits:
            return False
        bucket.append(now)
        return True

# ----------------- HTTPS enforcement -----------------
@app.before_request
def enforce_https():
    if app.debug or request.path in ("/health", "/robots.txt", "/sw.js", "/manifest.json"):
        return
    if request.headers.get("X-Forwarded-Proto", "http") != "https":
        code = 301 if request.method in ("GET", "HEAD") else 307
        return redirect(request.url.replace("http://", "https://", 1), code=code)

# ----------------- Health & robots -----------------
@app.get("/health")
def health(): return jsonify({"ok": True})

@app.get("/robots.txt")
def robots():
    return "User-agent: *\nDisallow: /\n", 200, {"Content-Type": "text/plain"}

@app.errorhandler(404)
def not_found(e): return jsonify({"error": "not found"}), 404

@app.errorhandler(500)
def server_error(e): return jsonify({"error": "server error"}), 500

# ----------------- Partner Mode helpers -----------------
def slugify(name: str) -> str:
    s = (name or "").strip().lower()
    s = s.replace("&", "and").replace("/", "-").replace("’", "").replace("'", "")
    s = "-".join(p for p in s.split() if p)
    allow = "abcdefghijklmnopqrstuvwxyz0123456789-"
    s = "".join(ch for ch in s if ch in allow).strip("-")
    return s or "service"

def current_partner(): return session.get("partner")

# ----------------- Data loader -----------------
# CSV columns: name,type,neighborhood,address,phone,website,hours,notes,walk_in,beds,lat,lng,
# age_min,age_max,lgbtq_friendly,languages,disability_access,tribal_friendly,tribe_run,email,appt_required,van_access
DATA_CACHE = {"items": [], "loaded": "", "source": ""}

def _parse_bool(x): return str(x).strip().lower() in ("1","true","yes","y","t")

def load_csv_bytes(csv_bytes: bytes):
    items = []
    rdr = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
    for row in rdr:
        try:
            langs = [x.strip() for x in (row.get("languages","") or "").split("|") if x.strip()]
            disab = [x.strip() for x in (row.get("disability_access","") or "").split("|") if x.strip()]
            item = {
                "name": row.get("name","").strip(),
                "type": row.get("type","").strip(),
                "neighborhood": row.get("neighborhood","").strip(),
                "address": row.get("address","").strip(),
                "phone": row.get("phone","").strip(),
                "website": row.get("website","").strip(),
                "email": row.get("email","").strip(),
                "hours": row.get("hours","").strip(),
                "notes": row.get("notes","").strip(),
                "walk_in": _parse_bool(row.get("walk_in","")),
                "appt_required": _parse_bool(row.get("appt_required","")),
                "van_access": _parse_bool(row.get("van_access","")),
                "beds": int(row.get("beds","0") or 0),
                "lat": float(row.get("lat","0") or 0),
                "lng": float(row.get("lng","0") or 0),
                "age_min": int(row.get("age_min","0") or 0),
                "age_max": int(row.get("age_max","0") or 0),
                "lgbtq_friendly": _parse_bool(row.get("lgbtq_friendly","")),
                "languages": langs,
                "disability_access": disab,
                "tribal_friendly": _parse_bool(row.get("tribal_friendly","")),
                "tribe_run": _parse_bool(row.get("tribe_run","")),
                "services": [row.get("type","").strip().lower()],
            }
            if not item["name"]:
                continue
            item["slug"] = slugify(item["name"])
            items.append(item)
        except Exception:
            continue
    return items

def try_load_data():
    src, items = "", []
    if SHEET_CSV_URL:
        try:
            r = requests.get(SHEET_CSV_URL, timeout=6)
            if r.ok and r.text.strip():
                items = load_csv_bytes(r.content); src = "sheet"
        except Exception:
            pass
    if not items:
        p = os.path.join(os.path.dirname(__file__), "services.csv")
        if os.path.exists(p):
            with open(p, "rb") as f:
                items = load_csv_bytes(f.read()); src = "csv"
    if not items:
        p = os.path.join(os.path.dirname(__file__), "services.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                items = json.load(f); src = "json"
                for it in items: it.setdefault("slug", slugify(it.get("name","")))
    if not items:
        # minimal fallback
        items = [
            {"name":"Lake Union Women's Shelter","slug":"lake-union-womens-shelter","type":"Shelter","neighborhood":"Downtown","beds":3,"hours":"Intake 4–8pm","walk_in":True,"phone":"(206) 555-1212","address":"123 Pine St, Seattle, WA","website":"https://example.org/shelter","notes":"ID preferred; LGBTQ+ inclusive","services":["shelter"],"lgbtq_friendly":True,"languages":["English"],"disability_access":["Wheelchair"],"tribal_friendly":False,"age_min":18,"age_max":0,"lat":47.6101,"lng":-122.3421},
            {"name":"Aurora Day Center","slug":"aurora-day-center","type":"Day Center","neighborhood":"North Seattle","hours":"8am–6pm daily","walk_in":True,"phone":"(206) 555-3434","address":"8600 Aurora Ave N, Seattle, WA","website":"https://example.org/daycenter","notes":"Showers, laundry, mail","services":["showers","laundry"],"lgbtq_friendly":True,"languages":["English","Spanish"],"disability_access":["Wheelchair","ASL"],"tribal_friendly":False,"age_min":0,"age_max":0,"lat":47.6922,"lng":-122.3440},
            {"name":"Harbor Free Clinic","slug":"harbor-free-clinic","type":"Clinic","neighborhood":"Capitol Hill","hours":"Walk-ins Wed/Fri 1–5pm","walk_in":True,"phone":"(206) 555-4545","address":"500 Broadway E, Seattle, WA","website":"https://example.org/clinic","notes":"MAT referrals; naloxone on site","services":["medical","clinic"],"lgbtq_friendly":True,"languages":["English","Spanish"],"disability_access":["Wheelchair"],"tribal_friendly":True,"tribe_run":False,"age_min":0,"age_max":0,"lat":47.6222,"lng":-122.3208},
        ]; src = "fallback"
    DATA_CACHE.update({"items": items, "loaded": datetime.utcnow().isoformat(timespec="seconds")+"Z", "source": src})

try_load_data()

NEIGHBORHOOD_COORDS = {
    "Downtown": (47.6070,-122.3366),
    "Capitol Hill": (47.6253,-122.3222),
    "North Seattle": (47.7000,-122.3300),
    "Ballard": (47.6687,-122.3860),
    "West Seattle": (47.5663,-122.3867),
    "South Seattle": (47.5400,-122.3000),
}

# ----------------- Routes: core pages -----------------
@app.get("/")
def index():
    org = request.args.get("org")
    if org:
        # Allowlist behavior: if set, enforce; else allow any
        if PARTNER_ALLOWLIST and org not in PARTNER_ALLOWLIST:
            session["partner"] = None
        else:
            session["partner"] = org[:80]
            log_event("partner_visit", org, {})
    return render_template("index.html", partner=current_partner(), partner_filters=PARTNER_FILTERS.get(current_partner() or "", {}))

@app.get("/guided")
def guided(): return render_template("guided.html")

# PWA assets
@app.get("/manifest.json")
def manifest(): return app.send_static_file("manifest.json")

@app.get("/sw.js")
def sw(): return app.send_static_file("sw.js")

# ----------------- Intake / Refer -----------------
@app.post("/submit_form")
def submit_form():
    d = request.form.to_dict(flat=True)
    name = (d.get("name") or "").strip()[:120]
    iid = _next_intake_id["v"]; _next_intake_id["v"] += 1
    INTAKES.appendleft({
        "id": iid,
        "ts": datetime.utcnow().isoformat(timespec="seconds")+"Z",
        "name": name,
        "need": (d.get("need") or "")[:240],
        "details": (d.get("details") or "")[:800],
        "pronouns": (d.get("pronouns") or "")[:40],
        "contact": (d.get("contact") or "")[:160],
        "status": "new"
    })
    log_event("intake_submitted", name or "anonymous", {"need": d.get("need","")})
    return jsonify({"status":"success","id":iid})

@app.post("/refer")
def refer():
    data = request.get_json(silent=True) or {}
    client_name = (data.get("client_name") or "").strip()[:120]
    svc = (data.get("service") or "").strip()[:160]
    iid = _next_intake_id["v"]; _next_intake_id["v"] += 1
    INTAKES.appendleft({
        "id": iid, "ts": datetime.utcnow().isoformat(timespec="seconds")+"Z",
        "name": client_name, "need": f"Referral to: {svc}", "details": "", "status": "new"
    })
    log_event("intake_submitted", client_name or "anonymous", {"via":"refer","service":svc})
    return jsonify({"ok": True, "id": iid})

# ----------------- Services API with filters -----------------
SYNONYMS = {
    "id": ["id","ids","identification","license","dmv","birth certificate"],
    "showers": ["shower","showers","hygiene","laundry"],
    "food": ["food","meal","meals","groceries","food bank","soup"],
    "transport": ["transport","transportation","bus","orca","transit","ticket","pass"],
    "detox": ["detox","withdrawal","sobering","sobering center"],
    "mental": ["mental","counseling","therapy","psychiatry","behavioral"]
}
def _expand(q):
    q = (q or "").strip().lower()
    if not q: return set()
    terms = {q}
    for k, syns in SYNONYMS.items():
        if k in q or any(s in q for s in syns): terms.update(syns)
    return terms

def fetch_services(q=None, kind=None, neighborhood=None, walk_in_only=None,
                   age=None, lgbtq=None, language=None, disability=None, tribal=None, tribe_run=None):
    items = DATA_CACHE["items"]
    qterms = _expand(q)
    def ok(item):
        if kind and item.get("type","").lower() != kind.lower(): return False
        if neighborhood and item.get("neighborhood","").lower() != neighborhood.lower(): return False
        if walk_in_only is True and not item.get("walk_in", False): return False
        if age not in (None,""):
            try:
                a = int(age); amin = int(item.get("age_min",0) or 0); amax = int(item.get("age_max",0) or 0)
                if not (a >= amin and (amax==0 or a <= amax)): return False
                if a < 18: item.setdefault("_minor_note", True)
            except: pass
        if lgbtq is True and not item.get("lgbtq_friendly", False): return False
        if tribal is True and not item.get("tribal_friendly", False): return False
        if tribe_run is True and not item.get("tribe_run", False): return False
        if language:
            langs = [x.lower() for x in (item.get("languages") or [])]
            if language.lower() not in langs: return False
        if disability:
            dis = [x.lower() for x in (item.get("disability_access") or [])]
            if disability.lower() not in dis: return False
        if qterms:
            hay = " ".join([item.get("name",""), item.get("notes",""), item.get("type",""), item.get("neighborhood","")] + [str(s) for s in item.get("services",[]) ]).lower()
            if not any(t in hay for t in qterms): return False
        return True
    return [x for x in items if ok(x)]

@app.get("/services")
def services():
    args = request.args
    walk_only = True if (args.get("walk_in","").lower() in ("1","true","yes")) else None
    lgbtq = True if (args.get("lgbtq","").lower() in ("1","true","yes")) else None
    tribal = True if (args.get("tribal","").lower() in ("1","true","yes")) else None
    tribe_run = True if (args.get("tribe_run","").lower() in ("1","true","yes")) else None
    data = fetch_services(
        q=args.get("q"), kind=args.get("type"), neighborhood=args.get("neighborhood"),
        walk_in_only=walk_only, age=args.get("age"), lgbtq=lgbtq,
        language=args.get("language"), disability=args.get("disability"),
        tribal=tribal, tribe_run=tribe_run
    )
    return jsonify(data)

# ----------------- Map / Deep links / Poster -----------------
@app.get("/map")
def map_view(): return render_template("map.html", partner=current_partner())

@app.get("/s/<slug>")
def service_detail(slug):
    for it in DATA_CACHE["items"]:
        if it.get("slug") == slug:
            lat = it.get("lat") or NEIGHBORHOOD_COORDS.get(it.get("neighborhood",""), (47.6062,-122.3321))[0]
            lng = it.get("lng") or NEIGHBORHOOD_COORDS.get(it.get("neighborhood",""), (47.6062,-122.3321))[1]
            return render_template("service.html", s=it, lat=lat, lng=lng)
    abort(404)

@app.get("/qr/<slug>.png")
def qr_code(slug):
    url = url_for("service_detail", slug=slug, _external=True)
    img = qrcode.make(url); buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return send_file(buf, mimetype="image/png")

@app.get("/poster/<slug>")
def poster(slug):
    for it in DATA_CACHE["items"]:
        if it.get("slug") == slug:
            return render_template("poster.html", s=it)
    abort(404)

# ----------------- Data Status -----------------
@app.get("/data-status")
def data_status():
    items = DATA_CACHE["items"]
    by_type, by_hood = {}, {}
    for it in items:
        by_type[it.get("type","")] = by_type.get(it.get("type",""),0)+1
        by_hood[it.get("neighborhood","")] = by_hood.get(it.get("neighborhood",""),0)+1
    return render_template("data_status.html", loaded=DATA_CACHE["loaded"], source=DATA_CACHE["source"],
                           count=len(items), by_type=by_type, by_hood=by_hood)

# ----------------- Dashboard & Auth -----------------
def set_role(role): session["role"]=role
def role(): return session.get("role")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method=="POST":
        pw = request.form.get("password","")
        if DASHBOARD_RO_PASSWORD and pw == DASHBOARD_RO_PASSWORD:
            set_role("ro"); log_event("login","case_manager_ro",{}); return redirect(url_for("dashboard"))
        if pw == DASHBOARD_PASSWORD:
            set_role("rw"); log_event("login","case_manager",{}); return redirect(url_for("dashboard"))
        return render_template("login.html", error="Incorrect password.")
    return render_template("login.html", error=None)

@app.get("/logout")
def logout(): session.clear(); return redirect(url_for("login"))

@app.get("/dashboard")
def dashboard():
    if role() not in ("ro","rw"): return redirect(url_for("login"))
    return render_template("dashboard.html",
        intakes=list(INTAKES)[:100], events=list(EVENTS)[:100],
        totals=analytics_summary(), read_only=(role()=="ro"))

@app.post("/intake/resolve")
def intake_resolve():
    if role()!="rw": abort(403)
    iid = request.form.get("id","")
    for it in INTAKES:
        if str(it.get("id")) == str(iid):
            it["status"]="resolved"; log_event("intake_resolved","case_manager",{"id":iid}); break
    return redirect(url_for("dashboard"))

# ----------------- Reporting incorrect info -----------------
@app.post("/report")
def report():
    data = request.get_json(silent=True) or {}
    REPORTS.appendleft({
        "ts": datetime.utcnow().isoformat(timespec="seconds")+"Z",
        "service": (data.get("service") or "")[:160],
        "slug": (data.get("slug") or "")[:160],
        "category": (data.get("category") or "")[:80],
        "suggestion": (data.get("suggestion") or "")[:400],
        "email": (data.get("email") or "")[:160],
    })
    # also log as an intake to ensure follow-up appears
    iid = _next_intake_id["v"]; _next_intake_id["v"] += 1
    INTAKES.appendleft({
        "id": iid, "ts": datetime.utcnow().isoformat(timespec="seconds")+"Z",
        "name": data.get("email","") or "anonymous",
        "need": f"Report: {data.get('category','')}",
        "details": f"{data.get('service','')} — {data.get('suggestion','')}",
        "status": "new"
    })
    log_event("report","user",{"category":data.get("category","")})
    return jsonify({"ok": True, "id": iid})

@app.get("/reports")
def reports_json(): return jsonify(list(REPORTS))

# ----------------- AI triage & analytics -----------------
def ai_triage(user_input: str):
    text = (user_input or "").lower().strip()
    if any(k in text for k in ("overdose","od","not breathing","unconscious","seizure","violence","in danger","threat")):
        return {"category":"emergency","recommendation":"Emergency: call 911"}
    if any(k in text for k in ("suicide","kill myself","end my life","self harm","self-harm")):
        return {"category":"mental_crisis","recommendation":"Crisis: call/text 988 (Suicide & Crisis Lifeline)"}
    if any(k in text for k in ("medical","doctor","clinic","nurse","health","sick","injury","hurt","wound")):
        return {"category":"medical","recommendation":"Visit Harbor Free Clinic"}
    if any(k in text for k in ("housing","shelter","bed","room","sleep","unhoused","tent")):
        return {"category":"housing","recommendation":"Apply to Lake Union Women's Shelter"}
    if any(k in text for k in ("id","ids","identification","license","dmv","birth certificate","documents")):
        return {"category":"id","recommendation":"Seattle ID Assistance Center"}
    if any(k in text for k in ("food","meal","meals","groceries","food bank","hungry")):
        return {"category":"food","recommendation":"Pike Place Food Bank"}
    if any(k in text for k in ("detox","withdrawal","sobering","sobering center","fentanyl","alcohol","heroin")):
        return {"category":"detox","recommendation":"Call First Step Detox for intake"}
    if any(k in text for k in ("mental","anxiety","depression","counseling","therapy","psychiatry")):
        return {"category":"mental_health","recommendation":"Start at Harbor Free Clinic for referral"}
    return {"category":"general","recommendation":"Call 211 for local resources"}

@app.post("/ai_triage")
def triage():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "ip")
    if not check_rate("triage", ip, max_hits=30, window_sec=300): return jsonify({"error":"rate_limited"}), 429
    payload = request.get_json(silent=True) or {}
    user_input = payload.get("message","")
    result = ai_triage(user_input)
    log_event("triage","user",{"category":result.get("category")})
    return jsonify({"input":user_input, **result})

ALLOWED_EVENT_TYPES = {
    "call_click","website_click","directions_click","copy_address","search","filter",
    "triage","intake_submitted","intake_resolved","login","guided_start","guided_complete",
    "lang_select","contrast_toggle","quick_exit","report","partner_visit","profile_save","goal_add","upload"
}
def log_event(evt_type: str, name: str, meta: dict):
    if evt_type not in ALLOWED_EVENT_TYPES: return
    EVENTS.appendleft({
        "t": time.time(),
        "ts": datetime.utcnow().isoformat(timespec="seconds")+"Z",
        # Do NOT store IP (privacy). We use IP only in rate-limiter buckets.
        "type": evt_type, "name": (name or "")[:120], "meta": meta or {}
    })

def analytics_summary():
    counts, top_services = {}, {}
    for e in EVENTS:
        counts[e["type"]] = counts.get(e["type"],0)+1
        if e["type"] in ("call_click","website_click","directions_click","copy_address"):
            nm = e.get("name",""); top_services[nm] = top_services.get(nm,0)+1
    return {"counts":counts, "top_services":sorted(top_services.items(), key=lambda x:-x[1])[:10],
            "events":len(EVENTS), "intakes":len(INTAKES), "reports":len(REPORTS)}

@app.get("/analytics")
def analytics(): return jsonify(analytics_summary())

@app.get("/daily.json")
def daily_json():
    since = time.time() - 86400
    counts = {}
    for e in EVENTS:
        if e.get("t", 0) >= since:
            counts[e["type"]] = counts.get(e["type"],0)+1
    return jsonify({"last_24h":counts, "intakes":len([i for i in INTAKES if True])})

@app.get("/digest.txt")
def digest_txt():
    s = analytics_summary()
    lines = [f"Events: {s['events']}", f"Intakes: {s['intakes']}", f"Reports: {s['reports']}", "Counts:"]
    lines += [f"- {k}: {v}" for k,v in s["counts"].items()]
    return "\n".join(lines), 200, {"Content-Type":"text/plain"}

@app.get("/export.csv")
def export_csv():
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["dataset","ts","type","name","meta","id","need","details","status","pronouns","contact"])
    for e in reversed(EVENTS):
        w.writerow(["event", e.get("ts",""), e.get("type",""), e.get("name",""), json.dumps(e.get("meta",{})),"","","","","",""])
    for i in reversed(INTAKES):
        w.writerow(["intake", i.get("ts",""), "", i.get("name",""), "", i.get("id",""), i.get("need",""), i.get("details",""), i.get("status",""), i.get("pronouns",""), i.get("contact","")])
    return buf.getvalue(), 200, {"Content-Type":"text/csv"}

# ----------------- Privacy & Safety -----------------
@app.get("/privacy")
def privacy(): return render_template("privacy.html")

# For now, safety numbers will be loaded from hotlines.json if present, else a stub
HOTLINES = [
    {"name":"Emergency", "phone":"911", "notes":"Life-threatening emergency"},
    {"name":"988 Suicide & Crisis Lifeline", "phone":"988", "notes":"Call/Text 988"},
    {"name":"211 Community Resources", "phone":"211", "notes":"General help"},
    # Add local/tribal hotlines in hotlines.json later
]
@app.get("/safety")
def safety():
    path = os.path.join(os.path.dirname(__file__), "hotlines.json")
    items = HOTLINES
    try:
        if os.path.exists(path):
            with open(path,"r",encoding="utf-8") as f:
                items = json.load(f) or HOTLINES
    except Exception:
        pass
    return render_template("safety.html", hotlines=items)

# ----------------- Accessibility pages -----------------
@app.get("/assist/deaf")
def assist_deaf(): return render_template("assist_deaf.html")

@app.get("/assist/blind")
def assist_blind(): return render_template("assist_blind.html")

# ----------------- Demo user profile / goals / upload -----------------
@app.route("/profile", methods=["GET","POST"])
def profile():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()[:160]
        name  = (request.form.get("name")  or "").strip()[:120]
        if email:
            USERS.setdefault(email, {"email":email, "name":name, "goals":[], "uploads":[]})
            session["user_email"] = email
            log_event("profile_save", email, {})
        return redirect(url_for("profile"))
    email = session.get("user_email")
    user = USERS.get(email) if email else None
    return render_template("profile.html", user=user)

@app.post("/goal/add")
def goal_add():
    email = session.get("user_email")
    if not email: abort(403)
    USERS.setdefault(email, {"email":email,"name":"","goals":[],"uploads":[]})
    text = (request.form.get("text") or "").strip()[:200]
    if text:
        USERS[email]["goals"].append({"text":text,"done":False,"ts":datetime.utcnow().isoformat(timespec="seconds")+"Z"})
        log_event("goal_add", email, {})
    return redirect(url_for("profile"))

@app.post("/upload")
def upload():
    email = session.get("user_email")
    if not email: abort(403)
    f = request.files.get("file")
    if not f: return redirect(url_for("profile"))
    USERS[email]["uploads"].append({"filename":f.filename, "size":len(f.read())})
    log_event("upload", email, {"filename":f.filename})
    return redirect(url_for("profile"))
