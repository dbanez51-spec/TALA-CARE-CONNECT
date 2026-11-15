"""TALA-CARE CONNECT - main Streamlit app (login, role-based menu, page routing)."""
import time
from datetime import date

import streamlit as st

import db
from db import q, run, audit, verify_password, create_user, PERMISSIONS
from modules.common import barangay_options, show_df
from modules import hospital, inventory, finder, assistance, admin

st.set_page_config(page_title="TALA-CARE CONNECT", page_icon="🏥", layout="wide")
db.init_db()

SESSION_MINUTES = 30
MAX_FAILS = 5
LOCK_SECONDS = 120

st.markdown("""<style>
[data-testid="stSidebar"]{background:#0b4f6c} [data-testid="stSidebar"] *{color:#fff}
[data-testid="stSidebar"] .stRadio label{padding:4px 0}
div[data-testid="stMetric"]{background:#f1f8fb;border-left:5px solid #14919b;padding:10px;border-radius:8px}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ Auth
def current_user():
    u = st.session_state.get("user")
    if not u:
        return None
    if time.time() - st.session_state.get("last_seen", 0) > SESSION_MINUTES * 60:
        audit(u, "SESSION_EXPIRED")
        st.session_state.clear()
        st.warning("Your session expired. Please log in again.")
        return None
    st.session_state["last_seen"] = time.time()
    return u


def signup_flow():
    """Single form: account + patient details, created together."""
    st.write("Create a patient account to book appointments, find medicines and check assistance options.")
    bar = barangay_options()
    with st.form("signup", clear_on_submit=False):
        c1, c2 = st.columns(2)
        un = c1.text_input("Choose a username *")
        pw = c2.text_input("Password * (8+ characters)", type="password")
        name = st.text_input("Full name *")
        c1, c2, c3 = st.columns(3)
        bd = c1.date_input("Birthdate *", date(2000, 1, 1), min_value=date(1900, 1, 1), max_value=date.today())
        sex = c2.selectbox("Sex *", ["Female", "Male"])
        contact = c3.text_input("Contact number")
        c1, c2, c3 = st.columns(3)
        brg = c1.selectbox("Barangay", list(bar.keys()) or ["(not available yet)"])
        purok = c2.text_input("Purok")
        street = c3.text_input("Street / Sitio")
        consent = st.checkbox("I consent to the collection and processing of my personal and health information for care and "
                              "coordination, as described in the Data Privacy Act of 2012 (RA 10173). *")
        ok = st.form_submit_button("Create account", type="primary")
    if not ok:
        return
    if not un.strip() or len(pw) < 8 or not name.strip():
        st.error("Username, full name and a password of 8+ characters are required.")
        return
    if not consent:
        st.error("Consent is required to create an account.")
        return
    if q("SELECT id FROM users WHERE username=?", (un.strip(),), one=True):
        st.error("That username is taken.")
        return
    pid = run("INSERT INTO patients(full_name,birthdate,sex,contact_no,consent_given,consent_at) VALUES(?,?,?,?,1,?)",
              (name.strip(), bd.isoformat(), sex, contact, db.now()))
    run("UPDATE patients SET patient_code=? WHERE id=?", (f"TC-{date.today().year}-{pid:05d}", pid))
    addr = ", ".join(x for x in [street, f"Purok {purok}" if purok else "", brg, "Talacogon", "Agusan del Sur"] if x)
    run("INSERT INTO patient_addresses(patient_id,barangay_id,barangay_text,purok,street_sitio,complete_address) VALUES(?,?,?,?,?,?)",
        (pid, bar.get(brg), brg, purok, street, addr))
    create_user(un.strip(), pw, name.strip(), "Patient", patient_id=pid)
    audit({"username": un}, "PATIENT_SIGNUP")
    st.success("Account created! Go to the 'Log in' tab.")


# ------------------------------------------------------------------ Notifications
def notification_center(user):
    unread = q("SELECT COUNT(*) n FROM notifications WHERE user_id=? AND is_read=0", (user["id"],), one=True)["n"]
    with st.sidebar.expander(f"🔔 Notifications ({unread})"):
        rows = q("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 15", (user["id"],))
        if not rows:
            st.caption("Nothing yet.")
        for r in rows:
            st.write(("• " if r["is_read"] else "🔸 ") + r["message"])
            st.caption(r["created_at"])
        if unread and st.button("Mark all as read"):
            run("UPDATE notifications SET is_read=1 WHERE user_id=?", (user["id"],))
            st.rerun()


def patient_home(user):
    st.header(f"Welcome, {user['full_name']}")
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("💊 Smart Medicine Finder")
        st.write("Can't get your medicine at the hospital? Search verified partner pharmacies, compare prices and request a reservation.")
        st.write("➡ Choose **Smart Medicine Finder** in the sidebar.")
    with c2:
        st.subheader("🤝 Financial Assistance Navigator")
        st.write("Need help paying for medicines, laboratory tests or hospital bills? See possible assistance programs and track your application.")
        st.write("➡ Choose **Financial Assistance** in the sidebar.")
    st.divider()
    todays = q("SELECT a.appt_date, d.full_name AS doctor, a.status FROM appointments a JOIN doctors d ON d.id=a.doctor_id WHERE a.patient_id=? AND a.appt_date>=? AND a.status IN ('Confirmed','Waiting') ORDER BY a.appt_date LIMIT 3",
               (user["patient_id"], db.today()))
    st.subheader("Upcoming appointments")
    show_df(todays, "No upcoming appointments.")


# ------------------------------------------------------------------ Router
def main():
    user = current_user()
    if not user:
        st.title("🏥 TALA-CARE CONNECT")
        st.caption("Integrated Healthcare Management and Community Medical Assistance System - Talacogon, Agusan del Sur")
        t1, t2 = st.tabs(["Log in", "Patient sign-up"])
        with t1:
            login_form()
        with t2:
            signup_flow()
        return

    pages = db_pages(user["role"])
    st.sidebar.title("TALA-CARE")
    st.sidebar.write(f"**{user['full_name']}**  \n{user['role']}")
    notification_center(user)
    choice = st.sidebar.radio("Menu", pages)
    if st.sidebar.button("Log out"):
        audit(user, "LOGOUT")
        st.session_state.clear()
        st.rerun()

    # Every page is re-checked against the role's permission list (RBAC)
    if choice not in pages:
        st.error("Access denied.")
        return
    routes = {
        "Dashboard": hospital.dashboard,
        "Patients": hospital.patients,
        "Appointments": hospital.appointments,
        "Doctors & Schedules": hospital.doctors,
        "Medical Records": hospital.records,
        "Hospital Beds": hospital.beds,
        "Pharmacy Inventory": inventory.page,
        "Smart Medicine Finder": finder.page,
        "Financial Assistance": assistance.page,
        "Social Worker Desk": assistance.page,
        "Directory & Map": admin.directory,
        "Reports": admin.reports,
        "Admin": admin.admin,
        "Partner Pharmacy Portal": finder.partner_portal,
        "Patient Home": patient_home,
        "My Appointments": hospital.my_appointments,
        "My Records": hospital.my_records,
    }
    routes[choice](user)


def db_pages(role):
    rows = q("SELECT p.page FROM permissions p JOIN roles r ON r.id=p.role_id WHERE r.name=?", (role,))
    order = PERMISSIONS.get(role, [])
    allowed = {r["page"] for r in rows}
    return [p for p in order if p in allowed]


def login_form():
    with st.form("login"):
        un = st.text_input("Username")
        pw = st.text_input("Password", type="password")
        go = st.form_submit_button("Log in", type="primary")
    if go:
        if time.time() < st.session_state.get("locked_until", 0):
            st.error("Too many failed attempts. Please wait a couple of minutes.")
            return
        row = q("SELECT u.*, r.name AS role FROM users u JOIN roles r ON r.id=u.role_id WHERE u.username=? AND u.active=1", (un.strip(),), one=True)
        if row and verify_password(pw, row["password_hash"], row["salt"]):
            st.session_state["user"] = {k: row[k] for k in ("id", "username", "full_name", "role", "patient_id", "pharmacy_id")}
            st.session_state["last_seen"] = time.time()
            st.session_state["fails"] = 0
            audit(st.session_state["user"], "LOGIN")
            st.rerun()
        else:
            st.session_state["fails"] = st.session_state.get("fails", 0) + 1
            if st.session_state["fails"] >= MAX_FAILS:
                st.session_state["locked_until"] = time.time() + LOCK_SECONDS
                st.session_state["fails"] = 0
            audit(None, "LOGIN_FAILED", un)
            st.error("Wrong username or password.")
    with st.expander("Demo accounts (for testing only - change before real use)"):
        st.code("admin / Admin@123\nreception / Reception@123\ndoctor / Doctor@123\nnurse / Nurse@123\n"
                "pharmacist / Pharma@123\nsocial / Social@123\npartner / Partner@123")


main()
