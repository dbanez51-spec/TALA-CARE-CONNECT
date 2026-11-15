"""Database layer for TALA-CARE CONNECT (SQLite).

SQLite is used because it needs no server and is easy for a college capstone.
Everything is created automatically the first time the app runs.
"""
import hashlib
import hmac
import os
import sqlite3
from datetime import datetime, date

DB_PATH = os.environ.get("TALACARE_DB", "talacare.db")

ROLES = [
    "Administrator", "Receptionist", "Doctor", "Nurse",
    "Hospital Pharmacist", "Partner Pharmacy", "Hospital Social Worker", "Patient",
]

# Which pages each role can open (role-based access control).
PERMISSIONS = {
    "Administrator": ["Dashboard", "Patients", "Appointments", "Doctors & Schedules", "Medical Records",
                      "Hospital Beds", "Pharmacy Inventory", "Smart Medicine Finder",
                      "Financial Assistance", "Social Worker Desk", "Directory & Map",
                      "Reports", "Admin"],
    "Receptionist": ["Dashboard", "Patients", "Appointments", "Directory & Map"],
    "Doctor": ["Dashboard", "Appointments", "Medical Records", "Directory & Map"],
    "Nurse": ["Dashboard", "Patients", "Appointments", "Medical Records", "Hospital Beds", "Directory & Map"],
    "Hospital Pharmacist": ["Dashboard", "Pharmacy Inventory", "Smart Medicine Finder", "Directory & Map", "Reports"],
    "Partner Pharmacy": ["Partner Pharmacy Portal", "Directory & Map"],
    "Hospital Social Worker": ["Dashboard", "Social Worker Desk", "Directory & Map", "Reports"],
    "Patient": ["Patient Home", "Smart Medicine Finder", "Financial Assistance",
                "My Appointments", "My Records", "Directory & Map"],
}

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS roles(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS permissions(id INTEGER PRIMARY KEY, role_id INTEGER NOT NULL REFERENCES roles(id), page TEXT NOT NULL, UNIQUE(role_id,page));
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, salt TEXT NOT NULL,
  full_name TEXT NOT NULL, role_id INTEGER NOT NULL REFERENCES roles(id),
  patient_id INTEGER, pharmacy_id INTEGER, active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS barangays(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, verified INTEGER DEFAULT 0, source_note TEXT);
CREATE TABLE IF NOT EXISTS puroks(id INTEGER PRIMARY KEY, barangay_id INTEGER NOT NULL REFERENCES barangays(id), name TEXT NOT NULL, UNIQUE(barangay_id,name));
CREATE TABLE IF NOT EXISTS patients(
  id INTEGER PRIMARY KEY, patient_code TEXT UNIQUE, full_name TEXT NOT NULL, birthdate TEXT NOT NULL,
  sex TEXT CHECK(sex IN ('Male','Female')), contact_no TEXT, email TEXT,
  emergency_name TEXT, emergency_contact TEXT, consent_given INTEGER DEFAULT 0, consent_at TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS patient_addresses(
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id),
  province TEXT DEFAULT 'Agusan del Sur', municipality TEXT DEFAULT 'Talacogon',
  barangay_id INTEGER REFERENCES barangays(id), barangay_text TEXT, purok TEXT, street_sitio TEXT, complete_address TEXT);
CREATE TABLE IF NOT EXISTS departments(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS doctors(
  id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), full_name TEXT NOT NULL, specialization TEXT,
  department_id INTEGER REFERENCES departments(id), active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS doctor_schedules(
  id INTEGER PRIMARY KEY, doctor_id INTEGER NOT NULL REFERENCES doctors(id), weekday INTEGER NOT NULL CHECK(weekday BETWEEN 0 AND 6),
  start_time TEXT NOT NULL, end_time TEXT NOT NULL, capacity INTEGER NOT NULL DEFAULT 20);
CREATE TABLE IF NOT EXISTS staff(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), full_name TEXT NOT NULL, position TEXT, department_id INTEGER REFERENCES departments(id));
CREATE TABLE IF NOT EXISTS appointments(
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), doctor_id INTEGER NOT NULL REFERENCES doctors(id),
  department_id INTEGER REFERENCES departments(id), appt_date TEXT NOT NULL, reason TEXT,
  status TEXT DEFAULT 'Confirmed' CHECK(status IN ('Confirmed','Waiting','In Consultation','Completed','Cancelled')),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS queues(id INTEGER PRIMARY KEY, appointment_id INTEGER UNIQUE NOT NULL REFERENCES appointments(id), queue_date TEXT NOT NULL, doctor_id INTEGER NOT NULL, queue_no INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS medical_records(
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), doctor_id INTEGER REFERENCES doctors(id),
  appointment_id INTEGER REFERENCES appointments(id), visit_date TEXT DEFAULT CURRENT_TIMESTAMP,
  bp TEXT, temperature REAL, pulse INTEGER, weight_kg REAL, complaint TEXT, treatment TEXT, lab_request TEXT, lab_result TEXT, certificate_note TEXT);
CREATE TABLE IF NOT EXISTS diagnoses(id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL REFERENCES medical_records(id), diagnosis TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS medicines(
  id INTEGER PRIMARY KEY, generic_name TEXT NOT NULL, brand_name TEXT, strength TEXT NOT NULL, dosage_form TEXT NOT NULL,
  unit TEXT DEFAULT 'pc', min_stock INTEGER DEFAULT 10, UNIQUE(generic_name,strength,dosage_form,brand_name));
CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, contact TEXT);
CREATE TABLE IF NOT EXISTS medicine_batches(
  id INTEGER PRIMARY KEY, medicine_id INTEGER NOT NULL REFERENCES medicines(id), batch_no TEXT NOT NULL, expiry_date TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK(quantity>=0), supplier_id INTEGER REFERENCES suppliers(id), received_at TEXT DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(medicine_id,batch_no));
CREATE TABLE IF NOT EXISTS inventory_transactions(
  id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL REFERENCES medicine_batches(id), medicine_id INTEGER NOT NULL,
  tx_type TEXT NOT NULL CHECK(tx_type IN ('IN','OUT','ADJUST')), quantity INTEGER NOT NULL, note TEXT, user_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS prescriptions(
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), doctor_id INTEGER REFERENCES doctors(id),
  record_id INTEGER REFERENCES medical_records(id), status TEXT DEFAULT 'Pending' CHECK(status IN ('Pending','Dispensed','Partially Dispensed','Needs Outside Pharmacy')),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS prescription_items(
  id INTEGER PRIMARY KEY, prescription_id INTEGER NOT NULL REFERENCES prescriptions(id), medicine_id INTEGER NOT NULL REFERENCES medicines(id),
  quantity INTEGER NOT NULL CHECK(quantity>0), directions TEXT, dispensed_qty INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pharmacies(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT, barangay_id INTEGER REFERENCES barangays(id), purok TEXT, street TEXT,
  contact_no TEXT, operating_hours TEXT, license_no TEXT, verification_status TEXT DEFAULT 'Pending' CHECK(verification_status IN ('Pending','Verified','Rejected')),
  latitude REAL, longitude REAL, is_demo INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS pharmacy_inventory(
  id INTEGER PRIMARY KEY, pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(id), generic_name TEXT NOT NULL, brand_name TEXT, strength TEXT NOT NULL,
  dosage_form TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>=0), price REAL, batch_no TEXT, expiry_date TEXT,
  last_updated TEXT NOT NULL, confirmed_at TEXT, status TEXT DEFAULT 'Reported',
  UNIQUE(pharmacy_id,generic_name,strength,dosage_form,brand_name,batch_no));
CREATE TABLE IF NOT EXISTS pharmacy_stock_updates(id INTEGER PRIMARY KEY, pharmacy_id INTEGER NOT NULL, inventory_id INTEGER, old_qty INTEGER, new_qty INTEGER, updated_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS reservations(
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), pharmacy_id INTEGER NOT NULL REFERENCES pharmacies(id),
  inventory_id INTEGER NOT NULL REFERENCES pharmacy_inventory(id), quantity INTEGER NOT NULL CHECK(quantity>0),
  status TEXT DEFAULT 'Requested' CHECK(status IN ('Requested','Approved','Declined','Picked Up','Cancelled')),
  pharmacy_note TEXT, feedback TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS hospital_rooms(id INTEGER PRIMARY KEY, ward TEXT NOT NULL, room_no TEXT NOT NULL, UNIQUE(ward,room_no));
CREATE TABLE IF NOT EXISTS hospital_beds(
  id INTEGER PRIMARY KEY, room_id INTEGER NOT NULL REFERENCES hospital_rooms(id), bed_no TEXT NOT NULL,
  status TEXT DEFAULT 'Available' CHECK(status IN ('Available','Occupied','Reserved','Maintenance')), UNIQUE(room_id,bed_no));
CREATE TABLE IF NOT EXISTS admissions(id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), bed_id INTEGER NOT NULL REFERENCES hospital_beds(id), admitted_at TEXT DEFAULT CURRENT_TIMESTAMP, reason TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS discharges(id INTEGER PRIMARY KEY, admission_id INTEGER NOT NULL REFERENCES admissions(id), discharged_at TEXT DEFAULT CURRENT_TIMESTAMP, summary TEXT);
CREATE TABLE IF NOT EXISTS assistance_organizations(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, level TEXT, address TEXT, contact_no TEXT, website TEXT, last_verified TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS assistance_programs(
  id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL REFERENCES assistance_organizations(id), name TEXT NOT NULL,
  expense_categories TEXT, general_eligibility TEXT, application_process TEXT, last_verified TEXT);
CREATE TABLE IF NOT EXISTS assistance_requirements(id INTEGER PRIMARY KEY, program_id INTEGER NOT NULL REFERENCES assistance_programs(id), requirement TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS patient_assistance_applications(
  id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), expense_type TEXT NOT NULL, total_expense REAL NOT NULL,
  can_afford REAL NOT NULL, shortage REAL NOT NULL, assistance_type TEXT, has_philhealth INTEGER DEFAULT 0, already_applied INTEGER DEFAULT 0,
  status TEXT DEFAULT 'Not Yet Applied' CHECK(status IN ('Not Yet Applied','Preparing Requirements','Referred to Social Worker','Application Submitted','Under Evaluation','Approved','Rejected','Completed')),
  missing_requirements TEXT, social_worker_note TEXT, share_consent INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT);
CREATE TABLE IF NOT EXISTS social_worker_referrals(id INTEGER PRIMARY KEY, application_id INTEGER NOT NULL REFERENCES patient_assistance_applications(id), program_id INTEGER REFERENCES assistance_programs(id), social_worker_id INTEGER REFERENCES users(id), note TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), message TEXT NOT NULL, is_read INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, action TEXT NOT NULL, detail TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS medicine_watches(id INTEGER PRIMARY KEY, patient_id INTEGER NOT NULL REFERENCES patients(id), generic_name TEXT NOT NULL, strength TEXT NOT NULL, dosage_form TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS facilities(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, facility_type TEXT NOT NULL, address TEXT, barangay_id INTEGER REFERENCES barangays(id),
  contact_no TEXT, latitude REAL, longitude REAL, verified INTEGER DEFAULT 0, last_verified TEXT);
CREATE INDEX IF NOT EXISTS idx_appt_date ON appointments(appt_date, doctor_id);
CREATE INDEX IF NOT EXISTS idx_batch_med ON medicine_batches(medicine_id, expiry_date);
CREATE INDEX IF NOT EXISTS idx_pinv_generic ON pharmacy_inventory(generic_name, strength, dosage_form);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return date.today().isoformat()


def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def q(sql, params=(), one=False):
    """Run a SELECT and return a list of dict rows (or one dict)."""
    with get_conn() as c:
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]
    return (rows[0] if rows else None) if one else rows


def run(sql, params=()):
    """Run INSERT/UPDATE/DELETE. Returns last inserted id."""
    with get_conn() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def hash_password(password, salt=None):
    salt = salt or os.urandom(16).hex()
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return h, salt


def verify_password(password, h, salt):
    new, _ = hash_password(password, salt)
    return hmac.compare_digest(new, h)


def audit(user, action, detail=""):
    run("INSERT INTO audit_logs(user_id,username,action,detail,created_at) VALUES(?,?,?,?,?)",
        (user.get("id") if user else None, user.get("username") if user else "anonymous", action, detail, now()))


def notify(user_id, message):
    run("INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)", (user_id, message, now()))


def create_user(username, password, full_name, role, patient_id=None, pharmacy_id=None):
    h, s = hash_password(password)
    rid = q("SELECT id FROM roles WHERE name=?", (role,), one=True)["id"]
    return run("INSERT INTO users(username,password_hash,salt,full_name,role_id,patient_id,pharmacy_id) VALUES(?,?,?,?,?,?,?)",
               (username, h, s, full_name, rid, patient_id, pharmacy_id))


def init_db():
    with get_conn() as c:
        c.executescript(SCHEMA)
    if q("SELECT COUNT(*) n FROM roles", one=True)["n"] == 0:
        seed()


def seed():
    """Starter data. Everything marked DEMO is fake and for testing only.
    NO real barangay lists, pharmacy addresses or inventory are invented:
    the Administrator must enter verified data (see README)."""
    for r in ROLES:
        rid = run("INSERT INTO roles(name) VALUES(?)", (r,))
        for p in PERMISSIONS[r]:
            run("INSERT INTO permissions(role_id,page) VALUES(?,?)", (rid, p))

    for d in ["General Medicine", "Pediatrics", "Obstetrics & Gynecology", "Surgery"]:
        run("INSERT INTO departments(name) VALUES(?)", (d,))

    # Placeholder barangays. Admin must replace with the official PSGC/PSA list.
    for i in range(1, 4):
        run("INSERT INTO barangays(name,verified,source_note) VALUES(?,0,?)",
            (f"Demo Barangay {i}", "DEMO placeholder - replace with official list"))

    # Demo accounts (change passwords after first login!)
    create_user("admin", "Admin@123", "System Administrator", "Administrator")
    create_user("reception", "Reception@123", "Demo Receptionist", "Receptionist")
    docuid = create_user("doctor", "Doctor@123", "Dr. Demo Doctor", "Doctor")
    create_user("nurse", "Nurse@123", "Demo Nurse", "Nurse")
    create_user("pharmacist", "Pharma@123", "Demo Hospital Pharmacist", "Hospital Pharmacist")
    create_user("social", "Social@123", "Demo Social Worker", "Hospital Social Worker")
    dept = q("SELECT id FROM departments WHERE name='General Medicine'", one=True)["id"]
    did = run("INSERT INTO doctors(user_id,full_name,specialization,department_id) VALUES(?,?,?,?)",
              (docuid, "Dr. Demo Doctor", "General Medicine", dept))
    for wd in range(0, 5):
        run("INSERT INTO doctor_schedules(doctor_id,weekday,start_time,end_time,capacity) VALUES(?,?,?,?,?)",
            (did, wd, "08:00", "12:00", 20))

    # Demo partner pharmacy (fake)
    ph = run("INSERT INTO pharmacies(name,address,contact_no,operating_hours,license_no,verification_status,is_demo) VALUES(?,?,?,?,?,?,1)",
             ("DEMO Partner Pharmacy", "(demo - no real address)", "(demo)", "8:00 AM - 6:00 PM", "DEMO-0000", "Verified"))
    create_user("partner", "Partner@123", "Demo Pharmacy Staff", "Partner Pharmacy", pharmacy_id=ph)

    # Demo beds
    rm = run("INSERT INTO hospital_rooms(ward,room_no) VALUES('Ward A','101')")
    for b in range(1, 5):
        run("INSERT INTO hospital_beds(room_id,bed_no) VALUES(?,?)", (rm, f"B{b}"))

    # Demo medicine in hospital catalog (generic, widely known; stock is DEMO)
    mid = run("INSERT INTO medicines(generic_name,brand_name,strength,dosage_form,unit,min_stock) VALUES('Paracetamol','','500 mg','Tablet','tab',50)")
    sup = run("INSERT INTO suppliers(name,contact) VALUES('DEMO Supplier','(demo)')")
    bid = run("INSERT INTO medicine_batches(medicine_id,batch_no,expiry_date,quantity,supplier_id) VALUES(?,?,?,?,?)",
              (mid, "DEMO-B1", "2027-12-31", 30, sup))
    run("INSERT INTO inventory_transactions(batch_id,medicine_id,tx_type,quantity,note,created_at) VALUES(?,?,'IN',30,'Demo opening stock',?)", (bid, mid, now()))
    run("""INSERT INTO pharmacy_inventory(pharmacy_id,generic_name,brand_name,strength,dosage_form,quantity,price,batch_no,expiry_date,last_updated)
           VALUES(?,?,?,?,?,?,?,?,?,?)""", (ph, "Paracetamol", "", "500 mg", "Tablet", 200, 2.0, "DEMO-P1", "2027-10-31", now()))

    # Assistance organizations: real agencies, but contact/address/requirements are
    # NOT filled in - they must be verified by staff from official sources.
    orgs = [
        ("DSWD - Assistance to Individuals in Crisis Situation (AICS)", "National", "https://www.dswd.gov.ph",
         "AICS", "Medical, laboratory, medicines, hospital bill support"),
        ("Philippine Charity Sweepstakes Office (PCSO)", "National", "https://www.pcso.gov.ph",
         "PCSO Medical Assistance", "Medicines, hospital bills, laboratory, procedures"),
        ("Department of Health (DOH) - MAIFIP", "National", "https://doh.gov.ph",
         "Medical Assistance for Indigent and Financially-Incapacitated Patients (MAIFIP)", "Hospital bill, medicines, laboratory"),
        ("PhilHealth", "National", "https://www.philhealth.gov.ph",
         "PhilHealth benefit packages", "Hospital admission, outpatient packages (as covered)"),
        ("Provincial Social Welfare and Development Office (Agusan del Sur)", "Provincial", None,
         "Provincial medical assistance (confirm availability)", "To be confirmed with the office"),
        ("Municipal Social Welfare and Development Office (Talacogon)", "Municipal", None,
         "Municipal medical assistance and referral (confirm availability)", "To be confirmed with the office"),
    ]
    for name, lvl, web, prog, cats in orgs:
        oid = run("INSERT INTO assistance_organizations(name,level,website,notes) VALUES(?,?,?,?)",
                  (name, lvl, web, "Address/contact not yet verified. Staff must confirm with the agency."))
        pid = run("""INSERT INTO assistance_programs(org_id,name,expense_categories,general_eligibility,application_process)
                     VALUES(?,?,?,?,?)""",
                  (oid, prog, cats,
                   "Final eligibility is decided by the agency. Contact the office to confirm current rules.",
                   "Ask the hospital social worker or contact the office. Confirm steps on the official website/office."))
        run("INSERT INTO assistance_requirements(program_id,requirement) VALUES(?, 'Requirements not yet verified - confirm with the agency (commonly a valid ID, medical certificate/abstract, and prescription/billing - verify first)')", (pid,))
