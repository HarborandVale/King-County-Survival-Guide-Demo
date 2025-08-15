# app.py — known-good minimal skeleton for your demo
from flask import Flask, request, jsonify, render_template, abort
from werkzeug.middleware.proxy_fix import ProxyFix
import os, json, csv

app = Flask(__name__, static_folder="static", template_folder="templates")
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ---------- config & helpers ----------
DATA_DIR  = os.path.join(app.root_path, "static", "data")
DATA_FILE = os.path.join(DATA_DIR, "services.json")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")  # set in Render → Environment

def _load_services():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        print("Error reading services.json:", e)
        return []

def _match(service, q):
    hay = " ".join([
        str(service.get("name","")), str(service.get("type","")),
        str(service.get("address","")), str(service.get("neighborhood","")),
        str(service.get("notes","")),
        " ".join(service.get("tags") or []),
        " ".join(service.get("services") or []),
        str(service.get("phone","")), str(service.get("hours","")),
    ]).lower()
    return q in hay

def _parse_bool(s):
    return str(s).strip().lower() in {"1","true","yes","y"}

# ---------- routes ----------
@app.route("/")
def index():
    # Renders templates/index.html
    return render_template("index.html")

@app.route("/robots.txt")
def robots():
    return "User-agent: *\nDisallow:", 200, {"Content-Type": "text/plain; charset=utf-8"}

@app.route("/services")
def services():
    """
    GET /services
      ?q=keyword    (search across fields)
      ?type=Clinic  (exact match on 'type')
    """
    data = _load_services()
    q = (request.args.get("q") or "").strip().lower()
    want_type = (request.args.get("type") or "").strip().lower()

    if q:
        data = [s for s in data if _match(s, q)]
    if want_type:
        data = [s for s in data if (s.get("type","").strip().lower() == want_type)]
    return jsonify(data)

@app.route("/submit_form", methods=["POST"])
def submit_form():
    # echo back form for demo
    payload = {k: v for k, v in request.form.items()}
    return jsonify({"status": "success", "received": payload})

def ai_triage(text: str):
    t = (text or "").lower()
    if any(k in t for k in ("medical","doctor","clinic","nurse","health","sick","injury","hurt","wound","od","overdose")):
        return {"recommendation": "Visit Clinic B"}
    if any(k in t for k in ("housing","shelter","bed","room","sleep","unhoused","tent")):
        return {"recommendation": "Apply to Shelter A"}
    return {"recommendation": "Call 211"}

@app.route("/ai_triage", methods=["POST"])
def triage():
    payload = request.get_json(silent=True) or {}
    msg = payload.get("message","")
    return jsonify({"input": msg, **ai_triage(msg)})

# ---- optional admin CSV -> JSON loader (token gated) ----
@app.route("/admin", methods=["GET"])
def admin_form():
    key = request.args.get("key","")
    if not ADMIN_KEY or key != ADMIN_KEY:
        return abort(403)
    return f"""
    <!doctype html><meta charset="utf-8">
    <h2>Upload CSV → services.json</h2>
    <form action="/admin/load_csv?key={key}" method="post" enctype="multipart/form-data">
      <p><input type="file" name="file" accept=".csv" required>
      <p><button type="submit">Upload & Convert</button>
    </form>
    <p>Headers: name,type,address,neighborhood,phone,email,hours,website,notes,tags,services,distance,walk_in,beds,lastVerified,lat,lng,photo
    """

@app.route("/admin/load_csv", methods=["POST"])
def admin_load_csv():
    key = request.args.get("key","")
    if not ADMIN_KEY or key != ADMIN_KEY:
        return abort(403)
    if "file" not in request.files:
        return jsonify({"error":"no file"}), 400
    file = request.files["file"]
    text = file.stream.read().decode("utf-8", errors="ignore")
    rows = list(csv.DictReader(text.splitlines()))
    out = []
    for i, r in enumerate(rows, start=1):
        tags = [t.strip() for t in (r.get("tags") or "").split(";") if t.strip()]
        services = [t.strip() for t in (r.get("services") or "").split(";") if t.strip()]
        try_beds = r.get("beds")
        beds = int(try_beds) if (try_beds and str(try_beds).isdigit()) else None
        item = {
            "id": r.get("id") or f"{(r.get('type') or 'svc').lower()}-{i}",
            "name": r.get("name") or "Unnamed",
            "type": r.get("type") or "",
            "address": r.get("address") or "",
            "neighborhood": r.get("neighborhood") or "",
            "phone": r.get("phone") or "",
            "email": r.get("email") or "",
            "hours": r.get("hours") or "",
            "website": r.get("website") or "",
            "notes": r.get("notes") or "",
            "tags": tags,
            "services": services,
            "distance": r.get("distance") or "",
            "walk_in": _parse_bool(r.get("walk_in")),
            "beds": beds,
            "lastVerified": r.get("lastVerified") or "",
            "lat": float(r["lat"]) if r.get("lat") else None,
            "lng": float(r["lng"]) if r.get("lng") else None,
            "photo": r.get("photo") or ""
        }
        out.append(item)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "written": len(out), "file": "/static/data/services.json"})

if __name__ == "__main__":
    app.run(debug=False)
