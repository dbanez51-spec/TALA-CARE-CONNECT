"""Supporting hospital modules: dashboard, patients, appointments, doctors, records, beds."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from db import q, run, audit, notify, now, today
from modules.common import patient_options, barangay_options, age_from, show_df
from modules.inventory import med_label, evaluate_prescription

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MIN_PER_PATIENT = 15  # assumption used for the waiting-time estimate


# ------------------------------------------------------------------ Dashboard
def dashboard(user):
    st.header("Hospital Dashboard")
    n = lambda sql, p=(): q(sql, p, one=True)["n"]
    beds_total = n("SELECT COUNT(*) n FROM hospital_beds")
    beds_occ = n("SELECT COUNT(*) n FROM hospital_beds WHERE status='Occupied'")
    beds_av = n("SELECT COUNT(*) n FROM hospital_beds WHERE status='Available'")
    c = st.columns(4)
    c[0].metric("Registered patients", n("SELECT COUNT(*) n FROM patients"))
    c[1].metric("Today's appointments", n("SELECT COUNT(*) n FROM appointments WHERE appt_date=? AND status!='Cancelled'", (today(),)))
    c[2].metric("Waiting patients", n("SELECT COUNT(*) n FROM appointments WHERE appt_date=? AND status IN ('Confirmed','Waiting')", (today(),)))
    wd = date.today().weekday()
    c[3].metric("Doctors on duty today", n("SELECT COUNT(DISTINCT doctor_id) n FROM doctor_schedules s JOIN doctors d ON d.id=s.doctor_id WHERE s.weekday=? AND d.active=1", (wd,)))
    c = st.columns(4)
    c[0].metric("Total beds", beds_total)
    c[1].metric("Occupied beds", beds_occ)
    c[2].metric("Available beds", beds_av)
    c[3].metric("Pending assistance requests", n("SELECT COUNT(*) n FROM patient_assistance_applications WHERE status IN ('Not Yet Applied','Preparing Requirements','Referred to Social Worker')"))
    c = st.columns(4)
    c[0].metric("Total usable medicine stock", n("SELECT COALESCE(SUM(quantity),0) n FROM medicine_batches WHERE expiry_date>=?", (today(),)))
    low = q("""SELECT m.id, m.min_stock, COALESCE((SELECT SUM(quantity) FROM medicine_batches b WHERE b.medicine_id=m.id AND b.expiry_date>=?),0) AS qty FROM medicines m""", (today(),))
    c[1].metric("Low-stock medicines", sum(1 for r in low if r["qty"] < r["min_stock"]))
    soon = (date.today() + timedelta(days=90)).isoformat()
    c[2].metric("Batches expiring in 90 days", n("SELECT COUNT(*) n FROM medicine_batches WHERE quantity>0 AND expiry_date BETWEEN ? AND ?", (today(), soon)))
    c[3].metric("Rx needing outside pharmacy", n("SELECT COUNT(*) n FROM prescriptions WHERE status='Needs Outside Pharmacy'"))
    st.metric("Verified partner pharmacies", n("SELECT COUNT(*) n FROM pharmacies WHERE verification_status='Verified'"))

    st.subheader("Patients by barangay")
    rows = q("""SELECT COALESCE(b.name, a.barangay_text, 'Not recorded') AS barangay, COUNT(*) AS patients
                FROM patient_addresses a LEFT JOIN barangays b ON b.id=a.barangay_id GROUP BY 1 ORDER BY 2 DESC""")
    if rows:
        st.bar_chart(pd.DataFrame(rows).set_index("barangay"))
    else:
        st.caption("No patient addresses yet.")


# ------------------------------------------------------------------ Patients
def register_patient_form(user, key="reg"):
    bar = barangay_options()
    with st.form(key, clear_on_submit=True):
        name = st.text_input("Full name *")
        c1, c2, c3 = st.columns(3)
        bd = c1.date_input("Birthdate *", date(2000, 1, 1), min_value=date(1900, 1, 1), max_value=date.today())
        sex = c2.selectbox("Sex *", ["Female", "Male"])
        contact = c3.text_input("Contact number")
        email = st.text_input("Email")
        c1, c2, c3 = st.columns(3)
        c1.text_input("Province", "Agusan del Sur", disabled=True)
        c2.text_input("Municipality", "Talacogon", disabled=True)
        brg = c3.selectbox("Barangay", list(bar.keys()) or ["(none - ask admin to add barangays)"])
        c1, c2 = st.columns(2)
        purok = c1.text_input("Purok (type if not in list)")
        street = c2.text_input("Street / Sitio")
        full_addr = st.text_input("Complete address (optional - auto-built if empty)")
        c1, c2 = st.columns(2)
        en = c1.text_input("Emergency contact name")
        ec = c2.text_input("Emergency contact number")
        consent = st.checkbox("The patient consents to the collection and processing of personal and health information "
                              "for care and coordination (Data Privacy Act of 2012, RA 10173). *")
        ok = st.form_submit_button("Register patient")
    if not ok:
        return None
    if not name.strip():
        st.error("Full name is required.")
        return None
    if not consent:
        st.error("Patient consent is required to register.")
        return None
    pid = run("INSERT INTO patients(full_name,birthdate,sex,contact_no,email,emergency_name,emergency_contact,consent_given,consent_at) VALUES(?,?,?,?,?,?,?,1,?)",
              (name.strip(), bd.isoformat(), sex, contact, email, en, ec, now()))
    code = f"TC-{date.today().year}-{pid:05d}"
    run("UPDATE patients SET patient_code=? WHERE id=?", (code, pid))
    addr = full_addr.strip() or ", ".join(x for x in [street, f"Purok {purok}" if purok else "", brg, "Talacogon", "Agusan del Sur"] if x)
    run("INSERT INTO patient_addresses(patient_id,barangay_id,barangay_text,purok,street_sitio,complete_address) VALUES(?,?,?,?,?,?)",
        (pid, bar.get(brg), brg, purok, street, addr))
    audit(user, "REGISTER_PATIENT", code)
    st.success(f"Registered. Patient ID: {code}")
    return pid


def patients(user):
    st.header("Patients")
    t1, t2 = st.tabs(["Search patients", "Register new patient"])
    with t1:
        s = st.text_input("Search by name or patient ID")
        rows = q("""SELECT p.patient_code, p.full_name, p.birthdate, p.sex, p.contact_no, a.complete_address
                    FROM patients p LEFT JOIN patient_addresses a ON a.patient_id=p.id
                    WHERE p.full_name LIKE ? OR p.patient_code LIKE ? ORDER BY p.full_name LIMIT 200""", (f"%{s}%", f"%{s}%"))
        for r in rows:
            r["age"] = age_from(r["birthdate"])
        show_df(rows)
        audit(user, "VIEW_PATIENT_LIST")
    with t2:
        register_patient_form(user)


# ------------------------------------------------------------------ Appointments
def book_appointment(patient_id, doctor_id, dept_id, appt_date, reason):
    """Returns (ok, message). Enforces schedule, capacity and duplicate rules."""
    if appt_date < date.today():
        return False, "Cannot book a past date."
    sch = q("SELECT * FROM doctor_schedules WHERE doctor_id=? AND weekday=?", (doctor_id, appt_date.weekday()), one=True)
    if not sch:
        return False, f"The doctor has no clinic on {WEEKDAYS[appt_date.weekday()]}."
    used = q("SELECT COUNT(*) n FROM appointments WHERE doctor_id=? AND appt_date=? AND status!='Cancelled'", (doctor_id, appt_date.isoformat()), one=True)["n"]
    if used >= sch["capacity"]:
        return False, "The doctor is fully booked on that date. Please choose another date."
    dup = q("SELECT id FROM appointments WHERE patient_id=? AND doctor_id=? AND appt_date=? AND status!='Cancelled'",
            (patient_id, doctor_id, appt_date.isoformat()), one=True)
    if dup:
        return False, "This patient already has an appointment with this doctor on that date."
    aid = run("INSERT INTO appointments(patient_id,doctor_id,department_id,appt_date,reason) VALUES(?,?,?,?,?)",
              (patient_id, doctor_id, dept_id, appt_date.isoformat(), reason))
    qn = q("SELECT COALESCE(MAX(queue_no),0)+1 n FROM queues WHERE doctor_id=? AND queue_date=?", (doctor_id, appt_date.isoformat()), one=True)["n"]
    run("INSERT INTO queues(appointment_id,queue_date,doctor_id,queue_no) VALUES(?,?,?,?)", (aid, appt_date.isoformat(), doctor_id, qn))
    wait = (qn - 1) * MIN_PER_PATIENT
    return True, f"Appointment confirmed. Queue number {qn}. Estimated waiting time about {wait} minutes (assuming {MIN_PER_PATIENT} min per patient)."


def booking_form(user, patient_id=None, key="book"):
    depts = q("SELECT * FROM departments ORDER BY name")
    if not depts:
        st.info("No departments yet.")
        return
    popts = patient_options() if patient_id is None else None
    with st.form(key, clear_on_submit=True):
        pid = patient_id
        if popts is not None:
            pl = st.selectbox("Patient", list(popts.keys()))
            pid = popts[pl] if pl else None
        d = st.selectbox("Department", depts, format_func=lambda x: x["name"])
        docs = q("SELECT * FROM doctors WHERE active=1 AND department_id=?", (d["id"],))
        doc = st.selectbox("Doctor", docs, format_func=lambda x: f"{x['full_name']} ({x['specialization']})") if docs else None
        if doc:
            sch = q("SELECT * FROM doctor_schedules WHERE doctor_id=? ORDER BY weekday", (doc["id"],))
            st.caption("Clinic days: " + (", ".join(f"{WEEKDAYS[s['weekday']]} {s['start_time']}-{s['end_time']}" for s in sch) or "none set"))
        dt = st.date_input("Date", date.today() + timedelta(days=1), min_value=date.today())
        reason = st.text_input("Reason for visit")
        ok = st.form_submit_button("Book appointment")
    if ok:
        if not doc or not pid:
            st.error("Please select a patient and doctor. (If no doctor appears, ask the admin to add one.)")
            return
        good, msg = book_appointment(pid, doc["id"], d["id"], dt, reason)
        (st.success if good else st.error)(msg)
        if good:
            audit(user, "BOOK_APPOINTMENT", f"patient {pid}")


def appointments(user):
    st.header("Appointments & Queue")
    t1, t2, t3 = st.tabs(["Queue monitor", "Book appointment", "Appointment history"])
    with t1:
        d = st.date_input("Date", date.today(), key="qd")
        rows = q("""SELECT a.id, qu.queue_no, p.full_name AS patient, dr.full_name AS doctor, a.status, a.reason
                    FROM appointments a JOIN patients p ON p.id=a.patient_id JOIN doctors dr ON dr.id=a.doctor_id
                    LEFT JOIN queues qu ON qu.appointment_id=a.id WHERE a.appt_date=? ORDER BY dr.full_name, qu.queue_no""", (d.isoformat(),))
        show_df(rows, "No appointments for this date.")
        if rows and user["role"] in ("Administrator", "Receptionist", "Nurse", "Doctor"):
            c1, c2, c3 = st.columns([2, 2, 1])
            sel = c1.selectbox("Appointment", rows, format_func=lambda r: f"#{r['id']} Q{r['queue_no']} {r['patient']}")
            ns = c2.selectbox("Set status", ["Confirmed", "Waiting", "In Consultation", "Completed", "Cancelled"])
            if c3.button("Update"):
                run("UPDATE appointments SET status=? WHERE id=?", (ns, sel["id"]))
                audit(user, "APPT_STATUS", f"#{sel['id']} -> {ns}")
                st.rerun()
    if user["role"] in ("Administrator", "Receptionist", "Nurse"):
        with t2:
            booking_form(user)
    with t3:
        rows = q("""SELECT a.id, a.appt_date, p.full_name AS patient, dr.full_name AS doctor, a.status FROM appointments a
                    JOIN patients p ON p.id=a.patient_id JOIN doctors dr ON dr.id=a.doctor_id ORDER BY a.appt_date DESC LIMIT 300""")
        show_df(rows)


def my_appointments(user):
    st.header("My Appointments")
    pid = user["patient_id"]
    t1, t2 = st.tabs(["My appointments", "Book new"])
    with t1:
        rows = q("""SELECT a.id, a.appt_date, dr.full_name AS doctor, a.status, qu.queue_no FROM appointments a
                    JOIN doctors dr ON dr.id=a.doctor_id LEFT JOIN queues qu ON qu.appointment_id=a.id
                    WHERE a.patient_id=? ORDER BY a.appt_date DESC""", (pid,))
        show_df(rows, "You have no appointments yet.")
        up = [r for r in rows if r["status"] in ("Confirmed", "Waiting") and r["appt_date"] >= today()]
        if up:
            sel = st.selectbox("Cancel an appointment", up, format_func=lambda r: f"#{r['id']} {r['appt_date']} {r['doctor']}")
            if st.button("Cancel selected appointment"):
                run("UPDATE appointments SET status='Cancelled' WHERE id=? AND patient_id=?", (sel["id"], pid))
                audit(user, "CANCEL_APPT", str(sel["id"]))
                st.rerun()
            st.caption("To reschedule: cancel, then book a new date.")
    with t2:
        booking_form(user, patient_id=pid, key="pbook")


# ------------------------------------------------------------------ Doctors
def doctors(user):
    st.header("Doctors & Schedules")
    depts = q("SELECT * FROM departments ORDER BY name")
    t1, t2, t3 = st.tabs(["Doctors", "Add doctor", "Schedules"])
    with t1:
        show_df(q("SELECT d.id, d.full_name, d.specialization, dp.name AS department, d.active FROM doctors d LEFT JOIN departments dp ON dp.id=d.department_id"))
    with t2:
        with st.form("adddoc", clear_on_submit=True):
            n = st.text_input("Doctor full name *")
            sp = st.text_input("Specialization")
            dp = st.selectbox("Department", depts, format_func=lambda x: x["name"])
            nd = st.text_input("Department to add (optional)")
            if st.form_submit_button("Save doctor"):
                if nd.strip():
                    try:
                        run("INSERT INTO departments(name) VALUES(?)", (nd.strip(),))
                    except Exception:
                        pass
                if n.strip():
                    run("INSERT INTO doctors(full_name,specialization,department_id) VALUES(?,?,?)", (n.strip(), sp, dp["id"]))
                    audit(user, "ADD_DOCTOR", n)
                    st.success("Doctor added.")
                else:
                    st.error("Name required.")
    with t3:
        docs = q("SELECT * FROM doctors")
        if docs:
            doc = st.selectbox("Doctor", docs, format_func=lambda x: x["full_name"])
            show_df([{"Day": WEEKDAYS[s["weekday"]], "Start": s["start_time"], "End": s["end_time"], "Capacity": s["capacity"]}
                     for s in q("SELECT * FROM doctor_schedules WHERE doctor_id=? ORDER BY weekday", (doc["id"],))])
            with st.form("addsch"):
                c1, c2, c3, c4 = st.columns(4)
                wd = c1.selectbox("Day", range(7), format_func=lambda i: WEEKDAYS[i])
                s = c2.time_input("Start", value=None)
                e = c3.time_input("End", value=None)
                cap = c4.number_input("Capacity", 1, 200, 20)
                if st.form_submit_button("Add schedule"):
                    if not s or not e or e <= s:
                        st.error("Enter a valid start and end time.")
                    else:
                        # schedule conflict prevention: no overlapping time on the same day
                        clash = [x for x in q("SELECT * FROM doctor_schedules WHERE doctor_id=? AND weekday=?", (doc["id"], wd))
                                 if not (e.strftime("%H:%M") <= x["start_time"] or s.strftime("%H:%M") >= x["end_time"])]
                        if clash:
                            st.error("This overlaps an existing schedule for that day.")
                        else:
                            run("INSERT INTO doctor_schedules(doctor_id,weekday,start_time,end_time,capacity) VALUES(?,?,?,?,?)",
                                (doc["id"], wd, s.strftime("%H:%M"), e.strftime("%H:%M"), cap))
                            st.success("Schedule added.")
                            st.rerun()


# ------------------------------------------------------------------ Medical records
def records(user):
    st.header("Electronic Medical Records")
    if user["role"] not in ("Administrator", "Doctor", "Nurse"):
        st.error("Access denied.")
        return
    popts = patient_options()
    if not popts:
        st.info("No patients yet.")
        return
    pl = st.selectbox("Patient", list(popts.keys()))
    pid = popts[pl]
    audit(user, "VIEW_EMR", f"patient {pid}")
    t1, t2 = st.tabs(["History", "New consultation"])
    with t1:
        for r in q("""SELECT r.*, d.full_name AS doctor FROM medical_records r LEFT JOIN doctors d ON d.id=r.doctor_id
                      WHERE r.patient_id=? ORDER BY r.id DESC""", (pid,)):
            dx = ", ".join(x["diagnosis"] for x in q("SELECT diagnosis FROM diagnoses WHERE record_id=?", (r["id"],)))
            with st.expander(f"{r['visit_date']} - {dx or 'no diagnosis'} ({r['doctor'] or 'n/a'})"):
                st.write(f"BP {r['bp'] or '-'} | Temp {r['temperature'] or '-'} | Pulse {r['pulse'] or '-'} | Weight {r['weight_kg'] or '-'} kg")
                st.write(f"**Complaint:** {r['complaint'] or '-'}")
                st.write(f"**Treatment:** {r['treatment'] or '-'}")
                st.write(f"**Lab request:** {r['lab_request'] or '-'}  |  **Result:** {r['lab_result'] or '-'}")
                st.write(f"**Certificate / notes:** {r['certificate_note'] or '-'}")
    with t2:
        docs = q("SELECT * FROM doctors WHERE active=1")
        meds = q("SELECT * FROM medicines ORDER BY generic_name")
        with st.form("emr", clear_on_submit=True):
            doc = st.selectbox("Attending doctor", docs, format_func=lambda x: x["full_name"]) if docs else None
            c1, c2, c3, c4 = st.columns(4)
            bp = c1.text_input("BP (e.g. 120/80)")
            temp = c2.number_input("Temp (C)", 0.0, 45.0, 0.0, 0.1)
            pulse = c3.number_input("Pulse", 0, 250, 0)
            wt = c4.number_input("Weight (kg)", 0.0, 400.0, 0.0, 0.1)
            cc = st.text_area("Complaint / history")
            dx = st.text_input("Diagnosis (separate multiple with commas)")
            tx = st.text_area("Treatment")
            lab = st.text_input("Laboratory request")
            labr = st.text_input("Laboratory result")
            cert = st.text_input("Medical certificate / notes")
            st.markdown("**Prescription** (doctors only; medicines come from the hospital catalog)")
            chosen = st.multiselect("Medicines", meds, format_func=med_label) if user["role"] in ("Doctor", "Administrator") else []
            ok = st.form_submit_button("Save consultation")
        details = {}
        if chosen:
            st.info("Enter quantity & directions for each selected medicine, then press 'Save prescription items'.")
        if ok:
            rid = run("""INSERT INTO medical_records(patient_id,doctor_id,bp,temperature,pulse,weight_kg,complaint,treatment,lab_request,lab_result,certificate_note,visit_date)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (pid, doc["id"] if doc else None, bp, temp or None, pulse or None, wt or None, cc, tx, lab, labr, cert, now()))
            for d in [x.strip() for x in dx.split(",") if x.strip()]:
                run("INSERT INTO diagnoses(record_id,diagnosis) VALUES(?,?)", (rid, d))
            audit(user, "ADD_CONSULTATION", f"patient {pid}")
            st.session_state["last_record"] = (rid, pid, [m["id"] for m in chosen], doc["id"] if doc else None)
            st.success("Consultation saved.")
    lr = st.session_state.get("last_record")
    if lr and lr[1] == pid and lr[2]:
        st.subheader("Prescription items")
        with st.form("rxitems"):
            vals = {}
            for mid in lr[2]:
                m = q("SELECT * FROM medicines WHERE id=?", (mid,), one=True)
                c1, c2 = st.columns([1, 3])
                vals[mid] = (c1.number_input(f"Qty - {med_label(m)}", 1, 10000, 1, key=f"rq{mid}"),
                             c2.text_input("Directions", key=f"rd{mid}"))
            if st.form_submit_button("Save prescription"):
                rxid = run("INSERT INTO prescriptions(patient_id,doctor_id,record_id) VALUES(?,?,?)", (pid, lr[3], lr[0]))
                for mid, (qty, dr) in vals.items():
                    run("INSERT INTO prescription_items(prescription_id,medicine_id,quantity,directions) VALUES(?,?,?,?)", (rxid, mid, qty, dr))
                short = evaluate_prescription(rxid)
                audit(user, "ADD_PRESCRIPTION", f"Rx#{rxid}")
                del st.session_state["last_record"]
                if short:
                    st.warning("Hospital stock is not enough for some items. The patient was notified to use the Smart Medicine Finder.")
                else:
                    st.success("Prescription saved. Hospital pharmacy can fill it.")


