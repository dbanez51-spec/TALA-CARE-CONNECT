"""SIGNATURE FEATURE 1: Smart Medicine Finder and Partner Pharmacy Network."""
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st

from db import q, run, audit, notify, now, today
from modules.common import maps_link, directions_link, haversine_km, show_df
from modules.inventory import available_qty, med_label

STALE_DAYS = 7
CONFIRM_HOURS = 24


def freshness(row):
    """Never present old records as confirmed. Returns (label, level)."""
    lu = datetime.strptime(row["last_updated"], "%Y-%m-%d %H:%M:%S")
    if row.get("confirmed_at"):
        ca = datetime.strptime(row["confirmed_at"], "%Y-%m-%d %H:%M:%S")
        if datetime.now() - ca <= timedelta(hours=CONFIRM_HOURS):
            return f"CONFIRMED by pharmacy on {row['confirmed_at']}", "confirmed"
    if datetime.now() - lu > timedelta(days=STALE_DAYS):
        return f"OLD RECORD (updated {row['last_updated']}) - may be outdated, call first", "stale"
    return f"REPORTED (updated {row['last_updated']}) - not confirmed, please call to verify", "reported"


def search_partners(generic, strength, form, qty, barangay_id=None, user_ll=None):
    sql = """SELECT i.*, p.name AS pharmacy, p.address, p.contact_no, p.operating_hours, p.latitude, p.longitude, p.barangay_id, p.is_demo
             FROM pharmacy_inventory i JOIN pharmacies p ON p.id=i.pharmacy_id
             WHERE p.verification_status='Verified' AND LOWER(i.generic_name)=LOWER(?) AND LOWER(i.strength)=LOWER(?)
               AND LOWER(i.dosage_form)=LOWER(?) AND i.quantity>=? AND (i.expiry_date IS NULL OR i.expiry_date>=?)"""
    params = [generic, strength, form, qty, today()]
    if barangay_id:
        sql += " AND p.barangay_id=?"
        params.append(barangay_id)
    rows = q(sql + " ORDER BY i.price", params)
    for r in rows:
        r["Stock status"], r["_level"] = freshness(r)
        r["Distance (km)"] = None
        if user_ll and r["latitude"] is not None and r["longitude"] is not None:
            r["Distance (km)"] = round(haversine_km(user_ll[0], user_ll[1], r["latitude"], r["longitude"]), 2)
    return rows


def render_results(rows, patient_id, key):
    if not rows:
        st.warning("No verified partner pharmacy currently reports enough stock of this exact medicine. "
                   "You can set an alert below, or ask the hospital social worker/pharmacist for help.")
        return
    st.caption("Prices are estimates. Availability is what pharmacies REPORTED - always contact the pharmacy to confirm before going.")
    df = pd.DataFrame([{
        "Pharmacy": r["pharmacy"] + (" (DEMO)" if r["is_demo"] else ""), "Price (PHP)": r["price"],
        "Reported qty": r["quantity"], "Stock status": r["Stock status"], "Address": r["address"],
        "Contact": r["contact_no"], "Hours": r["operating_hours"], "Distance (km)": r["Distance (km)"],
        "Batch": r["batch_no"], "Expiry": r["expiry_date"]} for r in rows])
    st.subheader("Compare pharmacies")
    st.dataframe(df, width="stretch", hide_index=True)
    if any(r["latitude"] is not None for r in rows):
        st.map(pd.DataFrame([{"lat": r["latitude"], "lon": r["longitude"]} for r in rows if r["latitude"] is not None]))
    for r in rows:
        with st.expander(f"{r['pharmacy']} - PHP {r['price']} - {r['Stock status'].split(' ')[0]}"):
            st.write(f"**Address:** {r['address']}  \n**Contact:** {r['contact_no']}  \n**Hours:** {r['operating_hours']}")
            m, d = maps_link(r["address"], r["latitude"], r["longitude"]), directions_link(r["address"], r["latitude"], r["longitude"])
            if m:
                st.markdown(f"[View on Google Maps]({m}) | [Get directions]({d})")
            if patient_id:
                c1, c2 = st.columns([1, 2])
                rq = c1.number_input("Quantity to reserve", 1, int(r["quantity"]), 1, key=f"{key}rq{r['id']}")
                if c2.button("Request reservation (needs pharmacy approval)", key=f"{key}rs{r['id']}"):
                    run("INSERT INTO reservations(patient_id,pharmacy_id,inventory_id,quantity) VALUES(?,?,?,?)",
                        (patient_id, r["pharmacy_id"], r["id"], rq))
                    for u in q("SELECT id FROM users WHERE pharmacy_id=?", (r["pharmacy_id"],)):
                        notify(u["id"], "New reservation request from a patient. Please review in your portal.")
                    st.success("Reservation requested. This is NOT guaranteed until the pharmacy approves it.")


