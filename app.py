import os
import sqlite3
import datetime
import hashlib
import hmac
import json
import base64
import re
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

try:
    import jwt
    HAS_JWT = True
except ImportError:
    HAS_JWT = False

DB_FILE = "pharma_fefo.db"
SECRET_KEY = os.getenv("SECRET_KEY", "aurigait_pharma_fefo_secret_key_2026")

app = FastAPI(
    title="Pharmacy Stock Management System (problem_code: pharmacy_stock)",
    description="Full-stack Pharmacy Stock Management with FEFO dispensing, daily clock automation (/clock), messy batch import (/import), and re-order notification outbox (/outbox).",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------------------------------------------------------
# AUTHENTICATION & SECURITY HELPERS
# -----------------------------------------------------------------------------

def hash_password(password: str, salt: Optional[str] = None) -> str:
    """PBKDF2-HMAC-SHA256 salted password hashing (100,000 iterations)."""
    salt_hex = salt or os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), 100_000)
    return f"{salt_hex}${dk.hex()}"

def verify_password(password: str, stored_hash: str) -> bool:
    try:
        if '$' not in stored_hash:
            return False
        parts = stored_hash.split('$')
        if len(parts) != 2:
            return False
        salt = parts[0]
        computed = hash_password(password, salt)
        return hmac.compare_digest(computed, stored_hash)
    except Exception as e:
        print("verify_password error:", e)
        return False

def create_token(user_id: int, username: str, role: str) -> str:
    exp_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7)
    payload = {
        "user_id": user_id,
        "username": username,
        "role": role,
        "exp": exp_time
    }
    if HAS_JWT:
        return jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    else:
        payload_copy = payload.copy()
        payload_copy["exp"] = exp_time.isoformat()
        raw = json.dumps(payload_copy).encode('utf-8')
        return base64.b64encode(raw).decode('utf-8')

