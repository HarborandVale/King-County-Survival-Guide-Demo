# Optimized code framework for the concierge app build, divided into tiers

try:
    from flask import Flask, request, jsonify, render_template
except ModuleNotFoundError:
    raise ImportError("Flask is not installed in this environment. Please install it using 'pip install flask' or run the code in an environment where Flask is available.")

import os

app = Flask(__name__)

@app.route('/health')
def health():
    return jsonify({"ok": True})
# --- TIER 1: MVP ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/submit_form', methods=['POST'])
def submit_form():
    data = request.form
    print("Form submitted:", data)
    return jsonify({'status': 'success'})

# --- TIER 2: Case manager dashboard + API integration ---
def fetch_services_from_api():
    return [
        {'name': 'Shelter A', 'beds': 2, 'status': 'Available'},
        {'name': 'Clinic B', 'status': 'Walk-ins Only'}
    ]

@app.route('/services')
def services():
    services = fetch_services_from_api()
    return jsonify(services)

# --- TIER 3: AI smart routing + analytics ---
def ai_triage(user_input):
    if 'housing' in user_input.lower():
        return {'recommendation': 'Apply to Shelter A'}
    elif 'medical' in user_input.lower():
        return {'recommendation': 'Visit Clinic B'}
    return {'recommendation': 'Call 211'}

@app.route('/ai_triage', methods=['POST'])
def triage():
    user_input = request.json.get('message', '')
    return jsonify(ai_triage(user_input))

# --- TEST CASES ---
def test_ai_triage():
    assert ai_triage("I need housing") == {'recommendation': 'Apply to Shelter A'}
    assert ai_triage("I have a medical emergency") == {'recommendation': 'Visit Clinic B'}
    assert ai_triage("Something else") == {'recommendation': 'Call 211'}

test_ai_triage()

if __name__ == '__main__':
    # Disable debug mode to avoid multiprocessing errors in restricted environments
    app.run(debug=False)

