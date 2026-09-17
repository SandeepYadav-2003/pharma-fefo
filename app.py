import os
import sqlite3
import datetime
import hashlib
import hmac
import json
import base64
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
    title="Pharma FEFO Inventory & Dispensing Engine",
    description="Full-stack Pharmacy Stock Management with First-Expiry-First-Out (FEFO) dispensing logic.",
    version="1.0.0"
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
# DATABASE SETUP & SEEDING
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
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (medicine_id) REFERENCES medicines(id)
    );
    """)

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
            (1, "PARA-B2026-01", (today + datetime.timedelta(days=15)).strftime("%Y-%m-%d"), 150, 150, 2.50, "Sun Pharma"),  # Expiring soon (15d)
            (1, "PARA-B2026-02", (today + datetime.timedelta(days=180)).strftime("%Y-%m-%d"), 300, 300, 2.40, "Cipla"),        # Long active (180d)
            (1, "PARA-B2025-09", (today - datetime.timedelta(days=10)).strftime("%Y-%m-%d"), 50, 50, 2.00, "Sun Pharma"),    # EXPIRED (-10d)
            
            # Amoxicillin
            (2, "AMOX-B2026-01", (today + datetime.timedelta(days=5)).strftime("%Y-%m-%d"), 80, 80, 8.50, "GlaxoSmithKline"), # Expiring soon (5d)
            (2, "AMOX-B2026-05", (today + datetime.timedelta(days=90)).strftime("%Y-%m-%d"), 200, 200, 8.00, "Abbott"),      # Active (90d)
            
            # Ibuprofen
            (3, "IBU-B2026-11", (today + datetime.timedelta(days=25)).strftime("%Y-%m-%d"), 120, 120, 4.00, "Pfizer"),       # Expiring soon (25d)
            (3, "IBU-B2025-12", (today - datetime.timedelta(days=40)).strftime("%Y-%m-%d"), 40, 40, 3.50, "Pfizer"),        # EXPIRED (-40d)
            
            # Cetirizine
            (4, "CET-B2026-03", (today + datetime.timedelta(days=200)).strftime("%Y-%m-%d"), 500, 500, 1.50, "Dr. Reddy's")  # Active (200d)
        ]

        for med_id, bno, exp, init_q, cur_q, price, supp in batches_data:
            cursor.execute("INSERT INTO batches (medicine_id, batch_number, expiry_date, initial_qty, current_qty, unit_price, supplier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (med_id, bno, exp, init_q, cur_q, price, supp))
        conn.commit()

    conn.close()

# Initialize DB
init_db()

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
# API ENDPOINTS
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

    cursor.execute("SELECT COALESCE(SUM(current_qty), 0) FROM batches WHERE expiry_date >= ? AND current_qty > 0", (today_str,))
    total_sellable_stock = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM batches WHERE expiry_date >= ? AND expiry_date <= ? AND current_qty > 0", (today_str, soon_str))
    expiring_soon_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM batches WHERE expiry_date < ? AND current_qty > 0", (today_str,))
    expired_count = cursor.fetchone()[0]

    cursor.execute("SELECT COALESCE(SUM(quantity_dispensed), 0) FROM dispense_logs WHERE DATE(dispensed_at) = ?", (today_str,))
    dispensed_today = cursor.fetchone()[0]

    conn.close()

    return {
        "total_medicines": total_medicines,
        "total_sellable_stock": total_sellable_stock,
        "expiring_soon_batches": expiring_soon_count,
        "expired_batches": expired_count,
        "dispensed_today": dispensed_today,
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
           COALESCE(SUM(CASE WHEN b.expiry_date >= ? THEN b.current_qty ELSE 0 END), 0) AS sellable_stock,
           COALESCE(SUM(CASE WHEN b.expiry_date < ? THEN b.current_qty ELSE 0 END), 0) AS expired_stock,
           MIN(CASE WHEN b.expiry_date >= ? AND b.current_qty > 0 THEN b.expiry_date END) AS next_expiry
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

    offset = (page - 1) * limit
    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    medicines = [dict(row) for row in cursor.fetchall()]
    conn.close()

    total_pages = (total_records + limit - 1) // limit if total_records > 0 else 1

    return {
        "data": medicines,
        "pagination": {
            "page": page,
            "limit": limit,
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
           COALESCE(SUM(CASE WHEN b.expiry_date >= ? THEN b.current_qty ELSE 0 END), 0) AS in_date_stock,
           MIN(CASE WHEN b.expiry_date >= ? AND b.current_qty > 0 THEN b.expiry_date END) AS earliest_expiry
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
           b.expiry_date, b.initial_qty, b.current_qty, b.unit_price, b.supplier, b.created_at
    FROM batches b
    JOIN medicines m ON b.medicine_id = m.id
    WHERE 1=1
    """
    params = []

    if medicine_id:
        query += " AND b.medicine_id = ?"
        params.append(medicine_id)

    if status == "active":
        query += " AND b.expiry_date >= ? AND b.current_qty > 0"
        params.append(today_str)
    elif status == "expiring_soon":
        query += " AND b.expiry_date >= ? AND b.expiry_date <= ? AND b.current_qty > 0"
        params.extend([today_str, soon_str])
    elif status == "expired":
        query += " AND b.expiry_date < ? AND b.current_qty > 0"
        params.append(today_str)

    query += f" ORDER BY b.{sort_col} {sort_order}"

    count_query = "SELECT COUNT(*) FROM (" + query + ")"
    cursor.execute(count_query, params)
    total_records = cursor.fetchone()[0]

    offset = (page - 1) * limit
    query += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    batches = []
    for r in rows:
        b_dict = dict(r)
        exp_date = datetime.datetime.strptime(b_dict["expiry_date"], "%Y-%m-%d").date()
        days_left = (exp_date - today).days
        
        if days_left < 0:
            b_status = "EXPIRED"
            badge_color = "red"
        elif days_left <= 30:
            b_status = f"EXPIRING SOON ({days_left}d left)"
            badge_color = "amber"
        else:
            b_status = f"IN DATE ({days_left}d left)"
            badge_color = "green"

        b_dict["status"] = b_status
        b_dict["days_left"] = days_left
        b_dict["badge_color"] = badge_color
        batches.append(b_dict)

    total_pages = (total_records + limit - 1) // limit if total_records > 0 else 1

    return {
        "data": batches,
        "pagination": {
            "page": page,
            "limit": limit,
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
        INSERT INTO batches (medicine_id, batch_number, expiry_date, initial_qty, current_qty, unit_price, supplier)
        VALUES (?, ?, ?, ?, ?, ?, ?)
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
    1. Selects all active batches for medicine_id where expiry_date >= TODAY and current_qty > 0
    2. Orders batches strictly by expiry_date ASC, id ASC
    3. Strictly blocks expired batches from being dispensed
    4. Deducts required quantity sequentially across eligible batches
    5. Logs detailed audit record for each consumed batch
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
    WHERE medicine_id = ? AND expiry_date >= ? AND current_qty > 0
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
    """Returns batches expiring within 30 days and already expired batches."""
    conn = get_db()
    cursor = conn.cursor()
    today = datetime.date.today()
    today_str = today.strftime("%Y-%m-%d")
    soon_str = (today + datetime.timedelta(days=30)).strftime("%Y-%m-%d")

    cursor.execute("""
    SELECT b.id, b.batch_number, b.expiry_date, b.current_qty, b.unit_price, m.name as medicine_name, m.unit
    FROM batches b
    JOIN medicines m ON b.medicine_id = m.id
    WHERE b.expiry_date >= ? AND b.expiry_date <= ? AND b.current_qty > 0
    ORDER BY b.expiry_date ASC
    """, (today_str, soon_str))
    expiring_soon = []
    for r in cursor.fetchall():
        d = dict(r)
        d["days_left"] = (datetime.datetime.strptime(d["expiry_date"], "%Y-%m-%d").date() - today).days
        expiring_soon.append(d)

    cursor.execute("""
    SELECT b.id, b.batch_number, b.expiry_date, b.current_qty, b.unit_price, m.name as medicine_name, m.unit
    FROM batches b
    JOIN medicines m ON b.medicine_id = m.id
    WHERE b.expiry_date < ? AND b.current_qty > 0
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
    <title>PharmaFEFO — Smart Pharmacy Stock & FEFO Dispensing Engine</title>
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
                <button onclick="showTab('landing')" class="hover:text-sky-400 transition">Landing Page</button>
                <button onclick="showTab('dashboard')" class="hover:text-sky-400 transition">Dashboard</button>
                <button onclick="showTab('inventory')" class="hover:text-sky-400 transition">Stock Inventory</button>
                <button onclick="showTab('batches')" class="hover:text-sky-400 transition">Batch Management</button>
                <button onclick="showTab('dispenser')" class="hover:text-sky-400 transition font-semibold text-emerald-400"><i class="fa-solid fa-bolt mr-1"></i>FEFO Dispenser</button>
                <button onclick="showTab('alerts')" class="hover:text-sky-400 transition relative">
                    Expiry Alerts
                    <span id="nav-alert-badge" class="hidden absolute -top-2 -right-3 bg-rose-500 text-white text-xs px-1.5 py-0.5 rounded-full font-bold">0</span>
                </button>
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

        <!-- 1. LANDING PAGE TAB (Mandatory Product Page) -->
        <section id="tab-landing" class="space-y-12">
            <!-- Hero Banner -->
            <div class="gradient-bg text-white rounded-3xl p-8 sm:p-12 shadow-2xl relative overflow-hidden">
                <div class="max-w-3xl space-y-6 relative z-10">
                    <span class="bg-sky-500/20 text-sky-300 border border-sky-400/30 text-xs px-3 py-1 rounded-full font-semibold uppercase tracking-wider">
                        Next-Gen Pharmacy Operations
                    </span>
                    <h1 class="text-4xl sm:text-5xl font-extrabold tracking-tight leading-tight">
                        Zero Expired Medicines Left Behind with <span class="text-sky-400">FEFO Automation</span>
                    </h1>
                    <p class="text-slate-300 text-lg leading-relaxed">
                        PharmaFEFO revolutionizes neighbourhood pharmacy inventory with **First-Expiry-First-Out** automated batch dispensing, real-time in-date stock tracking, and proactive expiry alerts.
                    </p>
                    <div class="flex flex-wrap gap-4 pt-2">
                        <button onclick="showTab('dispenser')" class="bg-emerald-500 hover:bg-emerald-400 text-slate-900 font-bold px-6 py-3 rounded-xl shadow-lg transition flex items-center space-x-2">
                            <i class="fa-solid fa-circle-play"></i>
                            <span>Launch FEFO Dispenser Engine</span>
                        </button>
                        <button onclick="showTab('dashboard')" class="bg-slate-800 hover:bg-slate-700 border border-slate-700 text-white font-semibold px-6 py-3 rounded-xl transition">
                            Explore Dashboard
                        </button>
                    </div>
                </div>
            </div>

            <!-- Core Problem & Solution Grid -->
            <div class="grid md:grid-cols-3 gap-8">
                <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-3">
                    <div class="w-12 h-12 bg-sky-100 text-sky-600 rounded-xl flex items-center justify-center text-xl font-bold">
                        <i class="fa-solid fa-hourglass-start"></i>
                    </div>
                    <h3 class="text-lg font-bold text-slate-900">100% FEFO Dispensing</h3>
                    <p class="text-slate-600 text-sm">
                        Automatically consumes from batches expiring soonest first. Eliminates dead inventory and accidental distribution of expired stock.
                    </p>
                </div>

                <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-3">
                    <div class="w-12 h-12 bg-emerald-100 text-emerald-600 rounded-xl flex items-center justify-center text-xl font-bold">
                        <i class="fa-solid fa-check-double"></i>
                    </div>
                    <h3 class="text-lg font-bold text-slate-900">In-Date Stock Visibility</h3>
                    <p class="text-slate-600 text-sm">
                        Instantly answers customer queries like <em>"Do we have paracetamol in date?"</em> by calculating sellable stock excluding expired batches.
                    </p>
                </div>

                <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-3">
                    <div class="w-12 h-12 bg-amber-100 text-amber-600 rounded-xl flex items-center justify-center text-xl font-bold">
                        <i class="fa-solid fa-triangle-exclamation"></i>
                    </div>
                    <h3 class="text-lg font-bold text-slate-900">Proactive Expiry Radar</h3>
                    <p class="text-slate-600 text-sm">
                        Generates a 30-day heads-up notification system allowing pharmacists to return or discount batches before they spoil.
                    </p>
                </div>
            </div>

            <!-- Product Specs & Roadmap -->
            <div class="bg-slate-900 text-white rounded-2xl p-8 space-y-6">
                <h2 class="text-2xl font-bold text-white">Target Audience & Value Proposition</h2>
                <div class="grid md:grid-cols-2 gap-6 text-sm text-slate-300">
                    <div class="space-y-2">
                        <h4 class="font-bold text-sky-400">Target Audience</h4>
                        <p>Independent neighbourhood pharmacies, hospital dispensaries, retail chain chemist outlets, and pharmaceutical stockists.</p>
                    </div>
                    <div class="space-y-2">
                        <h4 class="font-bold text-emerald-400">How It Helps</h4>
                        <p>Saves thousands of dollars annually in expired stock waste, guarantees patient safety, and speeds up checkout dispensing by 70%.</p>
                    </div>
                </div>

                <div class="border-t border-slate-800 pt-6">
                    <h3 class="text-lg font-bold text-white mb-4">🚀 Top 3 Next Planned Features</h3>
                    <div class="grid md:grid-cols-3 gap-4">
                        <div class="bg-slate-800 p-4 rounded-xl border border-slate-700">
                            <span class="text-xs text-sky-400 font-bold block mb-1">Feature 1</span>
                            <h4 class="font-bold text-white text-sm">Barcode & QR Batch Scanner</h4>
                            <p class="text-slate-400 text-xs mt-1">Scan 2D barcodes at checkout to instantly auto-select and deduct the exact FEFO batch.</p>
                        </div>
                        <div class="bg-slate-800 p-4 rounded-xl border border-slate-700">
                            <span class="text-xs text-emerald-400 font-bold block mb-1">Feature 2</span>
                            <h4 class="font-bold text-white text-sm">Supplier Auto-Return Portal</h4>
                            <p class="text-slate-400 text-xs mt-1">Automated credit note generation for returning batches 45 days prior to expiration.</p>
                        </div>
                        <div class="bg-slate-800 p-4 rounded-xl border border-slate-700">
                            <span class="text-xs text-amber-400 font-bold block mb-1">Feature 3</span>
                            <h4 class="font-bold text-white text-sm">AI Demand & Reorder Predictor</h4>
                            <p class="text-slate-400 text-xs mt-1">Predict seasonal spikes (e.g. flu season) and automate batch purchase orders.</p>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <!-- 2. DASHBOARD TAB -->
        <section id="tab-dashboard" class="hidden space-y-6">
            <div class="flex justify-between items-center">
                <h2 class="text-2xl font-extrabold text-slate-900">Pharmacy Operations Dashboard</h2>
                <span id="dashboard-date" class="text-xs font-semibold text-slate-500 bg-white px-3 py-1.5 rounded-lg border border-slate-200"></span>
            </div>

            <!-- Stats Metric Cards -->
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
                    <span class="text-xs font-semibold text-slate-500 uppercase tracking-wider">Expired Batches (Blocked)</span>
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

        <!-- 3. STOCK INVENTORY TAB (Pagination & Sorting & Search) -->
        <section id="tab-inventory" class="hidden space-y-6">
            <div class="flex flex-col sm:flex-row justify-between sm:items-center gap-4">
                <div>
                    <h2 class="text-2xl font-extrabold text-slate-900">Medicine Master Inventory</h2>
                    <p class="text-xs text-slate-500">Real-time sellable stock counts (excluding expired batches)</p>
                </div>
            </div>

            <!-- Filter Controls -->
            <div class="bg-white p-4 rounded-xl border border-slate-200 flex flex-wrap gap-4 items-center justify-between">
                <div class="flex items-center space-x-2 flex-1 min-w-[240px]">
                    <i class="fa-solid fa-search text-slate-400"></i>
                    <input type="text" id="inv-search" oninput="loadMedicines()" placeholder="Search medicine by name or category..." class="w-full text-sm outline-none bg-transparent">
                </div>
                <div class="flex items-center space-x-4">
                    <label class="text-xs font-semibold text-slate-500">Sort By:</label>
                    <select id="inv-sort" onchange="loadMedicines()" class="text-xs bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5 outline-none">
                        <option value="name">Name</option>
                        <option value="category">Category</option>
                        <option value="created_at">Date Added</option>
                    </select>
                    <select id="inv-order" onchange="loadMedicines()" class="text-xs bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5 outline-none">
                        <option value="asc">Ascending</option>
                        <option value="desc">Descending</option>
                    </select>
                </div>
            </div>

            <!-- Medicines Table -->
            <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                <table class="w-full text-left text-sm border-collapse">
                    <thead class="bg-slate-100 text-slate-600 text-xs uppercase tracking-wider">
                        <tr>
                            <th class="p-4">Medicine Name</th>
                            <th class="p-4">Generic Name</th>
                            <th class="p-4">Category</th>
                            <th class="p-4">Sellable Stock (In-Date)</th>
                            <th class="p-4">Next Batch Expiry</th>
                            <th class="p-4">Actions</th>
                        </tr>
                    </thead>
                    <tbody id="inventory-tbody" class="divide-y divide-slate-100"></tbody>
                </table>
            </div>

            <!-- Pagination Bar -->
            <div class="flex justify-between items-center text-xs text-slate-500">
                <span id="inv-page-info">Showing page 1 of 1</span>
                <div class="flex space-x-2">
                    <button id="inv-prev-btn" onclick="changeInvPage(-1)" class="px-3 py-1.5 bg-white border border-slate-200 rounded-lg disabled:opacity-50">Previous</button>
                    <button id="inv-next-btn" onclick="changeInvPage(1)" class="px-3 py-1.5 bg-white border border-slate-200 rounded-lg disabled:opacity-50">Next</button>
                </div>
            </div>
        </section>

        <!-- 4. BATCHES MANAGEMENT TAB -->
        <section id="tab-batches" class="hidden space-y-6">
            <div class="flex flex-col sm:flex-row justify-between sm:items-center gap-4">
                <div>
                    <h2 class="text-2xl font-extrabold text-slate-900">Batch Stock Ledger</h2>
                    <p class="text-xs text-slate-500">Track batch expiry dates, quantities, and status</p>
                </div>
            </div>

            <!-- Filter & Sort Status Bar -->
            <div class="bg-white p-4 rounded-xl border border-slate-200 flex flex-wrap gap-4 items-center justify-between">
                <div class="flex space-x-2 text-xs font-semibold">
                    <button onclick="setBatchFilter('all')" class="batch-filter-btn px-3 py-1.5 rounded-lg bg-slate-900 text-white" data-status="all">All Batches</button>
                    <button onclick="setBatchFilter('active')" class="batch-filter-btn px-3 py-1.5 rounded-lg bg-slate-100 text-slate-600" data-status="active">Active (In-Date)</button>
                    <button onclick="setBatchFilter('expiring_soon')" class="batch-filter-btn px-3 py-1.5 rounded-lg bg-amber-100 text-amber-700" data-status="expiring_soon">Expiring Soon (&lt;30d)</button>
                    <button onclick="setBatchFilter('expired')" class="batch-filter-btn px-3 py-1.5 rounded-lg bg-rose-100 text-rose-700" data-status="expired">Expired</button>
                </div>
                <div class="flex items-center space-x-3 text-xs">
                    <label class="font-semibold text-slate-500">Sort By Expiry:</label>
                    <select id="batch-sort-order" onchange="loadBatches()" class="bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5 outline-none font-semibold">
                        <option value="asc">Earliest Expiring First</option>
                        <option value="desc">Latest Expiring First</option>
                    </select>
                </div>
            </div>

            <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                <table class="w-full text-left text-sm border-collapse">
                    <thead class="bg-slate-100 text-slate-600 text-xs uppercase tracking-wider">
                        <tr>
                            <th class="p-4">Batch Number</th>
                            <th class="p-4">Medicine</th>
                            <th class="p-4">Expiry Date</th>
                            <th class="p-4">Available Qty</th>
                            <th class="p-4">Unit Price</th>
                            <th class="p-4">Status</th>
                        </tr>
                    </thead>
                    <tbody id="batches-tbody" class="divide-y divide-slate-100"></tbody>
                </table>
            </div>
        </section>

        <!-- 5. FEFO DISPENSER CONSOLE TAB -->
        <section id="tab-dispenser" class="hidden space-y-6 max-w-3xl mx-auto">
            <div class="bg-white p-8 rounded-3xl border border-slate-200 shadow-xl space-y-6">
                <div class="flex items-center space-x-3 border-b border-slate-100 pb-4">
                    <div class="bg-emerald-500 text-white p-3 rounded-2xl font-bold">
                        <i class="fa-solid fa-bolt text-2xl"></i>
                    </div>
                    <div>
                        <h2 class="text-2xl font-black text-slate-900">FEFO Automated Dispensing Console</h2>
                        <p class="text-xs text-slate-500">Dispenses strictly from the earliest expiring batch first. Expired stock is automatically blocked.</p>
                    </div>
                </div>

                <form id="dispense-form" onsubmit="handleDispense(event)" class="space-y-5">
                    <div>
                        <label class="block text-xs font-bold text-slate-700 uppercase mb-2">Select Medicine</label>
                        <select id="dispense-med-id" required onchange="updateDispensePreview()" class="w-full p-3 rounded-xl border border-slate-200 bg-slate-50 font-medium text-slate-800 outline-none focus:ring-2 focus:ring-emerald-500">
                            <option value="">-- Choose Medicine --</option>
                        </select>
                    </div>

                    <div id="dispense-preview" class="hidden bg-emerald-50 border border-emerald-200 p-4 rounded-xl text-xs space-y-1">
                        <div class="font-bold text-emerald-800" id="prev-title">--</div>
                        <div class="text-emerald-700" id="prev-stock">--</div>
                        <div class="text-emerald-600 font-semibold" id="prev-fefo">--</div>
                    </div>

                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-xs font-bold text-slate-700 uppercase mb-2">Quantity to Dispense</label>
                            <input type="number" id="dispense-qty" min="1" required placeholder="e.g. 20" class="w-full p-3 rounded-xl border border-slate-200 font-medium text-slate-800 outline-none focus:ring-2 focus:ring-emerald-500">
                        </div>
                        <div>
                            <label class="block text-xs font-bold text-slate-700 uppercase mb-2">Customer Name / Presc #</label>
                            <input type="text" id="dispense-customer" placeholder="Walk-in Customer" class="w-full p-3 rounded-xl border border-slate-200 font-medium text-slate-800 outline-none focus:ring-2 focus:ring-emerald-500">
                        </div>
                    </div>

                    <button type="submit" class="w-full py-4 bg-emerald-600 hover:bg-emerald-500 text-white font-black rounded-2xl shadow-lg transition text-base flex items-center justify-center space-x-2">
                        <i class="fa-solid fa-cart-flatbed"></i>
                        <span>Execute FEFO Dispense Order</span>
                    </button>
                </form>

                <!-- FEFO Execution Audit Result -->
                <div id="dispense-result" class="hidden space-y-4"></div>
            </div>
        </section>

        <!-- 6. EXPIRY ALERTS TAB -->
        <section id="tab-alerts" class="hidden space-y-6">
            <h2 class="text-2xl font-extrabold text-slate-900">Batch Expiry Radar & Alerts</h2>

            <div class="grid md:grid-cols-2 gap-6">
                <div class="bg-amber-50 border border-amber-200 rounded-2xl p-6 space-y-4">
                    <h3 class="font-bold text-amber-900 text-lg flex items-center space-x-2">
                        <i class="fa-solid fa-triangle-exclamation text-amber-600"></i>
                        <span>Expiring Within 30 Days</span>
                    </h3>
                    <div id="alerts-expiring-list" class="space-y-3"></div>
                </div>

                <div class="bg-rose-50 border border-rose-200 rounded-2xl p-6 space-y-4">
                    <h3 class="font-bold text-rose-900 text-lg flex items-center space-x-2">
                        <i class="fa-solid fa-ban text-rose-600"></i>
                        <span>Expired Batches (Strictly Blocked)</span>
                    </h3>
                    <div id="alerts-expired-list" class="space-y-3"></div>
                </div>
            </div>
        </section>

    </main>

    <!-- AUTH MODAL -->
    <div id="auth-modal" class="fixed inset-0 bg-slate-900/60 backdrop-blur-sm hidden z-50 flex items-center justify-center p-4">
        <div class="bg-white rounded-3xl max-w-md w-full p-6 shadow-2xl space-y-4 relative">
            <button onclick="closeAuthModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-600"><i class="fa-solid fa-xmark text-xl"></i></button>
            <h3 class="text-xl font-bold text-slate-900 text-center" id="auth-modal-title">Pharmacist Authentication</h3>
            
            <div class="flex border-b border-slate-200 mb-4">
                <button onclick="switchAuthTab('login')" id="auth-tab-login" class="flex-1 py-2 font-bold text-sm text-sky-600 border-b-2 border-sky-600">Login</button>
                <button onclick="switchAuthTab('register')" id="auth-tab-reg" class="flex-1 py-2 font-bold text-sm text-slate-400">Register</button>
            </div>

            <form onsubmit="handleAuthSubmit(event)" class="space-y-4">
                <div>
                    <label class="block text-xs font-bold text-slate-600 mb-1">Username</label>
                    <input type="text" id="auth-user" required class="w-full p-2.5 border border-slate-200 rounded-xl outline-none focus:ring-2 focus:ring-sky-500">
                </div>
                <div id="auth-email-group" class="hidden">
                    <label class="block text-xs font-bold text-slate-600 mb-1">Email</label>
                    <input type="email" id="auth-email" class="w-full p-2.5 border border-slate-200 rounded-xl outline-none focus:ring-2 focus:ring-sky-500">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-600 mb-1">Password</label>
                    <input type="password" id="auth-pass" required class="w-full p-2.5 border border-slate-200 rounded-xl outline-none focus:ring-2 focus:ring-sky-500">
                </div>
                <div id="auth-error-msg" class="hidden text-xs text-rose-600 font-semibold"></div>
                <button type="submit" class="w-full py-3 bg-sky-600 hover:bg-sky-500 text-white font-bold rounded-xl transition">Submit</button>
            </form>
        </div>
    </div>

    <!-- JAVASCRIPT APP CONTROLLER -->
    <script>
        let currentTab = 'landing';
        let invPage = 1;
        let batchStatus = 'all';
        let authMode = 'login';
        let authToken = localStorage.getItem('pharma_token') || '';
        let authUser = localStorage.getItem('pharma_username') || 'Pharmacist (Admin)';

        document.addEventListener('DOMContentLoaded', () => {
            if(authUser) document.getElementById('current-username').innerText = authUser;
            loadDashboardStats();
            loadMedicines();
            loadBatches();
            loadAlerts();
        });

        function getAuthHeader() {
            return authToken ? { 'Authorization': `Bearer ${authToken}` } : {};
        }

        function showTab(tabName) {
            ['landing', 'dashboard', 'inventory', 'batches', 'dispenser', 'alerts'].forEach(t => {
                document.getElementById(`tab-${t}`).classList.add('hidden');
            });
            document.getElementById(`tab-${tabName}`).classList.remove('hidden');
            currentTab = tabName;
            if(tabName === 'dashboard') loadDashboardStats();
            if(tabName === 'inventory') loadMedicines();
            if(tabName === 'batches') loadBatches();
            if(tabName === 'dispenser') populateDispenseDropdown();
            if(tabName === 'alerts') loadAlerts();
        }

        async function loadDashboardStats() {
            try {
                const res = await fetch('/api/dashboard/stats');
                const data = await res.json();
                document.getElementById('stat-medicines').innerText = data.total_medicines;
                document.getElementById('stat-sellable').innerText = data.total_sellable_stock;
                document.getElementById('stat-expiring').innerText = data.expiring_soon_batches;
                document.getElementById('stat-expired').innerText = data.expired_batches;
                document.getElementById('dashboard-date').innerText = `System Date: ${data.today_date}`;

                const navBadge = document.getElementById('nav-alert-badge');
                const totalAlerts = data.expiring_soon_batches + data.expired_batches;
                if(totalAlerts > 0) {
                    navBadge.innerText = totalAlerts;
                    navBadge.classList.remove('hidden');
                }
            } catch (err) { console.error(err); }
        }

        async function executeInDateQuery() {
            const query = document.getElementById('quick-query-input').value.trim();
            if(!query) return;
            const res = await fetch(`/api/medicines/search?q=${encodeURIComponent(query)}`);
            const data = await res.json();
            const box = document.getElementById('query-result-box');
            box.classList.remove('hidden');
            if(data.results.length === 0) {
                box.innerHTML = `<div class="text-rose-400 text-sm font-semibold">No medicines found matching "${query}".</div>`;
                return;
            }
            box.innerHTML = data.results.map(r => `
                <div class="p-3 bg-slate-900/60 rounded-lg text-sm border-l-4 ${r.is_available_in_date ? 'border-emerald-500' : 'border-rose-500'}">
                    <div class="font-bold text-white">${r.answer_summary}</div>
                    <div class="text-xs text-slate-400">Category: ${r.category || 'General'} | Generic: ${r.generic_name || 'N/A'}</div>
                </div>
            `).join('');
        }

        async function loadMedicines() {
            const search = document.getElementById('inv-search').value;
            const sort = document.getElementById('inv-sort').value;
            const order = document.getElementById('inv-order').value;
            const res = await fetch(`/api/medicines?search=${encodeURIComponent(search)}&page=${invPage}&limit=10&sort_by=${sort}&order=${order}`);
            const json = await res.json();
            const tbody = document.getElementById('inventory-tbody');
            tbody.innerHTML = json.data.map(m => `
                <tr class="hover:bg-slate-50 transition">
                    <td class="p-4 font-bold text-slate-900">${m.name}</td>
                    <td class="p-4 text-slate-600">${m.generic_name || '-'}</td>
                    <td class="p-4 text-xs font-semibold"><span class="bg-slate-100 px-2 py-1 rounded-md text-slate-700">${m.category || 'General'}</span></td>
                    <td class="p-4 font-black ${m.sellable_stock > 0 ? 'text-emerald-600' : 'text-rose-600'}">${m.sellable_stock} ${m.unit}</td>
                    <td class="p-4 text-xs text-slate-500 font-medium">${m.next_expiry || 'No active batches'}</td>
                    <td class="p-4">
                        <button onclick="quickSelectDispense(${m.id})" class="bg-emerald-500 hover:bg-emerald-400 text-slate-900 font-bold text-xs px-3 py-1.5 rounded-lg transition">
                            Dispense FEFO
                        </button>
                    </td>
                </tr>
            `).join('');

            document.getElementById('inv-page-info').innerText = `Page ${json.pagination.page} of ${json.pagination.total_pages} (${json.pagination.total_records} total items)`;
            document.getElementById('inv-prev-btn').disabled = json.pagination.page <= 1;
            document.getElementById('inv-next-btn').disabled = json.pagination.page >= json.pagination.total_pages;
        }

        function changeInvPage(delta) {
            invPage += delta;
            loadMedicines();
        }

        async function loadBatches() {
            const order = document.getElementById('batch-sort-order').value;
            const res = await fetch(`/api/batches?status=${batchStatus}&page=1&limit=50&sort_by=expiry_date&order=${order}`);
            const json = await res.json();
            const tbody = document.getElementById('batches-tbody');
            tbody.innerHTML = json.data.map(b => `
                <tr class="hover:bg-slate-50 transition">
                    <td class="p-4 font-mono font-bold text-slate-900">${b.batch_number}</td>
                    <td class="p-4 font-medium text-slate-800">${b.medicine_name}</td>
                    <td class="p-4 font-semibold text-slate-700">${b.expiry_date}</td>
                    <td class="p-4 font-bold ${b.current_qty > 0 ? 'text-slate-900' : 'text-slate-400'}">${b.current_qty} / ${b.initial_qty} ${b.unit}</td>
                    <td class="p-4 font-mono text-xs">₹${b.unit_price}</td>
                    <td class="p-4"><span class="text-xs px-2.5 py-1 rounded-full font-bold bg-${b.badge_color}-100 text-${b.badge_color}-700">${b.status}</span></td>
                </tr>
            `).join('');
        }

        function setBatchFilter(st) {
            batchStatus = st;
            document.querySelectorAll('.batch-filter-btn').forEach(b => {
                if(b.getAttribute('data-status') === st) {
                    b.className = 'batch-filter-btn px-3 py-1.5 rounded-lg bg-slate-900 text-white font-semibold text-xs';
                } else {
                    b.className = 'batch-filter-btn px-3 py-1.5 rounded-lg bg-slate-100 text-slate-600 font-semibold text-xs';
                }
            });
            loadBatches();
        }

        async function populateDispenseDropdown() {
            const res = await fetch('/api/medicines?limit=100');
            const json = await res.json();
            const select = document.getElementById('dispense-med-id');
            select.innerHTML = '<option value="">-- Choose Medicine --</option>' + json.data.map(m => `
                <option value="${m.id}">${m.name} (${m.sellable_stock} ${m.unit} in-date)</option>
            `).join('');
        }

        function quickSelectDispense(medId) {
            showTab('dispenser');
            setTimeout(() => {
                document.getElementById('dispense-med-id').value = medId;
                updateDispensePreview();
            }, 100);
        }

        async function updateDispensePreview() {
            const medId = document.getElementById('dispense-med-id').value;
            const previewBox = document.getElementById('dispense-preview');
            if(!medId) { previewBox.classList.add('hidden'); return; }

            const res = await fetch(`/api/batches?medicine_id=${medId}&status=active`);
            const json = await res.json();
            previewBox.classList.remove('hidden');

            if(json.data.length === 0) {
                document.getElementById('prev-title').innerText = "⚠️ No Active In-Date Batches!";
                document.getElementById('prev-stock').innerText = "Sellable Stock: 0 units available.";
                document.getElementById('prev-fefo').innerText = "Cannot dispense — all stock is either expired or depleted.";
            } else {
                const soonest = json.data[0];
                const totalStock = json.data.reduce((acc, b) => acc + b.current_qty, 0);
                document.getElementById('prev-title').innerText = `✅ Total In-Date Sellable Stock: ${totalStock} units`;
                document.getElementById('prev-stock').innerText = `Active Batches Available: ${json.data.length} batches`;
                document.getElementById('prev-fefo').innerText = `🎯 Next Batch to be Dispensed First (FEFO): #${soonest.batch_number} (Expires: ${soonest.expiry_date} - ${soonest.current_qty} units left)`;
            }
        }

        async function handleDispense(e) {
            e.preventDefault();
            const medId = parseInt(document.getElementById('dispense-med-id').value);
            const qty = parseInt(document.getElementById('dispense-qty').value);
            const cust = document.getElementById('dispense-customer').value || "Walk-in Customer";
            const resBox = document.getElementById('dispense-result');

            try {
                const headers = { 'Content-Type': 'application/json', ...getAuthHeader() };
                const res = await fetch('/api/dispense', {
                    method: 'POST',
                    headers: headers,
                    body: JSON.stringify({ medicine_id: medId, quantity: qty, customer_name: cust })
                });

                const data = await res.json();
                resBox.classList.remove('hidden');

                if(res.status === 401) {
                    openAuthModal();
                    document.getElementById('auth-error-msg').innerText = "Authentication required to execute dispensing. Please login as Pharmacist (admin/admin123).";
                    document.getElementById('auth-error-msg').classList.remove('hidden');
                    return;
                }

                if(!res.ok) {
                    resBox.innerHTML = `
                        <div class="bg-rose-50 border border-rose-200 p-4 rounded-xl text-rose-800 space-y-1">
                            <div class="font-bold flex items-center space-x-2"><i class="fa-solid fa-circle-xmark"></i><span>Dispense Failed!</span></div>
                            <div class="text-xs">${data.detail || 'An error occurred'}</div>
                        </div>`;
                } else {
                    resBox.innerHTML = `
                        <div class="bg-emerald-50 border border-emerald-200 p-6 rounded-2xl text-emerald-950 space-y-3">
                            <div class="font-extrabold text-base flex items-center space-x-2 text-emerald-800">
                                <i class="fa-solid fa-circle-check text-emerald-600 text-xl"></i>
                                <span>${data.message}</span>
                            </div>
                            <div class="text-xs text-emerald-800 font-semibold">Total Invoice Amount: ₹${data.total_cost}</div>
                            <div class="bg-white p-3 rounded-xl border border-emerald-200 text-xs space-y-2">
                                <div class="font-bold text-slate-700 uppercase tracking-wider">FEFO Batch Allocation Audit Breakdown:</div>
                                <div class="space-y-1">
                                    ${data.fefo_breakdown.map(b => `
                                        <div class="flex justify-between border-b border-slate-100 pb-1">
                                            <span>Batch <strong>#${b.batch_number}</strong> (Expires: ${b.expiry_date})</span>
                                            <span class="font-bold text-emerald-700">Deducted: ${b.quantity_taken} units @ ₹${b.unit_price}</span>
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                        </div>`;
                    loadDashboardStats();
                    updateDispensePreview();
                }
            } catch(err) { console.error(err); }
        }

        function loadAlerts() {
            fetch('/api/alerts/expiring')
                .then(res => res.json())
                .then(data => {
                    const expiringBox = document.getElementById('alerts-expiring-list');
                    if(data.expiring_soon.length === 0) {
                        expiringBox.innerHTML = '<div class="text-xs text-amber-700">No batches expiring within 30 days.</div>';
                    } else {
                        expiringBox.innerHTML = data.expiring_soon.map(b => `
                            <div class="bg-white p-3 rounded-xl border border-amber-200 shadow-sm flex justify-between items-center text-xs">
                                <div>
                                    <div class="font-bold text-slate-900">${b.medicine_name} (Batch #${b.batch_number})</div>
                                    <div class="text-amber-700 font-medium">Expires on ${b.expiry_date} (${b.days_left} days left)</div>
                                </div>
                                <div class="font-black text-amber-800 text-sm">${b.current_qty} ${b.unit}</div>
                            </div>
                        `).join('');
                    }

                    const expiredBox = document.getElementById('alerts-expired-list');
                    if(data.expired.length === 0) {
                        expiredBox.innerHTML = '<div class="text-xs text-emerald-700">Zero expired stock in inventory. Excellent!</div>';
                    } else {
                        expiredBox.innerHTML = data.expired.map(b => `
                            <div class="bg-white p-3 rounded-xl border border-rose-200 shadow-sm flex justify-between items-center text-xs">
                                <div>
                                    <div class="font-bold text-slate-900">${b.medicine_name} (Batch #${b.batch_number})</div>
                                    <div class="text-rose-700 font-medium">Expired ${b.days_overdue} days ago (${b.expiry_date})</div>
                                </div>
                                <div class="font-black text-rose-800 text-sm">${b.current_qty} ${b.unit}</div>
                            </div>
                        `).join('');
                    }
                });
        }

        function switchAuthTab(mode) {
            authMode = mode;
            document.getElementById('auth-tab-login').className = mode === 'login' ? 'flex-1 py-2 font-bold text-sm text-sky-600 border-b-2 border-sky-600' : 'flex-1 py-2 font-bold text-sm text-slate-400';
            document.getElementById('auth-tab-reg').className = mode === 'register' ? 'flex-1 py-2 font-bold text-sm text-sky-600 border-b-2 border-sky-600' : 'flex-1 py-2 font-bold text-sm text-slate-400';
            if(mode === 'register') {
                document.getElementById('auth-email-group').classList.remove('hidden');
            } else {
                document.getElementById('auth-email-group').classList.add('hidden');
            }
            document.getElementById('auth-error-msg').classList.add('hidden');
        }

        function openAuthModal() { document.getElementById('auth-modal').classList.remove('hidden'); }
        function closeAuthModal() { document.getElementById('auth-modal').classList.add('hidden'); }

        async function handleAuthSubmit(e) {
            e.preventDefault();
            const user = document.getElementById('auth-user').value;
            const pass = document.getElementById('auth-pass').value;
            const email = document.getElementById('auth-email').value;
            const errBox = document.getElementById('auth-error-msg');

            const url = authMode === 'login' ? '/api/auth/login' : '/api/auth/register';
            const body = authMode === 'login' ? { username: user, password: pass } : { username: user, email: email, password: pass };

            try {
                const res = await fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                const data = await res.json();
                if(!res.ok) {
                    errBox.innerText = data.detail || 'Authentication failed';
                    errBox.classList.remove('hidden');
                } else {
                    authToken = data.token;
                    authUser = data.username;
                    localStorage.setItem('pharma_token', authToken);
                    localStorage.setItem('pharma_username', authUser);
                    document.getElementById('current-username').innerText = authUser;
                    closeAuthModal();
                    alert(`Successfully authenticated as ${authUser}!`);
                }
            } catch(err) { console.error(err); }
        }
    </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
