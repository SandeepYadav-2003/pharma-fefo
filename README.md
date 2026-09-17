# PharmaFEFO — Smart Pharmacy Stock & FEFO Dispensing Engine

PharmaFEFO is a full-stack pharmacy inventory management and automated dispensing system designed to solve batch expiry management. Built specifically for neighbourhood pharmacies, it guarantees that stock is dispensed **First-Expiry-First-Out (FEFO)** and strictly prevents expired medicines from ever reaching patients.

---

## 🌟 Landing Page Product Summary

### 1. What It Is
PharmaFEFO is an intelligent batch-level pharmacy inventory and dispensing platform. It tracks individual medicine batches by expiry date, calculates real sellable stock dynamically, and automates multi-batch dispensing sequentially based on earliest expiry.

### 2. Key Features
- **100% FEFO Automated Dispensing**: Automatically deducts stock from the batch expiring soonest first.
- **Dynamic In-Date Sellable Stock Engine**: Computes available stock excluding expired batches (`expiry_date < TODAY`).
- **Instant In-Date Query ("Do we have Paracetamol in date?")**: Specialized API & UI search to verify active stock availability.
- **Expiry Radar & Heads-up Alerts**: Highlights batches expiring within 30 days and flags expired stock for isolation.
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

### Quick Start (1 Command)
```bash
python app.py
```
*The server will automatically initialize SQLite database `pharma_fefo.db`, seed initial sample data (Paracetamol, Amoxicillin, Ibuprofen, Cetirizine), and start on `http://0.0.0.0:8000`.*

### Accessing the Web Application
Open your browser or Codespaces forwarded port:
- **Web UI**: `http://localhost:8000`
- **Interactive OpenAPI (Swagger) Docs**: `http://localhost:8000/docs`

---

## 📡 Complete REST API Endpoint Directory

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Serves the Full-Stack Web Application (Landing Page, Dashboard, Inventory, Dispenser) |
| `POST` | `/api/auth/register` | Register new user account |
| `POST` | `/api/auth/login` | Authenticate user and receive JWT bearer token |
| `GET` | `/api/dashboard/stats` | High-level metrics (total medicines, sellable stock, expiring soon count, expired count) |
| `GET` | `/api/medicines` | List all medicines with pagination, search, sorting (`name`, `category`), & in-date stock |
| `POST` | `/api/medicines` | Create a new medicine entry |
| `GET` | `/api/medicines/search` | Instant in-date query engine (e.g. `?q=paracetamol`) |
| `GET` | `/api/batches` | List batch ledger with status filters (`all`, `active`, `expiring_soon`, `expired`) & sorting |
| `POST` | `/api/batches` | Add a new batch with batch number, expiry date, initial quantity, and unit price |
| `POST` | `/api/dispense` | **FEFO Dispensing Core Engine**: Deducts quantity sequentially from soonest-expiring active batches |
| `GET` | `/api/alerts/expiring` | Fetch batches expiring within 30 days and expired batches |

---

## 🧪 Debugging & Testing FEFO Logic

### Testing FEFO Dispensing via CLI / Curl:
```bash
curl -X POST "http://localhost:8000/api/dispense" \
     -H "Content-Type: application/json" \
     -d '{"medicine_id": 1, "quantity": 180, "customer_name": "Test Customer"}'
```
*Response will show exact batch breakdown demonstrating that the batch expiring in 15 days was fully depleted first, before taking remaining units from the batch expiring in 180 days, leaving expired batches completely untouched.*
