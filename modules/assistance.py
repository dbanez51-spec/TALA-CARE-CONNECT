"""SIGNATURE FEATURE 2: Patient Financial Assistance Navigator + Social Worker desk."""
import pandas as pd
import streamlit as st

from db import q, run, audit, notify, now
from modules.common import show_df, maps_link
from modules.finder import STALE_DAYS

EXPENSE_TYPES = ["Medicines", "Laboratory / diagnostics", "Hospitalization", "Surgery / procedure", "Other healthcare expense"]
STATUSES = ["Not Yet Applied", "Preparing Requirements", "Referred to Social Worker", "Application Submitted",
            "Under Evaluation", "Approved", "Rejected", "Completed"]
PATIENT_SETTABLE = ["Not Yet Applied", "Preparing Requirements", "Application Submitted"]
DISCLAIMER = ("These are possible options only, NOT approval guarantees. The agency decides eligibility. "
              "Information marked 'not yet verified' must be confirmed with the agency or the hospital social worker.")


def programs_for(expense_type, has_philhealth):
    progs = q("""SELECT pr.*, o.name AS org, o.address, o.contact_no, o.website, o.last_verified AS org_verified
                 FROM assistance_programs pr JOIN assistance_organizations o ON o.id=pr.org_id ORDER BY o.level, o.name""")
    key = {"Medicines": "medicine", "Laboratory / diagnostics": "lab", "Hospitalization": "hospital",
           "Surgery / procedure": "procedure"}.get(expense_type, "")
    out = []
    for p in progs:
        cats = (p["expense_categories"] or "").lower()
        local = "confirm" in cats or "to be confirmed" in cats
        if not key or key in cats or local or p["org"].startswith("PhilHealth"):
            out.append(p)
    return out


def show_program(p):
    verified = p["last_verified"] or p["org_verified"]
    with st.expander(f"{p['org']} - {p['name']}"):
        st.write(f"**Eligible expenses:** {p['expense_categories'] or '-'}")
        st.write(f"**General eligibility:** {p['general_eligibility'] or '-'}")
        reqs = [r["requirement"] for r in q("SELECT requirement FROM assistance_requirements WHERE program_id=?", (p["id"],))]
        st.write("**Required documents:** " + ("; ".join(reqs) if reqs else "not yet listed"))
        st.write(f"**How to apply:** {p['application_process'] or '-'}")
        st.write(f"**Office address:** {p['address'] or 'Not yet verified - ask the social worker'}")
        st.write(f"**Contact:** {p['contact_no'] or 'Not yet verified'}")
        if p["website"]:
            st.write(f"**Official website:** {p['website']}")
        if p["address"]:
            st.markdown(f"[Open in Google Maps]({maps_link(p['address'])})")
        if verified:
            st.write(f"**Last verified:** {verified}")
        else:
            st.warning("Last verification date: NOT YET VERIFIED. Confirm with the agency before relying on this.")


