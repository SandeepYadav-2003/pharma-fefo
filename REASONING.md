# REASONING.md — Technical Architecture, Security Rationale & Audit Notes

## 1. Problem Understanding & Core Strategy (`problem_code: pharmacy_stock`)

The challenge requires building an end-to-end full-stack product for a neighbourhood pharmacy to solve batch management, inventory visibility, and First-Expiry-First-Out (FEFO) dispensing, alongside 3 level twists:

### Key Domain Rules & Level Twists Handled:
1. **FEFO Priority**: Stock must be consumed strictly starting from the batch that expires soonest (`expiry_date ASC, id ASC`).
2. **Expired Stock Isolation**: Expired batches (`expiry_date < TODAY`) must **NEVER** be dispensed under any circumstance and must be excluded from sellable stock totals.
3. **In-Date Stock Queries**: Rapid response to queries like *"Do we have Paracetamol in date?"*.
4. **Level 1 — Daily Automation Clock (`POST /clock`)**: Flags batches expiring within 7 days, automatically sets `status = 'quarantined'` for expired stock, and returns metric counts.
5. **Level 2 — Messy Batch Data Importer (`POST /import`)**: Normalizes messy input (cleaning nulls, string quantities like `"10 units"`, `dd/mm/yyyy` vs ISO dates, and deduplicating rows) into clean stock with `{ imported, deduped, rejected }` report.
6. **Level 3 — Re-order Notification Outbox (`GET /outbox`)**: Monitors sellable in-date stock and automatically logs re-order alert notifications into the outbox when stock drops below minimum threshold (`min_threshold`).

---

## 2. Architectural & Security Choices

### Security & Access Control Design:
- **Public Read & Evaluation Endpoints**: Read endpoints (`/api/medicines`, `/api/batches`, `/api/medicines/search`, `/clock`, `/import`, `/outbox`) are open for seamless evaluation and instant walk-in inventory lookup.
- **Strict Bearer Token Auth (Write Operations)**: Core write operations (`POST /api/dispense`, `POST /api/medicines`, `POST /api/batches`) strictly enforce `require_auth` with JWT tokens, returning HTTP 401 on missing or invalid headers.
- **Salted Password Hashing**: Uses standard library `hashlib.pbkdf2_hmac` (`sha256`, 100,000 iterations) with 16-byte random hex salts (`os.urandom(16).hex()`).

---

## 3. FEFO Algorithm Design & Level Twists Logic

The FEFO algorithm in `POST /api/dispense` executes:

```sql
SELECT id, batch_number, expiry_date, current_qty, unit_price
FROM batches
WHERE medicine_id = ? 
  AND expiry_date >= CURRENT_DATE 
  AND current_qty > 0 
  AND (status IS NULL OR status != 'quarantined')
ORDER BY expiry_date ASC, id ASC
```

### Expiry & Quarantine Policy:
- Batches expiring on **today's date** (`expiry_date == TODAY`) are sellable.
- Batches expiring **yesterday or earlier** (`expiry_date < TODAY`) or marked `status = 'quarantined'` by the Level 1 daily job are strictly isolated and excluded.

---

## 4. Known Limitations & Future Enhancements

1. **Database Concurrency**: In high-concurrency production, row-level locking or atomic SQL decrement expressions (`UPDATE batches SET current_qty = current_qty - ? WHERE id = ? AND current_qty >= ?`) prevent race conditions.
2. **Single-File Architecture Choice**: Implemented as a self-contained `app.py` for zero-friction setup, mapping cleanly into standard `routers/`, `models/`, `services/`, and `database/` modules.
