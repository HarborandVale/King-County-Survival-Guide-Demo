from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
# Allow your frontend (Vite runs on 5173; adjust later if needed)
CORS(app, resources={r"/api/*": {"origins": ["http://localhost:5173", "http://127.0.0.1:5173"]}})

# --- sample resources; we’ll expand later ---
RESOURCES = [
    {
        "id": "kc-1",
        "name": "Self-Managed Night Shelters (SHARE/WHEEL)",
        "category": "shelter",
        "address": "1902 2nd Avenue, Josephinum, Seattle, WA 98101",
        "coords": None,
        "hours": "See website for intake times/locations",
        "phone": None,
        "email": None,
        "website": "https://www.sharewheel.org/share-screening-calendar",
        "services": ["Overnight shelter", "Self-managed"],
        "referralRequired": False,
        "referralBy": [],
        "tags": ["Multiple sites", "Men & Women"],
        "lastVerified": "2025-08-15",
        "distance": "1.2 mi",
        "photos": []
    }
]

@app.get("/api/health")
def health():
    return {"ok": True}

@app.get("/api/resources")
def list_resources():
    """
    Optional filters:
      - ?category=shelter|clinic|showers|meals|day|legal|all
      - ?q=free text search across name/address/services/tags
    """
    cat = request.args.get("category")
    q = (request.args.get("q") or "").strip().lower()

    data = RESOURCES[:]

    if cat and cat != "all":
        data = [r for r in data if r.get("category") == cat]

    if q:
        def hit(r):
            hay = " ".join([
                r.get("name",""),
                r.get("address","") or "",
                " ".join(r.get("services") or []),
                " ".join(r.get("tags") or [])
            ]).lower()
            return q in hay
        data = [r for r in data if hit(r)]

    return jsonify({"items": data, "count": len(data)})

@app.get("/api/resources/<rid>")
def get_resource(rid):
    for r in RESOURCES:
        if r["id"] == rid:
            return jsonify(r)
    return jsonify({"error": "not_found"}), 404

@app.post("/api/feedback")
def feedback():
    payload = request.get_json(silent=True) or {}
    print("FEEDBACK:", payload)
    return {"ok": True}

if __name__ == "__main__":
    # If you run this in Codespaces later, we’ll change host/port then.
    app.run(host="127.0.0.1", port=5000, debug=True)
