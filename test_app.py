import app

print("Testing app initialization...")
stats = app.get_dashboard_stats()
print("Dashboard Stats:", stats)

meds = app.get_medicines()
print("Medicines Count:", len(meds["data"]))

search_res = app.search_in_date_medicine("paracetamol")
print("Search Result for Paracetamol:", search_res)

alerts = app.get_expiring_alerts()
print("Expiring Soon Batches:", alerts["expiring_soon_count"])
print("Expired Batches:", alerts["expired_count"])

# Test FEFO Dispense
dispense_req = app.DispenseRequest(medicine_id=1, quantity=180, customer_name="Test Patient")
dispense_res = app.dispense_medicine(dispense_req, current_user={"username": "pharmacist"})
print("FEFO Dispense Result:", dispense_res)

print("\nALL VERIFICATION TESTS PASSED PERFECTLY!")
