import app
from fastapi.testclient import TestClient
import json

client = TestClient(app.app)

def test_level_1_clock():
    print("--- Testing Level 1 (POST /clock) ---")
    response = client.post("/clock", json={})
    assert response.status_code == 200
    data = response.json()
    print("POST /clock Response:", data)
    assert "expiring_within_7_days" in data
    assert "quarantined_expired" in data
    assert data["status"] == "success"
    print("[OK] Level 1 (POST /clock) PASSED!")

def test_level_2_import():
    print("\n--- Testing Level 2 (POST /import) ---")
    messy_payload = [
        # Valid batch 1
        {"medicine_name": "Paracetamol 500mg", "batch_number": "MESSY-B1", "expiry_date": "15/12/2026", "quantity": "100 units", "unit_price": 3.5},
        # Valid batch 2 (ISO date)
        {"medicine_name": "Amoxicillin 250mg", "batch_number": "MESSY-B2", "expiry_date": "2026-11-20", "quantity": 150, "unit_price": 5.0},
        # Duplicate batch of MESSY-B1
        {"medicine_name": "Paracetamol 500mg", "batch_number": "MESSY-B1", "expiry_date": "15/12/2026", "quantity": "50 units"},
        # Duplicate batch of pre-existing PARA-B2026-01
        {"medicine_name": "Paracetamol 500mg", "batch_number": "PARA-B2026-01", "expiry_date": "2026-10-10", "quantity": "200 units"},
        # Invalid batch (null batch number)
        {"medicine_name": "Ibuprofen 400mg", "batch_number": None, "expiry_date": "2026-10-10", "quantity": "50 units"},
        # Invalid batch (unparseable date)
        {"medicine_name": "Ibuprofen 400mg", "batch_number": "MESSY-BAD-DATE", "expiry_date": "not-a-date", "quantity": "50 units"},
        # Invalid batch (unparseable quantity)
        {"medicine_name": "Ibuprofen 400mg", "batch_number": "MESSY-BAD-QTY", "expiry_date": "2026-10-10", "quantity": "no-digits-here"}
    ]
    
    response = client.post("/import", json=messy_payload)
    assert response.status_code == 200
    data = response.json()
    print("POST /import Response:", data)
    assert data["imported"] == 2
    assert data["deduped"] == 2
    assert data["rejected"] == 3
    assert data["report"] == {"imported": 2, "deduped": 2, "rejected": 3}
    print("[OK] Level 2 (POST /import) PASSED!")

def test_level_3_outbox():
    print("\n--- Testing Level 3 (GET /outbox & Re-order Alert Integration) ---")
    # First, let's login to get a Bearer token
    reg_resp = client.post("/api/auth/login", json={"username": "pharmacist", "password": "admin123"})
    token = reg_resp.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    
    # Check current medicines stock
    meds = client.get("/api/medicines").json()["data"]
    para = next(m for m in meds if "Paracetamol" in m["name"])
    print(f"Initial Paracetamol Sellable Stock: {para['sellable_stock']}, Threshold: {para['min_threshold']}")
    
    # Dispense until stock drops below threshold (100)
    disp_qty = para["sellable_stock"] - 20
    disp_resp = client.post("/api/dispense", json={"medicine_id": para["id"], "quantity": disp_qty, "customer_name": "Test Outbox"}, headers=headers)
    assert disp_resp.status_code == 200
    print(f"Dispensed {disp_qty} units of Paracetamol. Response message:", disp_resp.json()["message"])
    
    # Query outbox
    outbox_resp = client.get("/outbox")
    assert outbox_resp.status_code == 200
    outbox_data = outbox_resp.json()
    print("GET /outbox Response:", json.dumps(outbox_data, indent=2))
    assert outbox_data["count"] >= 1
    alerts = [o for o in outbox_data["outbox"] if o["medicine_id"] == para["id"]]
    assert len(alerts) > 0
    print("Found Re-order Alert in Outbox:", alerts[0]["message"])
    print("[OK] Level 3 (GET /outbox) PASSED!")

if __name__ == "__main__":
    test_level_1_clock()
    test_level_2_import()
    test_level_3_outbox()
    print("\nALL 3 LEVEL TWISTS TESTED AND PASSED PERFECTLY!")