def patient_view(user):
    pid = user["patient_id"]
    st.header("Financial Assistance Navigator")
    st.info(DISCLAIMER)
    t1, t2, t3 = st.tabs(["1. Tell us your situation", "2. My applications", "3. Assistance directory"])
    with t1:
        with st.form("assess", clear_on_submit=True):
            et = st.selectbox("Type of medical expense", EXPENSE_TYPES)
            c1, c2 = st.columns(2)
            total = c1.number_input("Estimated total expense (PHP)", 0.0, 100000000.0, 0.0, 100.0)
            afford = c2.number_input("Amount you can afford (PHP)", 0.0, 100000000.0, 0.0, 100.0)
            atype = st.selectbox("Type of assistance needed", ["Medicines", "Laboratory", "Hospital bill", "Other / not sure"])
            c1, c2 = st.columns(2)
            ph = c1.checkbox("I already have PhilHealth coverage")
            ap = c2.checkbox("I already applied for assistance somewhere")
            share = st.checkbox("I allow the hospital social worker to review my information to help with referrals")
            ok = st.form_submit_button("Find possible assistance")
        if ok:
            if total <= 0:
                st.error("Please enter the estimated total expense.")
            else:
                short = max(total - afford, 0)
                aid = run("""INSERT INTO patient_assistance_applications(patient_id,expense_type,total_expense,can_afford,shortage,assistance_type,has_philhealth,already_applied,share_consent,updated_at)
                             VALUES(?,?,?,?,?,?,?,?,?,?)""", (pid, et, total, afford, short, atype, int(ph), int(ap), int(share), now()))
                audit(user, "ASSISTANCE_ASSESSMENT", f"app {aid}")
                st.session_state["assist_last"] = (aid, et, ph, short)
        last = st.session_state.get("assist_last")
        if last:
            aid, et, ph, short = last
            st.success(f"Estimated shortage: PHP {short:,.2f}. Saved as application #{aid}.")
            st.subheader("Possibly relevant programs")
            progs = programs_for(et, ph)
            for p in progs:
                show_program(p)
            if ph:
                st.caption("You said you have PhilHealth - ask the hospital billing/social worker which benefits apply to your case.")
            if et == "Medicines":
                st.subheader("Lower-cost options from partner pharmacies")
                st.caption("Open 'Smart Medicine Finder' in the sidebar to compare prices from verified partner pharmacies.")
    with t2:
        apps = q("SELECT * FROM patient_assistance_applications WHERE patient_id=? ORDER BY id DESC", (pid,))
        if not apps:
            st.caption("No applications yet.")
        for a in apps:
            with st.expander(f"#{a['id']} - {a['expense_type']} - shortage PHP {a['shortage']:,.2f} - {a['status']}"):
                st.progress((STATUSES.index(a["status"]) + 1) / len(STATUSES))
                if a["missing_requirements"]:
                    st.warning(f"Missing requirements noted by social worker: {a['missing_requirements']}")
                if a["social_worker_note"]:
                    st.write(f"Social worker note: {a['social_worker_note']}")
                if a["status"] in PATIENT_SETTABLE:
                    ns = st.selectbox("Update my status", PATIENT_SETTABLE, index=PATIENT_SETTABLE.index(a["status"]), key=f"ps{a['id']}")
                    c1, c2 = st.columns(2)
                    if c1.button("Save status", key=f"sv{a['id']}"):
                        run("UPDATE patient_assistance_applications SET status=?, updated_at=? WHERE id=? AND patient_id=?", (ns, now(), a["id"], pid))
                        st.rerun()
                    if c2.button("Ask a social worker to help", key=f"rf{a['id']}"):
                        run("UPDATE patient_assistance_applications SET status='Referred to Social Worker', share_consent=1, updated_at=? WHERE id=? AND patient_id=?", (now(), a["id"], pid))
                        for u in q("SELECT u.id FROM users u JOIN roles r ON r.id=u.role_id WHERE r.name='Hospital Social Worker'"):
                            notify(u["id"], f"New assistance request #{a['id']} needs review.")
                        st.rerun()
    with t3:
        for p in programs_for("", False):
            show_program(p)