def patient_view(user):
    pid = user["patient_id"]
    st.header("Smart Medicine Finder")
    st.write("Find your prescribed medicine in verified partner pharmacies when the hospital cannot supply it.")

    # Auto-activation: prescriptions the hospital cannot fill
    rx_items = q("""SELECT pi.*, m.generic_name, m.brand_name, m.strength, m.dosage_form, p.id AS rx_id FROM prescription_items pi
                    JOIN prescriptions p ON p.id=pi.prescription_id JOIN medicines m ON m.id=pi.medicine_id
                    WHERE p.patient_id=? AND p.status='Needs Outside Pharmacy' AND pi.dispensed_qty<pi.quantity""", (pid,))
    if rx_items:
        st.error("Some of your prescribed medicines are not fully available in the hospital pharmacy.")
        for i in rx_items:
            need = i["quantity"] - i["dispensed_qty"]
            with st.expander(f"Searching: {med_label(i)} (need {need})", expanded=True):
                st.write(f"Hospital has {available_qty(i['medicine_id'])} usable units.")
                render_results(search_partners(i["generic_name"], i["strength"], i["dosage_form"], need), pid, f"rx{i['id']}")
                st.info("Do not change brand or strength on your own. If you need an alternative, ask your doctor and pharmacist to review it.")
                if st.button("Ask pharmacist/doctor to review an alternative", key=f"alt{i['id']}"):
                    for u in q("SELECT u.id FROM users u JOIN roles r ON r.id=u.role_id WHERE r.name='Hospital Pharmacist'"):
                        notify(u["id"], f"Patient requested alternative review for {med_label(i)} (Rx #{i['rx_id']}).")
                    st.success("Request sent. A pharmacist and your prescribing doctor must review it - nothing is substituted automatically.")

    st.subheader("Search manually")
    meds = q("SELECT DISTINCT generic_name, strength, dosage_form FROM pharmacy_inventory ORDER BY generic_name")
    from modules.common import barangay_options
    bar = barangay_options()
    c1, c2, c3 = st.columns(3)
    gen = c1.text_input("Generic name", placeholder="e.g. Paracetamol")
    stg = c2.text_input("Strength", placeholder="e.g. 500 mg")
    frm = c3.selectbox("Form", ["Tablet", "Capsule", "Syrup", "Suspension", "Injection", "Ointment", "Drops", "Other"])
    c1, c2 = st.columns(2)
    qty = c1.number_input("Quantity needed", 1, 10000, 1)
    bsel = c2.selectbox("Filter by barangay", ["All"] + list(bar.keys()))
    with st.expander("Optional: sort by distance (enter your coordinates)"):
        lat = st.number_input("Your latitude", value=0.0, format="%.6f")
        lon = st.number_input("Your longitude", value=0.0, format="%.6f")
        st.caption("Tip: on Google Maps, long-press your location to see the coordinates.")
    if st.button("Search partner pharmacies", type="primary"):
        if not gen.strip() or not stg.strip():
            st.error("Enter the generic name and strength.")
        else:
            ll = (lat, lon) if (lat or lon) else None
            rows = search_partners(gen.strip(), stg.strip(), frm, qty, bar.get(bsel), ll)
            if ll:
                rows.sort(key=lambda r: (r["Distance (km)"] is None, r["Distance (km)"] or 0))
            st.session_state["manual_results"] = (rows, gen.strip(), stg.strip(), frm)
            audit(user, "FINDER_SEARCH", gen)
    if "manual_results" in st.session_state:
        rows, g, s, f = st.session_state["manual_results"]
        render_results(rows, pid, "man")
        if st.button("Notify me when this medicine becomes available"):
            run("INSERT INTO medicine_watches(patient_id,generic_name,strength,dosage_form) VALUES(?,?,?,?)", (pid, g, s, f))
            st.success("Alert saved. You'll get a notification when a verified partner pharmacy updates stock.")

    st.subheader("My reservations")
    res = q("""SELECT r.id, p.name AS pharmacy, i.generic_name, i.strength, r.quantity, r.status, r.pharmacy_note, r.created_at
               FROM reservations r JOIN pharmacies p ON p.id=r.pharmacy_id JOIN pharmacy_inventory i ON i.id=r.inventory_id
               WHERE r.patient_id=? ORDER BY r.id DESC""", (pid,))
    show_df(res, "No reservations yet.")
    done = [r for r in res if r["status"] in ("Approved", "Picked Up")]
    if done:
        sel = st.selectbox("Give feedback on a reservation", done, format_func=lambda r: f"#{r['id']} {r['pharmacy']} {r['generic_name']}")
        fb = st.radio("Did you get the medicine?", ["Yes, I got it", "No, it was not available"], horizontal=True)
        if st.button("Submit feedback (optional)"):
            run("UPDATE reservations SET feedback=? WHERE id=?", (fb, sel["id"]))
            st.success("Thank you. Feedback helps keep pharmacy information accurate.")