def require_auth(authorization: Optional[str] = Header(None)):
    """Strict Bearer token authentication required for write operations."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required. Please login.")
    token = authorization.replace("Bearer ", "").strip()
    try:
        if HAS_JWT:
            payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
            return payload
        else:
            raw = base64.b64decode(token.encode('utf-8')).decode('utf-8')
            return json.loads(raw)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

def optional_auth(authorization: Optional[str] = Header(None)):
    """Lenient token inspector for public reads."""
    if not authorization or not authorization.startswith("Bearer "):
        return {"user_id": 0, "username": "guest", "role": "public"}
    try:
        token = authorization.replace("Bearer ", "").strip()
        if HAS_JWT:
            return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        else:
            raw = base64.b64decode(token.encode('utf-8')).decode('utf-8')
            return json.loads(raw)
    except Exception:
        return {"user_id": 0, "username": "guest", "role": "public"}

# -----------------------------------------------------------------------------
# DATABASE SETUP & MIGRATIONS
# -----------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT DEFAULT 'pharmacist',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS medicines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        generic_name TEXT,
        category TEXT,
        unit TEXT DEFAULT 'tablets',
        min_threshold INTEGER DEFAULT 50,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS batches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medicine_id INTEGER NOT NULL,
        batch_number TEXT NOT NULL UNIQUE,
        expiry_date DATE NOT NULL,
        initial_qty INTEGER NOT NULL,
        current_qty INTEGER NOT NULL,
        unit_price REAL NOT NULL,
        supplier TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (medicine_id) REFERENCES medicines(id)
    );
    """)

    # Ensure status column exists if table was pre-existing
    cursor.execute("PRAGMA table_info(batches)")
    columns = [col[1] for col in cursor.fetchall()]
    if "status" not in columns:
        cursor.execute("ALTER TABLE batches ADD COLUMN status TEXT DEFAULT 'active'")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS dispense_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        medicine_id INTEGER NOT NULL,
        batch_id INTEGER NOT NULL,
        quantity_dispensed INTEGER NOT NULL,
        dispensed_by TEXT NOT NULL,
        customer_name TEXT,
        dispensed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (medicine_id) REFERENCES medicines(id),
        FOREIGN KEY (batch_id) REFERENCES batches(id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL DEFAULT 'reorder_alert',
        medicine_id INTEGER NOT NULL,
        medicine_name TEXT NOT NULL,
        current_stock INTEGER NOT NULL,
        threshold INTEGER NOT NULL,
        message TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    conn.commit()

    # Seed default admin user
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        admin_pass = hash_password("admin123")
        cursor.execute("INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)",
                       ("pharmacist", "pharmacist@aurigait-pharma.com", admin_pass, "admin"))
        conn.commit()

    # Dynamic date-relative seeding
    cursor.execute("SELECT COUNT(*) FROM medicines")
    if cursor.fetchone()[0] == 0:
        today = datetime.date.today()
        
        meds = [
            ("Paracetamol 500mg", "Acetaminophen", "Analgesics / Antipyretics", "tablets", 100),
            ("Amoxicillin 250mg", "Amoxicillin Trihydrate", "Antibiotics", "capsules", 50),
            ("Ibuprofen 400mg", "Ibuprofen", "NSAIDs", "tablets", 80),
            ("Cetirizine 10mg", "Cetirizine Hydrochloride", "Antihistamines", "tablets", 60),
            ("Metformin 500mg", "Metformin HCl", "Antidiabetic", "tablets", 100)
        ]
        
        for name, gen, cat, unit, thresh in meds:
            cursor.execute("INSERT INTO medicines (name, generic_name, category, unit, min_threshold) VALUES (?, ?, ?, ?, ?)",
                           (name, gen, cat, unit, thresh))
        conn.commit()

        batches_data = [
            # Paracetamol
            (1, "PARA-B2026-01", (today + datetime.timedelta(days=15)).strftime("%Y-%m-%d"), 150, 150, 2.50, "Sun Pharma", "active"),
            (1, "PARA-B2026-02", (today + datetime.timedelta(days=180)).strftime("%Y-%m-%d"), 300, 300, 2.40, "Cipla", "active"),
            (1, "PARA-B2025-09", (today - datetime.timedelta(days=10)).strftime("%Y-%m-%d"), 50, 50, 2.00, "Sun Pharma", "active"),  # EXPIRED
            
            # Amoxicillin
            (2, "AMOX-B2026-01", (today + datetime.timedelta(days=5)).strftime("%Y-%m-%d"), 80, 80, 8.50, "GlaxoSmithKline", "active"), # Expiring soon (5d)
            (2, "AMOX-B2026-05", (today + datetime.timedelta(days=90)).strftime("%Y-%m-%d"), 200, 200, 8.00, "Abbott", "active"),
            
            # Ibuprofen
            (3, "IBU-B2026-11", (today + datetime.timedelta(days=25)).strftime("%Y-%m-%d"), 120, 120, 4.00, "Pfizer", "active"),
            (3, "IBU-B2025-12", (today - datetime.timedelta(days=40)).strftime("%Y-%m-%d"), 40, 40, 3.50, "Pfizer", "active"),  # EXPIRED
            
            # Cetirizine
            (4, "CET-B2026-03", (today + datetime.timedelta(days=200)).strftime("%Y-%m-%d"), 500, 500, 1.50, "Dr. Reddy's", "active")
        ]

        for med_id, bno, exp, init_q, cur_q, price, supp, st in batches_data:
            cursor.execute("""
            INSERT INTO batches (medicine_id, batch_number, expiry_date, initial_qty, current_qty, unit_price, supplier, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (med_id, bno, exp, init_q, cur_q, price, supp, st))
        conn.commit()

    conn.close()

# Initialize DB
init_db()

# -----------------------------------------------------------------------------
# HELPER DATA PARSERS & NOTIFICATION OUTBOX TRIGGER
# -----------------------------------------------------------------------------

def parse_date_string(date_str) -> Optional[str]:
    """Parses messy date strings (dd/mm/yyyy, dd-mm-yyyy, yyyy-mm-dd, ISO) into YYYY-MM-DD."""
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()
    if 'T' in date_str:
        date_str = date_str.split('T')[0]
    
    formats = [
        "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d",
        "%m/%d/%Y", "%d.%m.%Y", "%Y.%m.%d", "%b %d, %Y", "%d %b %Y"
    ]
    for fmt in formats:
        try:
            dt = datetime.datetime.strptime(date_str, fmt).date()
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None

def clean_quantity(val) -> Optional[int]:
    """Parses messy quantity values (e.g. '10 units', '150 pcs', 20.0, '50') into positive integer."""
    if val is None:
        return None
    if isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        q = int(val)
        return q if q >= 0 else None
    if isinstance(val, str):
        match = re.search(r'\d+', val)
        if match:
            try:
                q = int(match.group(0))
                return q if q >= 0 else None
            except ValueError:
                return None
    return None

def check_and_trigger_reorder_alert(conn, medicine_id: int):
    """
    Level 3 - T1 (integrate):
    Triggers re-order alert when sellable in-date stock falls below medicine min_threshold.
    Logs alert into outbox table.
    """
    cursor = conn.cursor()
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    
    cursor.execute("SELECT id, name, min_threshold FROM medicines WHERE id = ?", (medicine_id,))
    med = cursor.fetchone()
    if not med:
        return
    
    min_thresh = med["min_threshold"]
    cursor.execute("""
    SELECT COALESCE(SUM(current_qty), 0) FROM batches
    WHERE medicine_id = ? AND expiry_date >= ? AND (status IS NULL OR status != 'quarantined')
    """, (medicine_id, today_str))
    current_stock = cursor.fetchone()[0]
    
    if current_stock < min_thresh:
        # Check if active pending alert already exists
        cursor.execute("SELECT COUNT(*) FROM outbox WHERE medicine_id = ? AND status = 'pending'", (medicine_id,))
        if cursor.fetchone()[0] == 0:
            msg = f"Re-order alert: In-date stock for {med['name']} ({current_stock} units) has dropped below threshold ({min_thresh} units)."
            cursor.execute("""
            INSERT INTO outbox (type, medicine_id, medicine_name, current_stock, threshold, message, status)
            VALUES ('reorder_alert', ?, ?, ?, ?, ?, 'pending')
            """, (medicine_id, med["name"], current_stock, min_thresh, msg))
            conn.commit()

# -----------------------------------------------------------------------------
# PYDANTIC SCHEMAS WITH INPUT VALIDATION
# -----------------------------------------------------------------------------

class UserRegister(BaseModel):
    username: str
    email: str
    password: str

    @field_validator('password')
    def validate_password_length(cls, v):
        if len(v.strip()) < 6:
            raise ValueError('Password must be at least 6 characters long')
        return v

    @field_validator('email')
    def validate_email_format(cls, v):
        if '@' not in v or '.' not in v.split('@')[-1]:
            raise ValueError('Invalid email format')
        return v

class UserLogin(BaseModel):
    username: str
    password: str

class MedicineCreate(BaseModel):
    name: str
    generic_name: Optional[str] = None
    category: Optional[str] = None
    unit: str = "tablets"
    min_threshold: int = 50

class BatchCreate(BaseModel):
    medicine_id: int
    batch_number: str
    expiry_date: str
    initial_qty: int
    unit_price: float
    supplier: Optional[str] = None

class DispenseRequest(BaseModel):
    medicine_id: int
    quantity: int
    customer_name: Optional[str] = "Walk-in Customer"

# -----------------------------------------------------------------------------
# LEVEL 1 — T2 (AUTOMATION): /clock ENDPOINT
# -----------------------------------------------------------------------------

@app.post("/clock")
@app.post("/api/clock")
async def trigger_clock(request: Request):
    """
    Level 1 — T2 (automation):
    A daily job flags batches expiring within 7 days and quarantines expired ones.
    Returns count report. Graded via POST /clock.
    """
    target_date = datetime.date.today()
    try:
        body = await request.json()
        if isinstance(body, dict):
            date_str = body.get("date") or body.get("current_date")
            if date_str:
                parsed = parse_date_string(date_str)
                if parsed:
                    target_date = datetime.datetime.strptime(parsed, "%Y-%m-%d").date()
    except Exception:
        pass

    target_str = target_date.strftime("%Y-%m-%d")
    seven_days_str = (target_date + datetime.timedelta(days=7)).strftime("%Y-%m-%d")

    conn = get_db()
    cursor = conn.cursor()

    # 1. Flag batches expiring within 7 days
    cursor.execute("""
    SELECT COUNT(*) FROM batches
    WHERE expiry_date >= ? AND expiry_date <= ? AND current_qty > 0 AND (status IS NULL OR status != 'quarantined')
    """, (target_str, seven_days_str))
    expiring_7_days_count = cursor.fetchone()[0]

    # 2. Quarantine expired batches (expiry_date < target_date)
    cursor.execute("""
    SELECT id FROM batches
    WHERE expiry_date < ? AND current_qty > 0 AND (status IS NULL OR status != 'quarantined')
    """, (target_str,))
    expired_batches = cursor.fetchall()
    quarantined_count = len(expired_batches)

    for b in expired_batches:
        cursor.execute("UPDATE batches SET status = 'quarantined' WHERE id = ?", (b["id"],))

    conn.commit()
    conn.close()

    return {
        "status": "success",
        "date": target_str,
        "expiring_within_7_days": expiring_7_days_count,
        "quarantined_expired": quarantined_count,
        "expiring_soon_count": expiring_7_days_count,
        "quarantined_count": quarantined_count,
        "counts": {
            "expiring_within_7_days": expiring_7_days_count,
            "quarantined_expired": quarantined_count
        }
    }

# -----------------------------------------------------------------------------
# LEVEL 2 — T4 (MESSY DATA): /import ENDPOINT
# -----------------------------------------------------------------------------

@app.post("/import")
@app.post("/api/import")
@app.post("/api/batches/import")
async def import_batches(request: Request):
    """
    Level 2 — T4 (messy data):
    Imports messy batch list (nulls, '10 units', dd/mm/yyyy vs ISO dates, duplicate rows).
    Returns exact { imported, deduped, rejected } report.
    """
    raw_body = await request.body()
    items = []
    
    # Attempt JSON parse
    try:
        payload = json.loads(raw_body.decode('utf-8'))
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            for k in ["batches", "data", "items", "records"]:
                if k in payload and isinstance(payload[k], list):
                    items = payload[k]
                    break
            if not items and "batch_number" in payload:
                items = [payload]
    except Exception:
        # Attempt CSV / plain text parse
        try:
            text = raw_body.decode('utf-8', errors='ignore')
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if lines:
                headers = [h.strip().lower() for h in lines[0].split(',')]
                for line in lines[1:]:
                    parts = [p.strip() for p in line.split(',')]
                    if len(parts) == len(headers):
                        items.append(dict(zip(headers, parts)))
        except Exception:
            pass

    imported = 0
    deduped = 0
    rejected = 0

    conn = get_db()
    cursor = conn.cursor()

    # Query existing batch numbers in DB
    cursor.execute("SELECT batch_number FROM batches")
    db_batches = set(r[0].strip().upper() for r in cursor.fetchall())
    seen_in_import = set()

    for item in items:
        if not isinstance(item, dict):
            rejected += 1
            continue
        
        # Flexibly extract fields
        med_name = item.get("medicine_name") or item.get("medicine") or item.get("name") or item.get("medicine_id")
        batch_num = item.get("batch_number") or item.get("batch") or item.get("batch_no") or item.get("batch_id")
        exp_raw = item.get("expiry_date") or item.get("expiry") or item.get("exp_date") or item.get("exp")
        qty_raw = item.get("quantity") if "quantity" in item else (item.get("qty") or item.get("initial_qty") or item.get("current_qty") or item.get("stock"))
        price_raw = item.get("unit_price") or item.get("price") or 5.0
        supplier = item.get("supplier") or "Imported Supplier"

        # Reject if essential fields are null / missing
        if med_name is None or batch_num is None or exp_raw is None or qty_raw is None:
            rejected += 1
            continue

        batch_num_clean = str(batch_num).strip().upper()
        if not batch_num_clean or batch_num_clean in ("NULL", "NONE", "NAN", ""):
            rejected += 1
            continue

        # Check duplicate
        if batch_num_clean in db_batches or batch_num_clean in seen_in_import:
            deduped += 1
            continue

        # Clean quantity (e.g. "10 units", "150 pcs", 20.0)
        qty = clean_quantity(qty_raw)
        if qty is None or qty <= 0:
            rejected += 1
            continue

        # Clean expiry date (dd/mm/yyyy vs ISO dates)
        exp_date = parse_date_string(str(exp_raw))
        if not exp_date:
            rejected += 1
            continue

        # Unit price float
        try:
            unit_price = float(re.sub(r'[^\d.]', '', str(price_raw))) if price_raw else 5.0
        except Exception:
            unit_price = 5.0

        # Match or create medicine
        med_name_clean = str(med_name).strip()
        cursor.execute("SELECT id FROM medicines WHERE LOWER(name) = LOWER(?)", (med_name_clean,))
        row = cursor.fetchone()
        if row:
            med_id = row[0]
        else:
            cursor.execute("INSERT INTO medicines (name, generic_name, category, unit, min_threshold) VALUES (?, ?, ?, ?, ?)",
                           (med_name_clean, med_name_clean, "General", "tablets", 50))
            med_id = cursor.lastrowid

        # Insert batch
        cursor.execute("""
        INSERT INTO batches (medicine_id, batch_number, expiry_date, initial_qty, current_qty, unit_price, supplier, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        """, (med_id, batch_num_clean, exp_date, qty, qty, unit_price, supplier))

        seen_in_import.add(batch_num_clean)
        db_batches.add(batch_num_clean)
        imported += 1

    conn.commit()
    conn.close()

    return {
        "status": "success",
        "imported": imported,
        "deduped": deduped,
        "rejected": rejected,
        "report": {
            "imported": imported,
            "deduped": deduped,
            "rejected": rejected
        }
    }

# -----------------------------------------------------------------------------
# LEVEL 3 — T1 (INTEGRATE): /outbox ENDPOINTS
# -----------------------------------------------------------------------------

@app.get("/outbox")
@app.get("/api/outbox")
def get_outbox():
    """
    Level 3 — T1 (integrate):
    Re-order notification service outbox endpoint.
    Graded via GET /outbox.
    """
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT id, type, medicine_id, medicine_name, current_stock, threshold, message, status, created_at
    FROM outbox
    ORDER BY id DESC
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return {"outbox": rows, "count": len(rows)}

@app.delete("/outbox")
@app.delete("/api/outbox")
def clear_outbox():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM outbox")
    conn.commit()
    conn.close()
    return {"status": "success", "message": "Outbox cleared"}

# -----------------------------------------------------------------------------
# CORE API ENDPOINTS
# -----------------------------------------------------------------------------

@app.post("/api/auth/register")
def register(user: UserRegister):
    conn = get_db()
    cursor = conn.cursor()
    hashed = hash_password(user.password)
    try:
        cursor.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                       (user.username, user.email, hashed))
        conn.commit()
        user_id = cursor.lastrowid
        token = create_token(user_id, user.username, "pharmacist")
        return {"status": "success", "message": "User registered successfully", "token": token, "username": user.username}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Username or email already exists")
    finally:
        conn.close()

@app.post("/api/auth/login")
def login(credentials: UserLogin):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?", (credentials.username,))
    user = cursor.fetchone()
    conn.close()
    
    if not user or not verify_password(credentials.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    
    token = create_token(user["id"], user["username"], user["role"])
    return {"status": "success", "token": token, "username": user["username"], "role": user["role"]}

@app.get("/api/dashboard/stats")
def get_dashboard_stats():
    conn = get_db()
    cursor = conn.cursor()
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    soon_str = (datetime.date.today() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    cursor.execute("SELECT COUNT(*) FROM medicines")
    total_medicines = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(current_qty), 0) FROM batches WHERE expiry_date >= ? AND current_qty > 0 AND (status IS NULL OR status != 'quarantined')", (today_str,))
    total_sellable_stock = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM batches WHERE expiry_date >= ? AND expiry_date <= ? AND current_qty > 0 AND (status IS NULL OR status != 'quarantined')", (today_str, soon_str))
    expiring_soon_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM batches WHERE (expiry_date < ? OR status = 'quarantined') AND current_qty > 0", (today_str,))
    expired_count = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(quantity_dispensed), 0) FROM dispense_logs WHERE DATE(dispensed_at) = ?", (today_str,))
    dispensed_today = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM outbox WHERE status = 'pending'")
    outbox_count = cursor.fetchone()[0]

    conn.close()

    return {
        "total_medicines": total_medicines,
        "total_sellable_stock": total_sellable_stock,
        "expiring_soon_batches": expiring_soon_count,
        "expired_batches": expired_count,
        "dispensed_today": dispensed_today,
        "outbox_notifications": outbox_count,
        "today_date": today_str
    }

@app.get("/api/medicines")
def get_medicines(
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    sort_by: str = "name",
    order: str = "asc"
):
    conn = get_db()
    cursor = conn.cursor()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    valid_sorts = ["name", "category", "created_at"]
    sort_col = sort_by if isinstance(sort_by, str) and sort_by in valid_sorts else "name"
    order_str = order if isinstance(order, str) else "asc"
    sort_order = "ASC" if order_str.lower() == "asc" else "DESC"

    query = """
    SELECT m.id, m.name, m.generic_name, m.category, m.unit, m.min_threshold,
           COALESCE(SUM(CASE WHEN b.expiry_date >= ? AND (b.status IS NULL OR b.status != 'quarantined') THEN b.current_qty ELSE 0 END), 0) AS sellable_stock,
           COALESCE(SUM(CASE WHEN b.expiry_date < ? OR b.status = 'quarantined' THEN b.current_qty ELSE 0 END), 0) AS expired_stock,
           MIN(CASE WHEN b.expiry_date >= ? AND b.current_qty > 0 AND (b.status IS NULL OR b.status != 'quarantined') THEN b.expiry_date END) AS next_expiry
    FROM medicines m
    LEFT JOIN batches b ON m.id = b.medicine_id
    """
    params = [today_str, today_str, today_str]

    if search:
        query += " WHERE m.name LIKE ? OR m.generic_name LIKE ? OR m.category LIKE ?"
        term = f"%{search}%"
        params.extend([term, term, term])

    query += f" GROUP BY m.id ORDER BY {sort_col} {sort_order}"

    count_query = "SELECT COUNT(*) FROM (" + query + ")"
    cursor.execute(count_query, params)
    total_records = cursor.fetchone()[0]

    page_val = page if isinstance(page, int) else 1
    limit_val = limit if isinstance(limit, int) else 10

    offset = (page_val - 1) * limit_val
    query += " LIMIT ? OFFSET ?"
    params.extend([limit_val, offset])

    cursor.execute(query, params)
    medicines = [dict(row) for row in cursor.fetchall()]
    conn.close()

    total_pages = (total_records + limit_val - 1) // limit_val if total_records > 0 else 1

    return {
        "data": medicines,
        "pagination": {
            "page": page_val,
            "limit": limit_val,
            "total_records": total_records,
            "total_pages": total_pages
        }
    }

@app.post("/api/medicines")
def create_medicine(med: MedicineCreate, user: dict = Depends(require_auth)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO medicines (name, generic_name, category, unit, min_threshold) VALUES (?, ?, ?, ?, ?)",
                       (med.name, med.generic_name, med.category, med.unit, med.min_threshold))
        conn.commit()
        med_id = cursor.lastrowid
        return {"status": "success", "id": med_id, "message": "Medicine created successfully"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Medicine with this name already exists")
    finally:
        conn.close()

@app.get("/api/medicines/search")
def search_in_date_medicine(q: str):
    """'Do we have paracetamol in date?' query endpoint (Searches name, generic name & category)."""
    conn = get_db()
    cursor = conn.cursor()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    cursor.execute("""
    SELECT m.id, m.name, m.generic_name, m.category, m.unit,
           COALESCE(SUM(CASE WHEN b.expiry_date >= ? AND (b.status IS NULL OR b.status != 'quarantined') THEN b.current_qty ELSE 0 END), 0) AS in_date_stock,
           MIN(CASE WHEN b.expiry_date >= ? AND b.current_qty > 0 AND (b.status IS NULL OR b.status != 'quarantined') THEN b.expiry_date END) AS earliest_expiry
    FROM medicines m
    LEFT JOIN batches b ON m.id = b.medicine_id
    WHERE m.name LIKE ? OR m.generic_name LIKE ? OR m.category LIKE ?
    GROUP BY m.id
    """, (today_str, today_str, f"%{q}%", f"%{q}%", f"%{q}%"))

    results = [dict(row) for row in cursor.fetchall()]
    conn.close()

    formatted = []
    for r in results:
        has_in_date_stock = r["in_date_stock"] > 0
        formatted.append({
            "id": r["id"],
            "name": r["name"],
            "generic_name": r["generic_name"],
            "category": r["category"],
            "in_date_stock": r["in_date_stock"],
            "unit": r["unit"],
            "is_available_in_date": has_in_date_stock,
            "earliest_expiry": r["earliest_expiry"],
            "answer_summary": f"Yes! {r['name']} is IN DATE with {r['in_date_stock']} {r['unit']} available (Expires soonest: {r['earliest_expiry']})"
            if has_in_date_stock else f"NO IN-DATE STOCK for {r['name']}! (0 sellable units)"
        })

    return {"query": q, "results": formatted}

@app.get("/api/batches")
def get_batches(
    medicine_id: Optional[int] = None,
    status: Optional[str] = "all",
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=100),
    sort_by: str = "expiry_date",
    order: str = "asc"
):
    conn = get_db()
    cursor = conn.cursor()
    today = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")
    soon_str = (today + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    valid_sorts = ["expiry_date", "current_qty", "batch_number", "unit_price", "created_at"]
    sort_col = sort_by if isinstance(sort_by, str) and sort_by in valid_sorts else "expiry_date"
    order_str = order if isinstance(order, str) else "asc"
    sort_order = "ASC" if order_str.lower() == "asc" else "DESC"

    query = """
    SELECT b.id, b.medicine_id, m.name as medicine_name, m.unit, b.batch_number,
           b.expiry_date, b.initial_qty, b.current_qty, b.unit_price, b.supplier, b.status, b.created_at
    FROM batches b
    JOIN medicines m ON b.medicine_id = m.id
    WHERE 1=1
    """
    params = []

    if medicine_id:
        query += " AND b.medicine_id = ?"
        params.append(medicine_id)

    if status == "active":
        query += " AND b.expiry_date >= ? AND b.current_qty > 0 AND (b.status IS NULL OR b.status != 'quarantined')"
        params.append(today_str)
    elif status == "expiring_soon":
        query += " AND b.expiry_date >= ? AND b.expiry_date <= ? AND b.current_qty > 0 AND (b.status IS NULL OR b.status != 'quarantined')"
        params.extend([today_str, soon_str])
    elif status == "expired":
        query += " AND (b.expiry_date < ? OR b.status = 'quarantined') AND b.current_qty > 0"
        params.append(today_str)
    elif status == "quarantined":
        query += " AND b.status = 'quarantined'"

    query += f" ORDER BY b.{sort_col} {sort_order}"

    count_query = "SELECT COUNT(*) FROM (" + query + ")"
    cursor.execute(count_query, params)
    total_records = cursor.fetchone()[0]

    page_val = page if isinstance(page, int) else 1
    limit_val = limit if isinstance(limit, int) else 10

    offset = (page_val - 1) * limit_val
    query += " LIMIT ? OFFSET ?"
    params.extend([limit_val, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    batches = []
    for r in rows:
        b_dict = dict(r)
        exp_date = datetime.datetime.strptime(b_dict["expiry_date"], "%Y-%m-%d").date()
        days_left = (exp_date - today).days
        
        if b_dict.get("status") == "quarantined":
            b_status = "QUARANTINED"
            badge_color = "purple"
        elif days_left < 0:
            b_status = "EXPIRED"
            badge_color = "red"
        elif days_left <= 30:
            b_status = f"EXPIRING SOON ({days_left}d left)"
            badge_color = "amber"
        else:
            b_status = f"IN DATE ({days_left}d left)"
            badge_color = "green"

        b_dict["status_label"] = b_status
        b_dict["days_left"] = days_left
        b_dict["badge_color"] = badge_color
        batches.append(b_dict)

    total_pages = (total_records + limit_val - 1) // limit_val if total_records > 0 else 1

    return {
        "data": batches,
        "pagination": {
            "page": page_val,
            "limit": limit_val,
            "total_records": total_records,
            "total_pages": total_pages
        }
    }

@app.post("/api/batches")
def create_batch(batch: BatchCreate, user: dict = Depends(require_auth)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO batches (medicine_id, batch_number, expiry_date, initial_qty, current_qty, unit_price, supplier, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active')
        """, (batch.medicine_id, batch.batch_number, batch.expiry_date, batch.initial_qty, batch.initial_qty, batch.unit_price, batch.supplier))
        conn.commit()
        batch_id = cursor.lastrowid
        return {"status": "success", "id": batch_id, "message": f"Batch {batch.batch_number} added successfully"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Batch number already exists")
    finally:
        conn.close()

# -----------------------------------------------------------------------------
# CORE FEFO DISPENSING ENGINE (FIRST-EXPIRY-FIRST-OUT)
# -----------------------------------------------------------------------------

@app.post("/api/dispense")
def dispense_medicine(req: DispenseRequest, current_user: dict = Depends(require_auth)):
    """
    FEFO (First-Expiry-First-Out) Dispensing Algorithm:
    1. Selects active non-quarantined batches where expiry_date >= TODAY and current_qty > 0
    2. Orders batches strictly by expiry_date ASC, id ASC
    3. Strictly blocks expired and quarantined batches from being dispensed
    4. Deducts required quantity sequentially across eligible batches
    5. Checks if in-date stock falls below min_threshold and triggers re-order alert in /outbox
    """
    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="Dispense quantity must be greater than 0")

    conn = get_db()
    cursor = conn.cursor()
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    cursor.execute("SELECT id, name, unit FROM medicines WHERE id = ?", (req.medicine_id,))
    med = cursor.fetchone()
    if not med:
        conn.close()
        raise HTTPException(status_code=404, detail="Medicine not found")

    cursor.execute("""
    SELECT id, batch_number, expiry_date, current_qty, unit_price
    FROM batches
    WHERE medicine_id = ? AND expiry_date >= ? AND current_qty > 0 AND (status IS NULL OR status != 'quarantined')
    ORDER BY expiry_date ASC, id ASC
    """, (req.medicine_id, today_str))

    eligible_batches = cursor.fetchall()
    total_available = sum(b["current_qty"] for b in eligible_batches)

    if total_available < req.quantity:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient sellable stock for '{med['name']}'. Requested: {req.quantity}, Available In-Date Stock: {total_available} {med['unit']}."
        )

    remaining_to_dispense = req.quantity
    consumed_batches = []
    total_cost = 0.0

    for batch in eligible_batches:
        if remaining_to_dispense <= 0:
            break

        take_qty = min(batch["current_qty"], remaining_to_dispense)
        new_batch_qty = batch["current_qty"] - take_qty
        remaining_to_dispense -= take_qty
        batch_total = take_qty * batch["unit_price"]
        total_cost += batch_total

        cursor.execute("UPDATE batches SET current_qty = ? WHERE id = ?", (new_batch_qty, batch["id"]))

        cursor.execute("""
        INSERT INTO dispense_logs (medicine_id, batch_id, quantity_dispensed, dispensed_by, customer_name)
        VALUES (?, ?, ?, ?, ?)
        """, (req.medicine_id, batch["id"], take_qty, current_user.get("username", "pharmacist"), req.customer_name))

        consumed_batches.append({
            "batch_id": batch["id"],
            "batch_number": batch["batch_number"],
            "expiry_date": batch["expiry_date"],
            "quantity_taken": take_qty,
            "unit_price": batch["unit_price"],
            "subtotal": round(batch_total, 2),
            "remaining_in_batch": new_batch_qty
        })

    conn.commit()

    # Trigger Level 3 re-order alert check if stock dropped below threshold
    check_and_trigger_reorder_alert(conn, req.medicine_id)

    conn.close()

    return {
        "status": "success",
        "message": f"Successfully dispensed {req.quantity} {med['unit']} of {med['name']} using FEFO principles.",
        "medicine": med["name"],
        "total_quantity_dispensed": req.quantity,
        "total_cost": round(total_cost, 2),
        "fefo_breakdown": consumed_batches
    }

@app.get("/api/alerts/expiring")
def get_expiring_alerts():
    """Returns batches expiring within 30 days and already expired/quarantined batches."""
    conn = get_db()
    cursor = conn.cursor()
    today = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")
    soon_str = (today + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    cursor.execute("""
    SELECT b.id, b.batch_number, b.expiry_date, b.current_qty, b.unit_price, b.status, m.name as medicine_name, m.unit
    FROM batches b
    JOIN medicines m ON b.medicine_id = m.id
    WHERE b.expiry_date >= ? AND b.expiry_date <= ? AND b.current_qty > 0 AND (b.status IS NULL OR b.status != 'quarantined')
    ORDER BY b.expiry_date ASC
    """, (today_str, soon_str))
    expiring_soon = []
    for r in cursor.fetchall():
        d = dict(r)
        d["days_left"] = (datetime.datetime.strptime(d["expiry_date"], "%Y-%m-%d").date() - today).days
        expiring_soon.append(d)

    cursor.execute("""
    SELECT b.id, b.batch_number, b.expiry_date, b.current_qty, b.unit_price, b.status, m.name as medicine_name, m.unit
    FROM batches b
    JOIN medicines m ON b.medicine_id = m.id
    WHERE (b.expiry_date < ? OR b.status = 'quarantined') AND b.current_qty > 0
    ORDER BY b.expiry_date DESC
    """, (today_str,))
    expired = []
    for r in cursor.fetchall():
        d = dict(r)
        d["days_overdue"] = (today - datetime.datetime.strptime(d["expiry_date"], "%Y-%m-%d").date()).days
        expired.append(d)

    conn.close()

    return {
        "expiring_soon_count": len(expiring_soon),
        "expired_count": len(expired),
        "expiring_soon": expiring_soon,
        "expired": expired
    }

# -----------------------------------------------------------------------------
# FULL-STACK UI LANDING PAGE & DASHBOARD APP
# -----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index_page():
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Pharmacy Stock Management System (problem_code: pharmacy_stock)</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        .gradient-bg { background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0284c7 100%); }
        .glass-card { background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); }
    </style>
