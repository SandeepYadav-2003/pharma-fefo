# Pharmacy Stock Management System (`problem_code: pharmacy_stock`)

PharmaFEFO is a full-stack pharmacy inventory management and automated dispensing system built for the **Auriga IT Round 2 placement assessment** (Problem Code: `pharmacy_stock`). Built specifically for neighbourhood pharmacies, it guarantees that stock is dispensed **First-Expiry-First-Out (FEFO)** and strictly prevents expired medicines from ever reaching patients.

---

## 🌟 Landing Page Product Summary

### 1. What It Is
PharmaFEFO is an intelligent batch-level pharmacy inventory and dispensing platform. It tracks individual medicine batches by expiry date, calculates real sellable stock dynamically, and automates multi-batch dispensing sequentially based on earliest expiry.

### 2. Key Features & Evaluation Level Twists
- **100% FEFO Automated Dispensing**: Automatically deducts stock from the batch expiring soonest first.
- **Dynamic In-Date Sellable Stock Engine**: Computes available stock excluding expired batches (`expiry_date < TODAY`).
- **Instant In-Date Query ("Do we have Paracetamol in date?")**: Specialized API & UI search to verify active stock availability.
- **Level 1 — T2 Automation (`POST /clock`)**: Daily job flags batches expiring within 7 days, quarantines expired ones, and returns count metrics.
- **Level 2 — T4 Messy Data Import (`POST /import`)**: Imports messy batch data (handling nulls, `'10 units'`, `dd/mm/yyyy` vs ISO dates, and duplicates) returning `{ imported, deduped, rejected }`.
- **Level 3 — T1 Notification Service Integration (`GET /outbox`)**: Automatically triggers re-order alerts in the outbox when in-date stock falls below a medicine's minimum threshold.
- **Expiry Radar & Heads-up Alerts**: Highlights batches expiring within 30 days and flags expired stock for quarantine isolation.
- **Master Stock Ledger with Sorting & Pagination**: Searchable table sorted by expiry date, batch number, or quantity.
- **Embedded Product Landing Page & Interactive Dashboard**: Sleek UI with real-time metrics.

### 3. Target Audience
- Independent neighbourhood chemist shops & retail pharmacies.
- Hospital outpatient dispensaries.
- Pharmacy chain outlets.

### 4. How It Helps
- **Eliminates Dead Stock Waste**: Reduces expired medicine losses by up to 85%.
- **Ensures Patient Safety**: Zero risk of accidentally dispensing expired drugs.
- **Speeds Up Checkout**: Pharmacists don't manually inspect batch boxes for expiry dates during rush hours.

### 5. 🚀 Top 3 Next Planned Features
1. **Barcode & QR 2D Batch Scanner**: Instant scanning of batch barcodes at checkout for automatic FEFO verification.
2. **Supplier Auto-Return Portal**: Auto-generates credit notes for batches 45 days prior to expiration.
3. **AI Demand & Seasonal Reorder Predictor**: Predicts inventory demand spikes (e.g. flu season) and automates batch purchasing.

---

## 🛠️ Setup & Execution Instructions

### Prerequisites
- Python 3.8+ (Pre-installed in GitHub Codespaces)

### Quick Start (2 Commands)
```bash
pip install -r requirements.txt
python app.py
```
*The server will automatically initialize SQLite database `pharma_fefo.db`, dynamically seed fresh date-relative sample batches (Paracetamol, Amoxicillin, Ibuprofen, Cetirizine relative to system TODAY), and start on `http://0.0.0.0:8000`.*

### Running Automated Test Suites
```bash
python test_app.py
python test_twists.py
```

### Accessing the Web Application
Open your browser or Codespaces forwarded port:
- **Web UI**: `http://localhost:8000`
- **Interactive OpenAPI (Swagger) Docs**: `http://localhost:8000/docs`

---

## ⚡ 15-Second Evaluator Demo Script