def staff_view(user):
    """Hospital pharmacist / admin: check hospital stock, then partners."""
    st.header("Smart Medicine Finder (staff view)")
    meds = q("SELECT * FROM medicines ORDER BY generic_name")
    if not meds:
        st.info("No hospital medicines in catalog.")
        return
    m = st.selectbox("Prescribed medicine", meds, format_func=med_label)
    qty = st.number_input("Quantity", 1, 10000, 1)
    av = available_qty(m["id"])
    if av >= qty:
        st.success(f"Hospital pharmacy can fulfil this ({av} usable units, non-expired).")
    else:
        st.error(f"Hospital pharmacy cannot fulfil this: usable stock {av}, needed {qty}. Searching partners...")
        render_results(search_partners(m["generic_name"], m["strength"], m["dosage_form"], qty), None, "stf")
        st.info("Any alternative medicine needs review by a pharmacist AND the prescribing doctor. This system never substitutes automatically.")


def page(user):
    patient_view(user) if user["role"] == "Patient" else staff_view(user)


# ------------------------------------------------------------------ Partner pharmacy portal
def notify_watchers(generic, strength, form):
    for w in q("SELECT w.id, u.id AS uid FROM medicine_watches w JOIN users u ON u.patient_id=w.patient_id WHERE LOWER(w.generic_name)=LOWER(?) AND LOWER(w.strength)=LOWER(?) AND LOWER(w.dosage_form)=LOWER(?)",
               (generic, strength, form)):
        notify(w["uid"], f"A partner pharmacy reported stock for {generic} {strength} {form}. Open the Smart Medicine Finder to check.")
        run("DELETE FROM medicine_watches WHERE id=?", (w["id"],))


