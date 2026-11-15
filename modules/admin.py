"""Administration, directory & map, reports."""
import io
import os
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

import db
from db import q, run, audit, notify, now, today, ROLES, create_user
from modules.common import show_df, barangay_options, maps_link, directions_link, haversine_km, download_buttons
from modules.finder import STALE_DAYS


# ------------------------------------------------------------------ Directory & map
def directory(user):
    st.header("Healthcare Directory - Talacogon, Agusan del Sur")
    st.caption("Only entries added by the administrator from verified sources are shown. Nothing here is invented.")
    bar = barangay_options()
    sel = st.selectbox("Search by barangay", ["All"] + list(bar.keys()))
    bid = bar.get(sel)
    items = []
    sql = "SELECT name, facility_type AS type, address, contact_no, latitude, longitude, last_verified FROM facilities" + (" WHERE barangay_id=?" if bid else "")
    items += q(sql, (bid,) if bid else ())
    psql = "SELECT name, 'Pharmacy' AS type, address, contact_no, latitude, longitude, NULL AS last_verified FROM pharmacies WHERE verification_status='Verified'" + (" AND barangay_id=?" if bid else "")
    items += q(psql, (bid,) if bid else ())
    if not bid:
        items += q("SELECT name, 'Assistance office' AS type, address, contact_no, NULL AS latitude, NULL AS longitude, last_verified FROM assistance_organizations")
    types = sorted({i["type"] for i in items})
    flt = st.multiselect("Facility type", types, default=types)
    items = [i for i in items if i["type"] in flt]
    with st.expander("Optional: estimate distance from your location"):
        lat = st.number_input("Your latitude", value=0.0, format="%.6f", key="dl")
        lon = st.number_input("Your longitude", value=0.0, format="%.6f", key="dn")
    rows = []
    for i in items:
        d = round(haversine_km(lat, lon, i["latitude"], i["longitude"]), 2) if (lat or lon) and i["latitude"] is not None else None
        rows.append({"Name": i["name"], "Type": i["type"], "Address": i["address"] or "not recorded", "Contact": i["contact_no"] or "",
                     "Distance (km, straight line)": d, "Directions": directions_link(i["address"], i["latitude"], i["longitude"]) or ""})
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                     column_config={"Directions": st.column_config.LinkColumn("Directions", display_text="Open map")})
    else:
        st.info("No entries yet. The administrator can add facilities in Admin > Facilities.")
    pts = [{"lat": i["latitude"], "lon": i["longitude"]} for i in items if i["latitude"] is not None]
    if pts:
        st.map(pd.DataFrame(pts))


# ------------------------------------------------------------------ Reports
def reports(user):
    st.header("Reports & Analytics")
    allowed = {
        "Patient statistics": "SELECT sex, COUNT(*) AS patients FROM patients GROUP BY sex",
        "Patients by barangay": "SELECT COALESCE(b.name,a.barangay_text,'Not recorded') AS barangay, COUNT(*) AS patients FROM patient_addresses a LEFT JOIN barangays b ON b.id=a.barangay_id GROUP BY 1 ORDER BY 2 DESC",
        "Appointments": "SELECT a.appt_date, d.full_name AS doctor, a.status, COUNT(*) AS total FROM appointments a JOIN doctors d ON d.id=a.doctor_id GROUP BY 1,2,3 ORDER BY 1 DESC",
        "Doctor schedules": "SELECT d.full_name AS doctor, s.weekday, s.start_time, s.end_time, s.capacity FROM doctor_schedules s JOIN doctors d ON d.id=s.doctor_id ORDER BY 1,2",
        "Bed occupancy": "SELECT status, COUNT(*) AS beds FROM hospital_beds GROUP BY status",
        "Medicine inventory": "SELECT m.generic_name, m.strength, m.dosage_form, COALESCE(SUM(b.quantity),0) AS qty FROM medicines m LEFT JOIN medicine_batches b ON b.medicine_id=m.id GROUP BY m.id",
        "Medicine consumption": "SELECT m.generic_name, m.strength, SUM(t.quantity) AS dispensed FROM inventory_transactions t JOIN medicines m ON m.id=t.medicine_id WHERE t.tx_type='OUT' GROUP BY m.id ORDER BY 3 DESC",
        "Medicine expiration": "SELECT m.generic_name, b.batch_no, b.expiry_date, b.quantity FROM medicine_batches b JOIN medicines m ON m.id=b.medicine_id WHERE b.quantity>0 ORDER BY b.expiry_date",
        "Pharmacy referrals": "SELECT p.name AS pharmacy, r.status, r.feedback, COUNT(*) AS total FROM reservations r JOIN pharmacies p ON p.id=r.pharmacy_id GROUP BY 1,2,3",
        "Financial assistance referrals": "SELECT a.status, a.expense_type, COUNT(*) AS total, ROUND(SUM(a.shortage),2) AS total_shortage FROM patient_assistance_applications a GROUP BY 1,2",
    }
    if user["role"] == "Hospital Social Worker":
        allowed = {k: v for k, v in allowed.items() if k in ("Financial assistance referrals", "Patients by barangay")}
    if user["role"] == "Hospital Pharmacist":
        allowed = {k: v for k, v in allowed.items() if k.startswith(("Medicine", "Pharmacy"))}
    name = st.selectbox("Report", list(allowed.keys()))
    df = pd.DataFrame(q(allowed[name]))
    if df.empty:
        st.info("No data yet for this report.")
    else:
        st.dataframe(df, width="stretch", hide_index=True)
    download_buttons(df, name.replace(" ", "_"))
    audit(user, "REPORT", name)