def social_desk(user):
    st.header("Social Worker Desk")
    t1, t2 = st.tabs(["Requests", "Programs & verification"])
    with t1:
        flt = st.multiselect("Filter by status", STATUSES, default=["Not Yet Applied", "Preparing Requirements", "Referred to Social Worker", "Application Submitted", "Under Evaluation"])
        apps = q("""SELECT a.*, p.full_name, p.contact_no, p.patient_code FROM patient_assistance_applications a JOIN patients p ON p.id=a.patient_id ORDER BY a.id DESC""")
        apps = [a for a in apps if a["status"] in flt]
        st.metric("Pending requests shown", len(apps))
        # Social workers only see assistance-related info, not medical records (data minimisation)
        show_df([{"ID": a["id"], "Patient": a["full_name"], "Expense": a["expense_type"], "Shortage": a["shortage"], "Status": a["status"], "Consent": "Yes" if a["share_consent"] else "No"} for a in apps])
        if apps:
            a = st.selectbox("Open request", apps, format_func=lambda x: f"#{x['id']} {x['full_name']} - {x['status']}")
            if not a["share_consent"]:
                st.warning("The patient has NOT consented to share this with social workers/external agencies. Ask for consent before referring.")
            st.write(f"**Contact:** {a['contact_no'] or '-'} | **Total:** PHP {a['total_expense']:,.2f} | **Can afford:** PHP {a['can_afford']:,.2f} | **PhilHealth:** {'Yes' if a['has_philhealth'] else 'No'} | **Already applied:** {'Yes' if a['already_applied'] else 'No'}")
            progs = programs_for(a["expense_type"], bool(a["has_philhealth"]))
            st.write("**Suggested programs:** " + (", ".join(f"{p['org']}" for p in progs) or "none"))
            with st.form(f"swf{a['id']}"):
                ns = st.selectbox("Status", STATUSES, index=STATUSES.index(a["status"]))
                miss = st.text_input("Missing requirements", a["missing_requirements"] or "")
                note = st.text_area("Note to patient", a["social_worker_note"] or "")
                prog = st.selectbox("Record referral to program (optional)", [None] + progs, format_func=lambda p: "-" if p is None else f"{p['org']} - {p['name']}")
                if st.form_submit_button("Save"):
                    run("UPDATE patient_assistance_applications SET status=?, missing_requirements=?, social_worker_note=?, updated_at=? WHERE id=?",
                        (ns, miss, note, now(), a["id"]))
                    if prog:
                        run("INSERT INTO social_worker_referrals(application_id,program_id,social_worker_id,note,created_at) VALUES(?,?,?,?,?)",
                            (a["id"], prog["id"], user["id"], note, now()))
                    u = q("SELECT id FROM users WHERE patient_id=?", (a["patient_id"],), one=True)
                    if u:
                        notify(u["id"], f"Your assistance request #{a['id']} is now: {ns}.")
                    audit(user, "SW_UPDATE", f"app {a['id']} -> {ns}")
                    st.rerun()
    with t2:
        st.caption("Update office details only from official sources, then set the verification date.")
        orgs = q("SELECT * FROM assistance_organizations ORDER BY level, name")
        o = st.selectbox("Organization", orgs, format_func=lambda x: x["name"])
        with st.form("orgedit"):
            addr = st.text_input("Office address", o["address"] or "")
            con = st.text_input("Contact number", o["contact_no"] or "")
            web = st.text_input("Official website", o["website"] or "")
            vdate = st.checkbox("I verified these details against an official source today")
            if st.form_submit_button("Save organization"):
                run("UPDATE assistance_organizations SET address=?, contact_no=?, website=?, last_verified=? WHERE id=?",
                    (addr, con, web, now()[:10] if vdate else o["last_verified"], o["id"]))
                audit(user, "ORG_UPDATE", o["name"])
                st.rerun()
        progs = q("SELECT * FROM assistance_programs WHERE org_id=?", (o["id"],))
        for p in progs:
            with st.expander(f"Program: {p['name']}"):
                with st.form(f"pe{p['id']}"):
                    cats = st.text_input("Eligible expenses", p["expense_categories"] or "")
                    el = st.text_area("General eligibility", p["general_eligibility"] or "")
                    ap = st.text_area("Application process", p["application_process"] or "")
                    reqs = q("SELECT requirement FROM assistance_requirements WHERE program_id=?", (p["id"],))
                    rq = st.text_area("Requirements (one per line)", "\n".join(r["requirement"] for r in reqs))
                    ver = st.checkbox("Verified against official source today")
                    if st.form_submit_button("Save program"):
                        run("UPDATE assistance_programs SET expense_categories=?, general_eligibility=?, application_process=?, last_verified=? WHERE id=?",
                            (cats, el, ap, now()[:10] if ver else p["last_verified"], p["id"]))
                        run("DELETE FROM assistance_requirements WHERE program_id=?", (p["id"],))
                        for line in [l.strip() for l in rq.splitlines() if l.strip()]:
                            run("INSERT INTO assistance_requirements(program_id,requirement) VALUES(?,?)", (p["id"], line))
                        st.rerun()
        with st.form("newprog", clear_on_submit=True):
            nm = st.text_input("Add a new program to this organization")
            if st.form_submit_button("Add program") and nm.strip():
                run("INSERT INTO assistance_programs(org_id,name,general_eligibility) VALUES(?,?,?)", (o["id"], nm.strip(), "Not yet verified."))
                st.rerun()


def page(user):
    patient_view(user) if user["role"] == "Patient" else (social_desk(user) if user["role"] in ("Hospital Social Worker", "Administrator") else st.error("Access denied."))