def my_records(user):
    st.header("My Records")
    pid = user["patient_id"]
    audit(user, "VIEW_OWN_RECORDS")
    for r in q("SELECT r.*, d.full_name AS doctor FROM medical_records r LEFT JOIN doctors d ON d.id=r.doctor_id WHERE patient_id=? ORDER BY r.id DESC", (pid,)):
        dx = ", ".join(x["diagnosis"] for x in q("SELECT diagnosis FROM diagnoses WHERE record_id=?", (r["id"],)))
        with st.expander(f"{r['visit_date']} - {dx or 'Consultation'}"):
            st.write(f"**Doctor:** {r['doctor'] or '-'}")
            st.write(f"**Treatment:** {r['treatment'] or '-'}")
            st.write(f"**Lab:** {r['lab_request'] or '-'} / {r['lab_result'] or '-'}")
    st.subheader("My prescriptions")
    rx = q("SELECT * FROM prescriptions WHERE patient_id=? ORDER BY id DESC", (pid,))
    if not rx:
        st.caption("No prescriptions yet.")
    for p in rx:
        items = q("SELECT pi.quantity, pi.directions, m.generic_name, m.brand_name, m.strength, m.dosage_form FROM prescription_items pi JOIN medicines m ON m.id=pi.medicine_id WHERE prescription_id=?", (p["id"],))
        with st.expander(f"Prescription #{p['id']} - {p['status']}"):
            for i in items:
                st.write(f"- {med_label(i)} x{i['quantity']} - {i['directions'] or ''}")
            if p["status"] == "Needs Outside Pharmacy":
                st.warning("Not fully available at the hospital. Use 'Smart Medicine Finder' from the sidebar.")


