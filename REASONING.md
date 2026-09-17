# REASONING.md — Technical Architecture, Decision Rationale & Testing Audit

## 1. Problem Understanding & Core Strategy

The challenge requires building an end-to-end full-stack product for a neighbourhood pharmacy to solve batch management, inventory visibility, and First-Expiry-First-Out (FEFO) dispensing.

### Key Domain Rules Handled:
1. **FEFO Priority**: Stock must be consumed strictly starting from the batch that expires soonest.
2. **Expired Stock Isolation**: Expired batches (`expiry_date < TODAY`) must **NEVER** be dispensed under any circumstance and must be excluded from sellable stock totals.
3. **In-Date Stock Queries**: Rapid response to questions like *"Do we have Paracetamol in date?"*.
4. **Expiry Heads-Up**: Clear visibility into batches nearing expiration (within 30 days).

---

## 2. Architectural Choices & Stack Selection

### Stack Chosen: Python 3 (FastAPI) + SQLite + HTML5/TailwindCSS Single-Page App
- **Why FastAPI?**: 
  - Zero-overhead async Python framework with automatic OpenAPI documentation.
  - Native Pydantic data validation ensures bad inputs are caught before reaching the DB.
  - Extremely lightweight — runs out-of-the-box in GitHub Codespaces without complex build tools.
- **Why SQLite?**: 
  - Zero setup, self-contained relational database built into Python's standard library.
  - Supports SQL foreign keys, ACID transactions, aggregate functions (`SUM`, `CASE WHEN`, `MIN`), and fast date comparisons (`YYYY-MM-DD`).
- **Why Single-Page Application (SPA) UI?**:
  - Blazing fast UX with instant tab switching (Landing Page, Dashboard, Inventory, Batches, Dispenser, Alerts).
  - Uses Tailwind CSS via CDN for a modern, responsive presentation.

---

## 3. FEFO Algorithm Design & Implementation Detail

The FEFO algorithm in `POST /api/dispense` operates as follows:

```sql
SELECT id, batch_number, expiry_date, current_qty, unit_price
FROM batches
WHERE medicine_id = ? AND expiry_date >= CURRENT_DATE AND current_qty > 0
ORDER BY expiry_date ASC, id ASC
```

### Execution Flow:
1. **Filtering**: Strictly filters `expiry_date >= TODAY` and `current_qty > 0`. This guarantees expired batches are completely invisible to the algorithm.
2. **Sorting**: Orders by `expiry_date ASC`. The earliest expiring batch is always at index `0`.
3. **Deduction Loop**:
   - Calculates `take_qty = min(batch.current_qty, remaining_needed)`.
   - Decrements `batch.current_qty` in the database.
   - Logs an audit row in `dispense_logs` capturing `batch_id`, `quantity_dispensed`, `dispensed_by`, and timestamp.
4. **Transaction Integrity**: Wrapped inside a single SQLite database transaction to prevent partial updates.

---

## 4. Testing Strategy & Edge Cases Verified

During development, the system was tested against the following critical edge cases:

### Case 1: Dispense Quantity Exceeds Single Batch
- **Scenario**: Customer orders 180 units of Paracetamol. Batch A has 150 units (expires in 15 days), Batch B has 300 units (expires in 180 days).
- **Result**: System automatically took 150 units from Batch A (depleting it to 0) and 30 units from Batch B, recording a multi-batch FEFO breakdown.

### Case 2: Attempting to Dispense Expired Stock
- **Scenario**: Paracetamol Batch C has 50 units but expired 10 days ago.
- **Result**: FEFO query ignores Batch C entirely. If only Batch C is available, the API returns HTTP 400 *"Insufficient sellable stock (0 sellable units)"*.

### Case 3: In-Date Stock Calculation
- **Query**: `GET /api/medicines/search?q=paracetamol`
- **Result**: Returns sellable stock as 450 units (150 from Batch A + 300 from Batch B), ignoring the 50 expired units in Batch C.

---

## 5. Bug Fixes & Refinements

1. **Date Format Standardization**: Standardized all date strings to ISO 8601 `YYYY-MM-DD` for lexicographical sorting in SQLite queries.
2. **Dynamic Badging**: Added automated badge coloring (`green` for >30d, `amber` for <=30d, `rose` for expired) in both backend APIs and UI components.
3. **CORS & Middleware**: Configured `CORSMiddleware` to allow seamless API access from external environments (like Codespaces port forwarding).
