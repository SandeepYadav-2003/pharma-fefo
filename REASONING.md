# REASONING.md — Technical Architecture, Security Rationale & Audit Notes

## 1. Problem Understanding & Core Strategy

The challenge requires building an end-to-end full-stack product for a neighbourhood pharmacy to solve batch management, inventory visibility, and First-Expiry-First-Out (FEFO) dispensing.

### Key Domain Rules Handled:
1. **FEFO Priority**: Stock must be consumed strictly starting from the batch that expires soonest (`expiry_date ASC, id ASC`).
2. **Expired Stock Isolation**: Expired batches (`expiry_date < TODAY`) must **NEVER** be dispensed under any circumstance and must be excluded from sellable stock totals.
3. **In-Date Stock Queries**: Rapid response to questions like *"Do we have Paracetamol in date?"*.
4. **Expiry Heads-Up**: Clear visibility into batches nearing expiration (within 30 days).

---

## 2. Architectural & Security Choices

### Security & Access Control Design:
- **Public Read Access (By Design)**: Read endpoints (`/api/medicines`, `/api/batches`, `/api/medicines/search`, `/api/alerts/expiring`) are public to allow instant walk-in inventory lookup and public landing page visibility without requiring login friction.
- **Strict Bearer Token Auth (Write Operations)**: Write operations (`POST /api/dispense`, `POST /api/medicines`, `POST /api/batches`) strictly enforce `require_auth` with JWT tokens, returning HTTP 401 on missing or invalid headers.
- **Salted Password Hashing**: Uses standard library `hashlib.pbkdf2_hmac` (`sha256`, 100,000 iterations) with 16-byte random hex salts (`os.urandom(16).hex()`).

---

## 3. FEFO Algorithm Design & Boundary Policies

The FEFO algorithm in `POST /api/dispense` executes:

```sql
SELECT id, batch_number, expiry_date, current_qty, unit_price
FROM batches
WHERE medicine_id = ? AND expiry_date >= CURRENT_DATE AND current_qty > 0
ORDER BY expiry_date ASC, id ASC
```

### Expiry Date Boundary Policy:
- A batch expiring on **today's date** (`expiry_date == TODAY`) is treated as sellable through the end of the current day.
- A batch expiring **yesterday or earlier** (`expiry_date < TODAY`) is strictly marked `EXPIRED` and ignored.

---

## 4. Known Limitations & Future Enhancements

1. **Database Concurrency**: Under extreme concurrent write volume, two simultaneous dispense requests could inspect the same `current_qty` row. In a multi-worker production environment, this would be addressed with explicit row-level locking (`SELECT ... FOR UPDATE` in PostgreSQL or atomic `UPDATE batches SET current_qty = current_qty - ? WHERE id = ? AND current_qty >= ?` in SQLite).
2. **Single-File Architecture Choice**: The system was intentionally implemented as a single, self-contained `app.py` to eliminate module import overhead during a 2.5-hour build round, mapping cleanly into standard `routers/`, `models/`, and `services/` layers for production.