# ------------------------------------------------------------------ Beds
def beds(user):
    st.header("Hospital Beds & Rooms")
    t1, t2, t3 = st.tabs(["Bed board", "Admit / Discharge", "Wards & beds setup"])
    with t1:
        rows = q("""SELECT r.ward, r.room_no, b.bed_no, b.status, p.full_name AS patient FROM hospital_beds b JOIN hospital_rooms r ON r.id=b.room_id
                    LEFT JOIN admissions a ON a.bed_id=b.id AND a.active=1 LEFT JOIN patients p ON p.id=a.patient_id ORDER BY r.ward, r.room_no, b.bed_no""")
        c = st.columns(4)
        for i, s in enumerate(["Available", "Occupied", "Reserved", "Maintenance"]):
            c[i].metric(s, sum(1 for r in rows if r["status"] == s))
        show_df(rows, "No beds set up yet.")
    with t2:
        popts = patient_options()
        free = q("SELECT b.id, r.ward, r.room_no, b.bed_no FROM hospital_beds b JOIN hospital_rooms r ON r.id=b.room_id WHERE b.status IN ('Available','Reserved')")
        if popts and free:
            with st.form("admit", clear_on_submit=True):
                pl = st.selectbox("Patient", list(popts.keys()))
                bed = st.selectbox("Bed", free, format_func=lambda b: f"{b['ward']} / Room {b['room_no']} / {b['bed_no']}")
                why = st.text_input("Reason for admission")
                if st.form_submit_button("Admit patient"):
                    if q("SELECT id FROM admissions WHERE patient_id=? AND active=1", (popts[pl],), one=True):
                        st.error("This patient is already admitted.")
                    else:
                        run("INSERT INTO admissions(patient_id,bed_id,reason,admitted_at) VALUES(?,?,?,?)", (popts[pl], bed["id"], why, now()))
                        run("UPDATE hospital_beds SET status='Occupied' WHERE id=?", (bed["id"],))
                        audit(user, "ADMIT", f"patient {popts[pl]}")
                        st.success("Admitted. Bed availability updated automatically.")
                        st.rerun()
        else:
            st.info("Need at least one patient and one free bed to admit.")
        act = q("SELECT a.id, a.bed_id, p.full_name, r.ward, b.bed_no FROM admissions a JOIN patients p ON p.id=a.patient_id JOIN hospital_beds b ON b.id=a.bed_id JOIN hospital_rooms r ON r.id=b.room_id WHERE a.active=1")
        if act:
            st.subheader("Discharge")
            a = st.selectbox("Admitted patient", act, format_func=lambda x: f"{x['full_name']} ({x['ward']} {x['bed_no']})")
            summ = st.text_input("Discharge summary")
            if st.button("Discharge"):
                run("INSERT INTO discharges(admission_id,summary,discharged_at) VALUES(?,?,?)", (a["id"], summ, now()))
                run("UPDATE admissions SET active=0 WHERE id=?", (a["id"],))
                run("UPDATE hospital_beds SET status='Available' WHERE id=?", (a["bed_id"],))
                audit(user, "DISCHARGE", str(a["id"]))
                st.rerun()
    with t3:
        with st.form("newbed", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            ward, room, bed = c1.text_input("Ward"), c2.text_input("Room no."), c3.text_input("Bed no.")
            if st.form_submit_button("Add bed"):
                if ward and room and bed:
                    rm = q("SELECT id FROM hospital_rooms WHERE ward=? AND room_no=?", (ward, room), one=True)
                    rid = rm["id"] if rm else run("INSERT INTO hospital_rooms(ward,room_no) VALUES(?,?)", (ward, room))
                    try:
                        run("INSERT INTO hospital_beds(room_id,bed_no) VALUES(?,?)", (rid, bed))
                        st.success("Bed added.")
                    except Exception:
                        st.error("Bed already exists.")
        beds_ = q("SELECT b.id, r.ward, r.room_no, b.bed_no, b.status FROM hospital_beds b JOIN hospital_rooms r ON r.id=b.room_id WHERE b.status!='Occupied'")
        if beds_:
            c1, c2, c3 = st.columns([3, 2, 1])
            b = c1.selectbox("Change status of", beds_, format_func=lambda x: f"{x['ward']} {x['room_no']} {x['bed_no']} ({x['status']})")
            ns = c2.selectbox("New status", ["Available", "Reserved", "Maintenance"])
            if c3.button("Apply"):
                run("UPDATE hospital_beds SET status=? WHERE id=?", (ns, b["id"]))
                audit(user, "BED_STATUS", f"{b['id']} -> {ns}")
                st.rerun()