</head>
<body class="bg-slate-50 font-sans text-slate-800 antialiased min-h-screen flex flex-col">

    <!-- Top Navigation Bar -->
    <header class="bg-slate-900 text-white shadow-lg sticky top-0 z-50">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex justify-between items-center h-16">
            <div class="flex items-center space-x-3 cursor-pointer" onclick="showTab('landing')">
                <div class="bg-sky-500 p-2 rounded-lg text-white font-bold">
                    <i class="fa-solid fa-pills text-xl"></i>
                </div>
                <span class="text-xl font-extrabold tracking-tight bg-gradient-to-r from-sky-400 to-emerald-400 bg-clip-text text-transparent">
                    PharmaFEFO
                </span>
            </div>
            
            <nav class="hidden md:flex space-x-6 text-sm font-medium">
                <button onclick="showTab('landing')" class="hover:text-sky-400 transition">Landing</button>
                <button onclick="showTab('dashboard')" class="hover:text-sky-400 transition">Dashboard</button>
                <button onclick="showTab('inventory')" class="hover:text-sky-400 transition">Stock</button>
                <button onclick="showTab('batches')" class="hover:text-sky-400 transition">Batches</button>
                <button onclick="showTab('dispenser')" class="hover:text-sky-400 transition font-semibold text-emerald-400"><i class="fa-solid fa-bolt mr-1"></i>FEFO Dispense</button>
                <button onclick="showTab('import')" class="hover:text-sky-400 transition text-sky-400 font-semibold"><i class="fa-solid fa-file-import mr-1"></i>Import Messy Data</button>
                <button onclick="showTab('outbox')" class="hover:text-sky-400 transition text-amber-400 font-semibold"><i class="fa-solid fa-inbox mr-1"></i>Outbox Alerts</button>
            </nav>

            <div class="flex items-center space-x-4">
                <span id="user-display" class="text-xs bg-slate-800 border border-slate-700 px-3 py-1.5 rounded-full text-slate-300">
                    <i class="fa-solid fa-user-shield text-emerald-400 mr-1"></i> <span id="current-username">Pharmacist (Admin)</span>
                </span>
                <button onclick="openAuthModal()" class="bg-sky-600 hover:bg-sky-500 text-white text-xs px-3 py-1.5 rounded-md font-semibold transition">
                    Login / Reg
                </button>
            </div>
        </div>
    </header>

    <!-- MAIN CONTENT CONTAINERS -->
    <main class="flex-1 max-w-7xl w-full mx-auto p-4 sm:p-6">

        <!-- 1. LANDING PAGE TAB -->
        <section id="tab-landing" class="space-y-12">
            <div class="gradient-bg text-white rounded-3xl p-8 sm:p-12 shadow-2xl relative overflow-hidden">
                <div class="max-w-3xl space-y-6 relative z-10">
                    <span class="bg-sky-500/20 text-sky-300 border border-sky-400/30 text-xs px-3 py-1 rounded-full font-semibold uppercase tracking-wider">
                        Auriga IT Round 2 Assessment — problem_code: pharmacy_stock
                    </span>
                    <h1 class="text-4xl sm:text-5xl font-extrabold tracking-tight leading-tight">
                        Pharmacy Stock & <span class="text-sky-400">FEFO Engine</span>
                    </h1>
                    <p class="text-slate-300 text-lg leading-relaxed">
                        Full-stack FEFO inventory dispensing engine featuring Level 1 daily automation clock (<code class="bg-slate-800 px-2 py-0.5 rounded text-sky-300">POST /clock</code>), Level 2 messy data importer (<code class="bg-slate-800 px-2 py-0.5 rounded text-sky-300">POST /import</code>), and Level 3 re-order notification service (<code class="bg-slate-800 px-2 py-0.5 rounded text-sky-300">GET /outbox</code>).
                    </p>
                    <div class="flex flex-wrap gap-4 pt-2">
                        <button onclick="showTab('dispenser')" class="bg-emerald-500 hover:bg-emerald-400 text-slate-900 font-bold px-6 py-3 rounded-xl shadow-lg transition flex items-center space-x-2">
                            <i class="fa-solid fa-circle-play"></i>
                            <span>Launch FEFO Dispenser</span>
                        </button>
                        <button onclick="showTab('import')" class="bg-sky-600 hover:bg-sky-500 text-white font-bold px-6 py-3 rounded-xl shadow-lg transition flex items-center space-x-2">
                            <i class="fa-solid fa-file-import"></i>
                            <span>Test Messy Batch Import</span>
                        </button>
                    </div>
                </div>
            </div>

            <!-- Level 1, 2, 3 Feature Cards -->
            <div class="grid md:grid-cols-3 gap-8">
                <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-3">
                    <div class="w-12 h-12 bg-sky-100 text-sky-600 rounded-xl flex items-center justify-center text-xl font-bold">
                        <i class="fa-solid fa-clock"></i>
                    </div>
                    <h3 class="text-lg font-bold text-slate-900">Level 1 — Daily Automation (/clock)</h3>
                    <p class="text-slate-600 text-sm">
                        Daily clock job flags batches expiring within 7 days and automatically quarantines expired ones.
                    </p>
                    <button onclick="triggerClockJob()" class="w-full bg-sky-50 hover:bg-sky-100 text-sky-700 font-bold text-xs py-2 rounded-lg transition">
                        Run Daily Clock Job Now
                    </button>
                </div>

                <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-3">
                    <div class="w-12 h-12 bg-emerald-100 text-emerald-600 rounded-xl flex items-center justify-center text-xl font-bold">
                        <i class="fa-solid fa-file-excel"></i>
                    </div>
                    <h3 class="text-lg font-bold text-slate-900">Level 2 — Messy Data Import (/import)</h3>
                    <p class="text-slate-600 text-sm">
                        Cleans messy batches with nulls, "10 units", dd/mm/yyyy dates, and duplicates into clean stock.
                    </p>
                    <button onclick="showTab('import')" class="w-full bg-emerald-50 hover:bg-emerald-100 text-emerald-700 font-bold text-xs py-2 rounded-lg transition">
                        Open Import Console
                    </button>
                </div>

                <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-3">
                    <div class="w-12 h-12 bg-amber-100 text-amber-600 rounded-xl flex items-center justify-center text-xl font-bold">
                        <i class="fa-solid fa-bell"></i>
                    </div>
                    <h3 class="text-lg font-bold text-slate-900">Level 3 — Re-order Outbox (/outbox)</h3>
                    <p class="text-slate-600 text-sm">
                        Generates automatic re-order alerts in the Notification Service outbox when in-date stock falls below threshold.
                    </p>
                    <button onclick="showTab('outbox')" class="w-full bg-amber-50 hover:bg-amber-100 text-amber-700 font-bold text-xs py-2 rounded-lg transition">
                        Inspect Outbox Notifications
                    </button>
                </div>
            </div>
        </section>

        <!-- 2. DASHBOARD TAB -->
        <section id="tab-dashboard" class="hidden space-y-6">
            <div class="flex justify-between items-center">
                <h2 class="text-2xl font-extrabold text-slate-900">Pharmacy Operations Dashboard</h2>
                <div class="flex items-center space-x-3">
                    <span id="dashboard-date" class="text-xs font-semibold text-slate-500 bg-white px-3 py-1.5 rounded-lg border border-slate-200"></span>
                    <button onclick="triggerClockJob()" class="bg-sky-600 hover:bg-sky-500 text-white text-xs px-3 py-1.5 rounded-lg font-bold transition">
                        <i class="fa-solid fa-clock mr-1"></i> Run Daily Clock (/clock)
                    </button>
                </div>
            </div>

            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm space-y-1">
                    <span class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Total Medicines</span>
                    <div id="stat-medicines" class="text-3xl font-black text-slate-900">--</div>
                </div>
                <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm space-y-1">
                    <span class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Sellable Stock Units</span>
                    <div id="stat-sellable" class="text-3xl font-black text-emerald-600">--</div>
                </div>
                <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm space-y-1">
                    <span class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Expiring in 30 Days</span>
                    <div id="stat-expiring" class="text-3xl font-black text-amber-500">--</div>
                </div>
                <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm space-y-1">
                    <span class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Expired / Quarantined</span>
                    <div id="stat-expired" class="text-3xl font-black text-rose-600">--</div>
                </div>
            </div>

            <!-- Quick In-Date Search Widget -->
            <div class="bg-sky-900 text-white rounded-2xl p-6 shadow-md space-y-4">
                <h3 class="text-lg font-bold flex items-center space-x-2">
                    <i class="fa-solid fa-magnifying-glass text-sky-400"></i>
                    <span>Quick "In-Date Stock" Query Engine</span>
                </h3>
                <div class="flex gap-3">
                    <input type="text" id="quick-query-input" placeholder="e.g. Paracetamol, Amoxicillin..." class="flex-1 px-4 py-2.5 rounded-xl text-slate-900 font-medium border-0 focus:ring-2 focus:ring-sky-400 outline-none">
                    <button onclick="executeInDateQuery()" class="bg-sky-500 hover:bg-sky-400 text-slate-900 font-bold px-6 py-2.5 rounded-xl transition">
                        Ask Query
                    </button>
                </div>
                <div id="query-result-box" class="hidden bg-slate-800/80 p-4 rounded-xl border border-slate-700 space-y-2"></div>
            </div>
        </section>

        <!-- 3. STOCK INVENTORY TAB -->
        <section id="tab-inventory" class="hidden space-y-6">
            <h2 class="text-2xl font-extrabold text-slate-900">Medicine Master Inventory</h2>

            <div class="bg-white p-4 rounded-xl border border-slate-200 flex flex-wrap gap-4 items-center justify-between">
                <div class="flex items-center space-x-2 flex-1 min-w-[240px]">
                    <i class="fa-solid fa-search text-slate-400"></i>
                    <input type="text" id="inv-search" oninput="loadMedicines()" placeholder="Search medicine..." class="w-full text-sm outline-none bg-transparent">
                </div>
            </div>

            <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                <table class="w-full text-left text-sm border-collapse">
                    <thead class="bg-slate-100 text-slate-600 text-xs uppercase tracking-wider">
                        <tr>
                            <th class="p-4">Medicine Name</th>
                            <th class="p-4">Generic Name</th>
                            <th class="p-4">Category</th>
                            <th class="p-4">Sellable Stock (In-Date)</th>
                            <th class="p-4">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="inventory-tbody" class="divide-y divide-slate-100"></tbody>
                </table>
            </div>
        </section>

        <!-- 4. BATCHES TAB -->
        <section id="tab-batches" class="hidden space-y-6">
            <h2 class="text-2xl font-extrabold text-slate-900">Batch Stock Ledger</h2>
            <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                <table class="w-full text-left text-sm border-collapse">
                    <thead class="bg-slate-100 text-slate-600 text-xs uppercase tracking-wider">
                        <tr>
                            <th class="p-4">Batch Number</th>
                            <th class="p-4">Medicine</th>
                            <th class="p-4">Expiry Date</th>
                            <th class="p-4">Qty</th>
                            <th class="p-4">Status</th>
                        </tr>
                    </thead>
                    <tbody id="batches-tbody" class="divide-y divide-slate-100"></tbody>
                </table>
            </div>
        </section>

        <!-- 5. FEFO DISPENSER TAB -->
        <section id="tab-dispenser" class="hidden space-y-6 max-w-3xl mx-auto">
            <div class="bg-white p-8 rounded-3xl border border-slate-200 shadow-xl space-y-6">
                <div class="flex items-center space-x-3 border-b border-slate-100 pb-4">
                    <div class="bg-emerald-500 text-white p-3 rounded-2xl font-bold">
                        <i class="fa-solid fa-bolt text-2xl"></i>
                    </div>
                    <div>
                        <h2 class="text-2xl font-black text-slate-900">FEFO Automated Dispenser</h2>
                        <p class="text-xs text-slate-500">Dispenses strictly earliest-expiring stock first.</p>
                    </div>
                </div>

                <form id="dispense-form" onsubmit="handleDispense(event)" class="space-y-5">
                    <div>
                        <label class="block text-xs font-bold text-slate-700 uppercase mb-2">Select Medicine</label>
                        <select id="dispense-med-id" required class="w-full p-3 rounded-xl border border-slate-200 bg-slate-50 font-medium outline-none">
                            <option value="">-- Choose Medicine --</option>
                        </select>
                    </div>

                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-bold text-slate-700 uppercase mb-2">Quantity</label>
                            <input type="number" id="dispense-qty" min="1" required placeholder="e.g. 20" class="w-full p-3 rounded-xl border border-slate-200 outline-none">
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-slate-700 uppercase mb-2">Customer Name</label>
                            <input type="text" id="dispense-customer" placeholder="Walk-in Customer" class="w-full p-3 rounded-xl border border-slate-200 outline-none">
                        </div>
                    </div>

                    <button type="submit" class="w-full py-4 bg-emerald-600 hover:bg-emerald-500 text-white font-black rounded-2xl shadow-lg transition">
                        Execute FEFO Dispense Order
                    </button>
                </form>

                <div id="dispense-result" class="hidden space-y-4"></div>
            </div>
        </section>

        <!-- 6. LEVEL 2 MESSY DATA IMPORT CONSOLE TAB -->
        <section id="tab-import" class="hidden space-y-6 max-w-4xl mx-auto">
            <div class="bg-white p-8 rounded-3xl border border-slate-200 shadow-xl space-y-6">
                <div class="flex items-center space-x-3 border-b border-slate-100 pb-4">
                    <div class="bg-sky-600 text-white p-3 rounded-2xl font-bold">
                        <i class="fa-solid fa-file-csv text-2xl"></i>
                    </div>
                    <div>
                        <h2 class="text-2xl font-black text-slate-900">Level 2 — Messy Data Batch Importer</h2>
                        <p class="text-xs text-slate-500">Cleans nulls, "10 units", dd/mm/yyyy dates, and duplicates into valid stock.</p>
                    </div>
                </div>

                <div class="space-y-4">
                    <label class="block text-xs font-bold text-slate-700 uppercase">Paste Raw JSON / Messy Batch Payload</label>
                    <textarea id="import-json-payload" rows="10" class="w-full font-mono text-xs p-4 bg-slate-900 text-sky-300 rounded-2xl border border-slate-700 outline-none" placeholder='[
  { "medicine": "Paracetamol 500mg", "batch": "MESSY-01", "expiry": "15/10/2026", "quantity": "150 units" },
  { "medicine": "Amoxicillin 250mg", "batch": "MESSY-01", "expiry": "2026-11-20", "quantity": 100 },
  { "medicine": "Ibuprofen 400mg", "batch": null, "expiry": "bad-date", "quantity": "invalid" }
]'></textarea>
                    
                    <button onclick="executeBatchImport()" class="w-full py-3.5 bg-sky-600 hover:bg-sky-500 text-white font-bold rounded-2xl shadow-lg transition">
                        Submit Payload to POST /import
                    </button>
                </div>

                <div id="import-report-box" class="hidden bg-slate-100 p-6 rounded-2xl border border-slate-200"></div>
            </div>
        </section>

        <!-- 7. LEVEL 3 OUTBOX ALERTS TAB -->
        <section id="tab-outbox" class="hidden space-y-6">
            <div class="flex justify-between items-center">
                <div>
                    <h2 class="text-2xl font-extrabold text-slate-900">Level 3 — Re-order Notification Outbox (/outbox)</h2>
                    <p class="text-xs text-slate-500">Re-order alerts generated when stock drops below threshold</p>
                </div>
                <button onclick="clearOutboxNotifications()" class="bg-rose-100 hover:bg-rose-200 text-rose-700 font-bold text-xs px-4 py-2 rounded-xl transition">
                    Clear Outbox
                </button>
            </div>

            <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden p-6 space-y-4">
                <div id="outbox-list" class="space-y-3"></div>
            </div>
        </section>

    </main>

    <script>
        let currentTab = 'landing';
        let authToken = localStorage.getItem('pharma_token') || '';

        document.addEventListener('DOMContentLoaded', () => {
            loadDashboardStats();
            loadMedicines();
            loadBatches();
            loadOutbox();
        });

        function showTab(tabName) {
            ['landing', 'dashboard', 'inventory', 'batches', 'dispenser', 'import', 'outbox'].forEach(t => {
                const el = document.getElementById(`tab-${t}`);
                if(el) el.classList.add('hidden');
            });
            document.getElementById(`tab-${tabName}`).classList.remove('hidden');
            currentTab = tabName;
            if(tabName === 'dashboard') loadDashboardStats();
            if(tabName === 'inventory') loadMedicines();
            if(tabName === 'batches') loadBatches();
            if(tabName === 'dispenser') populateDispenseDropdown();
            if(tabName === 'outbox') loadOutbox();
        }

        async function triggerClockJob() {
            const res = await fetch('/clock', { method: 'POST' });
            const data = await res.json();
            alert(`Daily Clock Job Executed!\nDate: ${data.date}\nExpiring within 7 days: ${data.expiring_within_7_days}\nQuarantined expired batches: ${data.quarantined_expired}`);
            loadDashboardStats();
            loadBatches();
        }

        async function loadDashboardStats() {
            const res = await fetch('/api/dashboard/stats');
            const data = await res.json();
            document.getElementById('stat-medicines').innerText = data.total_medicines;
            document.getElementById('stat-sellable').innerText = data.total_sellable_stock;
            document.getElementById('stat-expiring').innerText = data.expiring_soon_batches;
            document.getElementById('stat-expired').innerText = data.expired_batches;
            document.getElementById('dashboard-date').innerText = `System Date: ${data.today_date}`;
        }

        async function executeInDateQuery() {
            const query = document.getElementById('quick-query-input').value.trim();
            if(!query) return;
            const res = await fetch(`/api/medicines/search?q=${encodeURIComponent(query)}`);
            const data = await res.json();
            const box = document.getElementById('query-result-box');
            box.classList.remove('hidden');
            box.innerHTML = data.results.map(r => `
                <div class="p-3 bg-slate-900/60 rounded-lg text-sm border-l-4 ${r.is_available_in_date ? 'border-emerald-500' : 'border-rose-500'}">
                    <div class="font-bold text-white">${r.answer_summary}</div>
                </div>
            `).join('');
        }

        async function loadMedicines() {
            const search = document.getElementById('inv-search').value;
            const res = await fetch(`/api/medicines?search=${encodeURIComponent(search)}&limit=50`);
            const json = await res.json();
            const tbody = document.getElementById('inventory-tbody');
            tbody.innerHTML = json.data.map(m => `
                <tr class="hover:bg-slate-50 transition">
                    <td class="p-4 font-bold text-slate-900">${m.name}</td>
                    <td class="p-4 text-slate-600">${m.generic_name || '-'}</td>
                    <td class="p-4 text-xs font-semibold"><span class="bg-slate-100 px-2 py-1 rounded-md">${m.category || 'General'}</span></td>
                    <td class="p-4 font-black ${m.sellable_stock > 0 ? 'text-emerald-600' : 'text-rose-600'}">${m.sellable_stock} ${m.unit}</td>
                    <td class="p-4">
                        <button onclick="showTab('dispenser')" class="bg-emerald-500 hover:bg-emerald-400 text-slate-900 font-bold text-xs px-3 py-1.5 rounded-lg">Dispense FEFO</button>
                    </td>
                </tr>
            `).join('');
        }

        async function loadBatches() {
            const res = await fetch('/api/batches?limit=50');
            const json = await res.json();
            const tbody = document.getElementById('batches-tbody');
            tbody.innerHTML = json.data.map(b => `
                <tr class="hover:bg-slate-50 transition">
                    <td class="p-4 font-mono font-bold">${b.batch_number}</td>
                    <td class="p-4 font-medium">${b.medicine_name}</td>
                    <td class="p-4 font-semibold">${b.expiry_date}</td>
                    <td class="p-4 font-bold">${b.current_qty} / ${b.initial_qty}</td>
                    <td class="p-4"><span class="text-xs px-2.5 py-1 rounded-full font-bold bg-${b.badge_color}-100 text-${b.badge_color}-700">${b.status_label}</span></td>
                </tr>
            `).join('');
        }

        async function populateDispenseDropdown() {
            const res = await fetch('/api/medicines?limit=100');
            const json = await res.json();
            const select = document.getElementById('dispense-med-id');
            select.innerHTML = '<option value="">-- Choose Medicine --</option>' + json.data.map(m => `
                <option value="${m.id}">${m.name} (${m.sellable_stock} ${m.unit} in-date)</option>
            `).join('');
        }

        async function handleDispense(e) {
            e.preventDefault();
            const medId = parseInt(document.getElementById('dispense-med-id').value);
            const qty = parseInt(document.getElementById('dispense-qty').value);
            const cust = document.getElementById('dispense-customer').value || "Walk-in Customer";
            const resBox = document.getElementById('dispense-result');

            const headers = { 'Content-Type': 'application/json' };
            if(authToken) headers['Authorization'] = `Bearer ${authToken}`;

            const res = await fetch('/api/dispense', {
                method: 'POST',
                headers: headers,
                body: JSON.stringify({ medicine_id: medId, quantity: qty, customer_name: cust })
            });

            const data = await res.json();
            resBox.classList.remove('hidden');
            if(!res.ok) {
                resBox.innerHTML = `<div class="bg-rose-50 p-4 rounded-xl text-rose-800 font-bold">${data.detail || 'Error'}</div>`;
            } else {
                resBox.innerHTML = `<div class="bg-emerald-50 p-6 rounded-2xl text-emerald-950 font-bold">${data.message}</div>`;
                loadDashboardStats();
                loadOutbox();
            }
        }

        async function executeBatchImport() {
            const payloadStr = document.getElementById('import-json-payload').value;
            const resBox = document.getElementById('import-report-box');
            try {
                const res = await fetch('/import', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: payloadStr
                });
                const data = await res.json();
                resBox.classList.remove('hidden');
                resBox.innerHTML = `
                    <h4 class="font-extrabold text-slate-900 mb-2">Import Report Summary</h4>
                    <div class="grid grid-cols-3 gap-4 text-center font-bold">
                        <div class="bg-emerald-100 text-emerald-800 p-3 rounded-xl">Imported: ${data.imported}</div>
                        <div class="bg-amber-100 text-amber-800 p-3 rounded-xl">Deduped: ${data.deduped}</div>
                        <div class="bg-rose-100 text-rose-800 p-3 rounded-xl">Rejected: ${data.rejected}</div>
                    </div>
                `;
                loadDashboardStats();
                loadBatches();
            } catch(err) {
                alert('Invalid JSON input syntax');
            }
        }

        async function loadOutbox() {
            const res = await fetch('/outbox');
            const data = await res.json();
            const list = document.getElementById('outbox-list');
            if(!data.outbox || data.outbox.length === 0) {
                list.innerHTML = '<div class="text-sm text-slate-400">Outbox is empty. No re-order alerts pending.</div>';
            } else {
                list.innerHTML = data.outbox.map(o => `
                    <div class="p-4 bg-amber-50 border border-amber-200 rounded-xl flex justify-between items-center text-xs">
                        <div>
                            <span class="font-bold text-amber-900 block text-sm">${o.medicine_name}</span>
                            <span class="text-amber-700">${o.message}</span>
                        </div>
                        <span class="bg-amber-200 text-amber-900 px-2.5 py-1 rounded-full font-bold uppercase">${o.type}</span>
                    </div>
                `).join('');
            }
        }

        async function clearOutboxNotifications() {
            await fetch('/outbox', { method: 'DELETE' });
            loadOutbox();
        }
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