def partner_portal(user):
    st.header("Partner Pharmacy Portal")
    ph = q("SELECT * FROM pharmacies WHERE id=?", (user["pharmacy_id"],), one=True)
    if not ph:
        st.error("Your account is not linked to a pharmacy. Contact the administrator.")
        return
    st.caption(f"{ph['name']} - status: **{ph['verification_status']}**")
    if ph["verification_status"] != "Verified":
        st.warning("Your pharmacy is not verified yet. Patients cannot see your stock until the hospital administrator verifies your license.")
    stale = q("SELECT COUNT(*) n FROM pharmacy_inventory WHERE pharmacy_id=? AND last_updated<?",
              (ph["id"], (datetime.now() - timedelta(days=STALE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")), one=True)["n"]
    if stale:
        st.warning(f"Reminder: {stale} inventory record(s) have not been updated in {STALE_DAYS}+ days. Please update them.")
    t1, t2, t3, t4 = st.tabs(["Inventory", "Add / update stock", "Reservation requests", "Pharmacy profile"])
    with t1:
        inv = q("SELECT id, generic_name, brand_name, strength, dosage_form, quantity, price, batch_no, expiry_date, last_updated, confirmed_at FROM pharmacy_inventory WHERE pharmacy_id=? ORDER BY generic_name", (ph["id"],))
        show_df(inv, "No stock records yet.")
        if inv:
            sel = st.selectbox("Confirm that this stock is on the shelf right now", inv, format_func=lambda r: f"{r['generic_name']} {r['strength']} (qty {r['quantity']})")
            if st.button("Confirm availability now"):
                run("UPDATE pharmacy_inventory SET confirmed_at=?, last_updated=? WHERE id=?", (now(), now(), sel["id"]))
                audit(user, "PHARMACY_CONFIRM", str(sel["id"]))
                st.rerun()
    with t2:
        with st.form("pinv", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            g = c1.text_input("Generic name *")
            b = c2.text_input("Brand name")
            s = c3.text_input("Strength/dosage *")
            c1, c2, c3, c4 = st.columns(4)
            f = c1.selectbox("Form", ["Tablet", "Capsule", "Syrup", "Suspension", "Injection", "Ointment", "Drops", "Other"])
            qty = c2.number_input("Quantity", 0, 1000000, 0)
            price = c3.number_input("Price (PHP)", 0.0, 1000000.0, 0.0, 0.5)
            bn = c4.text_input("Batch no.")
            ex = st.date_input("Expiry date", value=None)
            if st.form_submit_button("Save stock"):
                if not g.strip() or not s.strip():
                    st.error("Generic name and strength are required.")
                elif ex and ex < datetime.now().date():
                    st.error("That batch is already expired.")
                else:
                    old = q("""SELECT id, quantity FROM pharmacy_inventory WHERE pharmacy_id=? AND LOWER(generic_name)=LOWER(?) AND LOWER(strength)=LOWER(?)
                               AND LOWER(dosage_form)=LOWER(?) AND COALESCE(brand_name,'')=? AND COALESCE(batch_no,'')=?""",
                            (ph["id"], g.strip(), s.strip(), f, b.strip(), bn.strip()), one=True)
                    status = "Available" if qty > 0 else "Out of stock"
                    if old:
                        run("UPDATE pharmacy_inventory SET quantity=?, price=?, expiry_date=?, last_updated=?, confirmed_at=NULL, status=? WHERE id=?",
                            (qty, price, ex.isoformat() if ex else None, now(), status, old["id"]))
                        iid, oq = old["id"], old["quantity"]
                    else:
                        iid = run("""INSERT INTO pharmacy_inventory(pharmacy_id,generic_name,brand_name,strength,dosage_form,quantity,price,batch_no,expiry_date,last_updated,status)
                                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (ph["id"], g.strip(), b.strip(), s.strip(), f, qty, price, bn.strip(), ex.isoformat() if ex else None, now(), status))
                        oq = 0
                    run("INSERT INTO pharmacy_stock_updates(pharmacy_id,inventory_id,old_qty,new_qty,updated_by,created_at) VALUES(?,?,?,?,?,?)",
                        (ph["id"], iid, oq, qty, user["id"], now()))
                    if qty > 0 and ph["verification_status"] == "Verified":
                        notify_watchers(g.strip(), s.strip(), f)
                    st.success("Stock saved with current date/time.")
    with t3:
        res = q("""SELECT r.id, pt.full_name AS patient, pt.contact_no, i.generic_name, i.strength, r.quantity, r.status, r.created_at
                   FROM reservations r JOIN patients pt ON pt.id=r.patient_id JOIN pharmacy_inventory i ON i.id=r.inventory_id
                   WHERE r.pharmacy_id=? ORDER BY r.id DESC""", (ph["id"],))
        show_df(res, "No reservation requests.")
        open_ = [r for r in res if r["status"] in ("Requested", "Approved")]
        if open_:
            c1, c2, c3 = st.columns([3, 2, 2])
            sel = c1.selectbox("Request", open_, format_func=lambda r: f"#{r['id']} {r['patient']} - {r['generic_name']} x{r['quantity']} ({r['status']})")
            act = c2.selectbox("Action", ["Approved", "Declined", "Picked Up"])
            note = c3.text_input("Note to patient")
            if st.button("Apply"):
                if act == "Picked Up":
                    run("UPDATE pharmacy_inventory SET quantity=MAX(quantity-?,0), last_updated=? WHERE id=(SELECT inventory_id FROM reservations WHERE id=?)",
                        (sel["quantity"], now(), sel["id"]))
                run("UPDATE reservations SET status=?, pharmacy_note=? WHERE id=?", (act, note, sel["id"]))
                u = q("SELECT u.id FROM users u JOIN reservations r ON r.patient_id=u.patient_id WHERE r.id=?", (sel["id"],), one=True)
                if u:
                    notify(u["id"], f"Your reservation #{sel['id']} was {act.lower()} by {ph['name']}.")
                audit(user, "RESERVATION", f"#{sel['id']} {act}")
                st.rerun()
    with t4:
        with st.form("pprofile"):
            addr = st.text_input("Address", ph["address"] or "")
            c1, c2 = st.columns(2)
            con = c1.text_input("Contact number", ph["contact_no"] or "")
            hrs = c2.text_input("Operating hours", ph["operating_hours"] or "")
            c1, c2 = st.columns(2)
            lat = c1.number_input("Latitude (optional)", value=float(ph["latitude"] or 0.0), format="%.6f")
            lon = c2.number_input("Longitude (optional)", value=float(ph["longitude"] or 0.0), format="%.6f")
            if st.form_submit_button("Save profile"):
                run("UPDATE pharmacies SET address=?, contact_no=?, operating_hours=?, latitude=?, longitude=? WHERE id=?",
                    (addr, con, hrs, lat or None, lon or None, ph["id"]))
                st.success("Saved.")
        st.write(f"License no.: {ph['license_no'] or '-'} (verification is done by the hospital administrator)")