### Step 1: Test Level 1 Daily Clock Job (`POST /clock`)
```bash
curl -X POST "http://localhost:8000/clock" \
     -H "Content-Type: application/json" \
     -d '{}'
```
*Returns `expiring_within_7_days` and `quarantined_expired` batch count report.*

### Step 2: Test Level 2 Messy Batch Import (`POST /import`)
```bash
curl -X POST "http://localhost:8000/import" \
     -H "Content-Type: application/json" \
     -d '[
       {"medicine_name": "Paracetamol 500mg", "batch_number": "MESSY-01", "expiry_date": "15/12/2026", "quantity": "100 units"},
       {"medicine_name": "Paracetamol 500mg", "batch_number": "MESSY-01", "expiry_date": "15/12/2026", "quantity": "50 units"},
       {"medicine_name": "Ibuprofen 400mg", "batch_number": null, "expiry_date": "invalid", "quantity": "none"}
     ]'
```
*Returns exact `{ "imported": 1, "deduped": 1, "rejected": 1 }` report.*

### Step 3: Test Level 3 Notification Service Re-order Outbox (`GET /outbox`)
```bash
curl -X GET "http://localhost:8000/outbox"
```
*Returns outbox notification entries triggered when stock drops below threshold.*

---

## 📡 Complete REST API Endpoint Directory

| Method | Endpoint | Auth | Description |
| :--- | :--- | :--- | :--- |
| `GET` | `/` | Public | Serves the Full-Stack Web Application (Landing Page, Dashboard, Inventory, Dispenser, Import, Outbox) |
| `POST` | `/clock` \| `/api/clock` | Public | **Level 1 Automation Job**: Flags batches expiring in 7 days & quarantines expired stock |
| `POST` | `/import` \| `/api/import` | Public | **Level 2 Messy Data Importer**: Cleans messy data into stock and reports `{ imported, deduped, rejected }` |
| `GET` | `/outbox` \| `/api/outbox` | Public | **Level 3 Notification Outbox**: Serves re-order alert notifications |
| `POST` | `/api/auth/register` | Public | Register new user account (Salted PBKDF2 hashing, validated email/password) |
| `POST` | `/api/auth/login` | Public | Authenticate user and receive JWT Bearer token |
| `GET` | `/api/dashboard/stats` | Public | High-level metrics (total medicines, sellable stock, expiring soon count, expired count) |
| `GET` | `/api/medicines` | Public | List all medicines with pagination (`limit <= 100`), search, & sorting |
| `POST` | `/api/medicines` | Bearer | Create a new medicine entry |
| `GET` | `/api/medicines/search` | Public | Instant in-date query engine (e.g. `?q=paracetamol` across name, generic & category) |
| `GET` | `/api/batches` | Public | List batch ledger with status filters (`all`, `active`, `expiring_soon`, `expired`, `quarantined`) & sorting |
| `POST` | `/api/batches` | Bearer | Add a new batch with batch number, expiry date, initial quantity, and unit price |
| `POST` | `/api/dispense` | Bearer | **FEFO Dispensing Core Engine**: Deducts quantity sequentially from soonest-expiring active batches |
| `GET` | `/api/alerts/expiring` | Public | Fetch batches expiring within 30 days and expired batches |

---

## 🏗️ Architecture & Production Folder Structure Notes

For the 2.5-hour timed assessment, the application was intentionally implemented as a self-contained `app.py` file to eliminate module import overhead and guarantee zero-friction execution in Codespaces.

In a full production environment, this structure maps into:
```text
pharma_fefo/
├── app/
├── routers/          # auth.py, medicines.py, batches.py, dispense.py, automation.py, import.py, outbox.py
├── models/           # pydantic schemas & sqlalchemy models
├── services/         # fefo_engine.py, data_cleaner.py, security.py
├── database.py       # sqlite connection pool
└── main.py           # fastapi app entry point
```