# ------------------------------------------------------------------ Admin
def admin(user):
    st.header("Administration")
    t = st.tabs(["Users", "Barangays & puroks", "Facilities", "Partner pharmacies", "Audit logs", "Backup & restore"])
    with t[0]:
        with st.form("newuser", clear_on_submit=True):
            c1, c2 = st.columns(2)
            un = c1.text_input("Username *")
            fn = c2.text_input("Full name *")
            c1, c2 = st.columns(2)
            pw = c1.text_input("Temporary password * (min 8 chars)", type="password")
            role = c2.selectbox("Role", [r for r in ROLES if r != "Patient"])
            phs = q("SELECT id, name FROM pharmacies")
            ph = st.selectbox("Pharmacy (only for Partner Pharmacy role)", [None] + phs, format_func=lambda x: "-" if x is None else x["name"])
            if st.form_submit_button("Create user"):
                if not un.strip() or not fn.strip() or len(pw) < 8:
                    st.error("Fill all fields; password needs 8+ characters.")
                elif role == "Partner Pharmacy" and not ph:
                    st.error("Select the pharmacy for this account.")
                else:
                    try:
                        create_user(un.strip(), pw, fn.strip(), role, pharmacy_id=ph["id"] if ph else None)
                        audit(user, "CREATE_USER", f"{un} ({role})")
                        st.success("User created.")
                    except Exception:
                        st.error("Username already exists.")
        users = q("SELECT u.id, u.username, u.full_name, r.name AS role, u.active FROM users u JOIN roles r ON r.id=u.role_id ORDER BY u.id")
        show_df(users)
        c1, c2, c3 = st.columns(3)
        u = c1.selectbox("Select user", users, format_func=lambda x: f"{x['username']} ({x['role']})")
        if c2.button("Activate / deactivate"):
            if u["id"] == user["id"]:
                st.error("You cannot deactivate yourself.")
            else:
                run("UPDATE users SET active=1-active WHERE id=?", (u["id"],))
                audit(user, "TOGGLE_USER", u["username"])
                st.rerun()
        npw = c3.text_input("New password", type="password", key="resetpw")
        if st.button("Reset password") and len(npw) >= 8:
            h, s = db.hash_password(npw)
            run("UPDATE users SET password_hash=?, salt=? WHERE id=?", (h, s, u["id"]))
            audit(user, "RESET_PASSWORD", u["username"])
            st.success("Password reset.")
    with t[1]:
        st.caption("Enter barangays/puroks from the official PSGC/PSA list or the LGU. Demo placeholders can be deleted below.")
        c1, c2 = st.columns(2)
        with c1.form("newbrgy", clear_on_submit=True):
            nm = st.text_input("Barangay name")
            src = st.text_input("Source (e.g. PSGC release / LGU document)")
            if st.form_submit_button("Add barangay") and nm.strip():
                try:
                    run("INSERT INTO barangays(name,verified,source_note) VALUES(?,?,?)", (nm.strip(), int(bool(src.strip())), src))
                    st.rerun()
                except Exception:
                    st.error("Already exists.")
        up = c2.file_uploader("Or upload CSV with a 'name' column", type="csv")
        if up is not None and st.button("Import barangays"):
            df = pd.read_csv(up)
            for n_ in df["name"].dropna().astype(str):
                try:
                    run("INSERT INTO barangays(name,verified,source_note) VALUES(?,1,'CSV import')", (n_.strip(),))
                except Exception:
                    pass
            st.rerun()
        brs = q("SELECT * FROM barangays ORDER BY name")
        show_df(brs)
        if brs:
            b = st.selectbox("Barangay", brs, format_func=lambda x: x["name"])
            pn = st.text_input("Add purok to this barangay")
            if st.button("Add purok") and pn.strip():
                try:
                    run("INSERT INTO puroks(barangay_id,name) VALUES(?,?)", (b["id"], pn.strip()))
                except Exception:
                    st.error("Already exists.")
            if b["name"].startswith("Demo Barangay") and st.button("Delete this demo barangay"):
                try:
                    run("DELETE FROM barangays WHERE id=?", (b["id"],))
                    st.rerun()
                except Exception:
                    st.error("In use; cannot delete.")
    with t[2]:
        bar = barangay_options()
        with st.form("fac", clear_on_submit=True):
            c1, c2 = st.columns(2)
            nm = c1.text_input("Facility name *")
            ft = c2.selectbox("Type", ["Hospital", "Rural Health Unit", "Barangay Health Station", "Partner drugstore", "Government assistance office", "Other"])
            ad = st.text_input("Address (verified)")
            c1, c2, c3 = st.columns(3)
            br = c1.selectbox("Barangay", ["-"] + list(bar.keys()))
            ct = c2.text_input("Contact")
            vf = c3.checkbox("Verified from an official source")
            c1, c2 = st.columns(2)
            la = c1.number_input("Latitude", value=0.0, format="%.6f")
            lo = c2.number_input("Longitude", value=0.0, format="%.6f")
            if st.form_submit_button("Add facility") and nm.strip():
                run("INSERT INTO facilities(name,facility_type,address,barangay_id,contact_no,latitude,longitude,verified,last_verified) VALUES(?,?,?,?,?,?,?,?,?)",
                    (nm.strip(), ft, ad, bar.get(br), ct, la or None, lo or None, int(vf), today() if vf else None))
                st.rerun()
        show_df(q("SELECT id, name, facility_type, address, verified, last_verified FROM facilities"), "No facilities yet.")
    with t[3]:
        bar = barangay_options()
        with st.form("newpharm", clear_on_submit=True):
            c1, c2 = st.columns(2)
            nm = c1.text_input("Pharmacy name *")
            lic = c2.text_input("License no.")
            ad = st.text_input("Address")
            c1, c2, c3 = st.columns(3)
            br = c1.selectbox("Barangay", ["-"] + list(bar.keys()), key="pb")
            ct = c2.text_input("Contact", key="pc")
            hrs = c3.text_input("Operating hours")
            if st.form_submit_button("Register pharmacy") and nm.strip():
                run("INSERT INTO pharmacies(name,address,barangay_id,contact_no,operating_hours,license_no) VALUES(?,?,?,?,?,?)",
                    (nm.strip(), ad, bar.get(br), ct, hrs, lic))
                st.rerun()
        phs = q("SELECT id, name, license_no, verification_status, is_demo FROM pharmacies")
        show_df(phs)
        if phs:
            c1, c2, c3 = st.columns([3, 2, 2])
            p = c1.selectbox("Pharmacy", phs, format_func=lambda x: x["name"])
            vs = c2.selectbox("Set status", ["Pending", "Verified", "Rejected"])
            if c3.button("Update verification"):
                run("UPDATE pharmacies SET verification_status=? WHERE id=?", (vs, p["id"]))
                audit(user, "VERIFY_PHARMACY", f"{p['name']} -> {vs}")
                st.rerun()
        if st.button("Send stock-update reminders to pharmacies with old records"):
            cutoff = (datetime.now() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
            n = 0
            for r in q("SELECT DISTINCT u.id FROM users u JOIN pharmacy_inventory i ON i.pharmacy_id=u.pharmacy_id WHERE i.last_updated<?", (cutoff,)):
                notify(r["id"], "Please update your medicine stock - some records are over 7 days old.")
                n += 1
            st.success(f"Reminders sent to {n} account(s).")
    with t[4]:
        show_df(q("SELECT created_at, username, action, detail FROM audit_logs ORDER BY id DESC LIMIT 500"))
    with t[5]:
        st.caption("Download a copy of the database regularly. On Streamlit Community Cloud the file can be reset when the app restarts, so keep backups!")
        if os.path.exists(db.DB_PATH):
            with open(db.DB_PATH, "rb") as f:
                st.download_button("Download database backup", f.read(), f"talacare_backup_{datetime.now():%Y%m%d_%H%M}.db", "application/octet-stream")
        up = st.file_uploader("Restore from a backup (.db)", type="db")
        if up is not None and st.checkbox("I understand this replaces ALL current data") and st.button("Restore now"):
            data = up.getvalue()
            if not data.startswith(b"SQLite format 3"):
                st.error("That is not a valid SQLite database.")
            else:
                with open(db.DB_PATH, "wb") as f:
                    f.write(data)
                audit(user, "RESTORE_DB")
                st.success("Restored. Please log in again.")
                st.session_state.clear()
                st.rerun()
