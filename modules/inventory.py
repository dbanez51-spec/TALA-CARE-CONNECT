"""Hospital pharmacy inventory (Module 6) with FEFO dispensing."""
from datetime import date, timedelta

import streamlit as st

from db import q, run, get_conn, audit, notify, now, today
from modules.common import show_df


def available_qty(medicine_id):
    """Usable stock = batches that are not expired."""
    r = q("SELECT COALESCE(SUM(quantity),0) n FROM medicine_batches WHERE medicine_id=? AND expiry_date>=?",
          (medicine_id, today()), one=True)
    return r["n"]


def med_label(m):
    brand = f" ({m['brand_name']})" if m.get("brand_name") else ""
    return f"{m['generic_name']}{brand} {m['strength']} {m['dosage_form']}"


def fefo_deduct(medicine_id, qty, user_id, note):
    """First-Expiry-First-Out: take from the batch that expires soonest."""
    left = qty
    with get_conn() as c:
        batches = c.execute(
            "SELECT id, quantity FROM medicine_batches WHERE medicine_id=? AND quantity>0 AND expiry_date>=? ORDER BY expiry_date, id",
            (medicine_id, today())).fetchall()
        if sum(b["quantity"] for b in batches) < qty:
            return False
        for b in batches:
            take = min(left, b["quantity"])
            c.execute("UPDATE medicine_batches SET quantity=quantity-? WHERE id=?", (take, b["id"]))
            c.execute("INSERT INTO inventory_transactions(batch_id,medicine_id,tx_type,quantity,note,user_id,created_at) VALUES(?,?,'OUT',?,?,?,?)",
                      (b["id"], medicine_id, take, note, user_id, now()))
            left -= take
            if left == 0:
                break
    return True


def evaluate_prescription(prescription_id):
    """Check if the hospital can fill every item. Updates status and notifies patient."""
    items = q("SELECT pi.*, m.generic_name FROM prescription_items pi JOIN medicines m ON m.id=pi.medicine_id WHERE prescription_id=?",
              (prescription_id,))
    short = [i for i in items if available_qty(i["medicine_id"]) < (i["quantity"] - i["dispensed_qty"])]
    rx = q("SELECT * FROM prescriptions WHERE id=?", (prescription_id,), one=True)
    if short:
        run("UPDATE prescriptions SET status='Needs Outside Pharmacy' WHERE id=?", (prescription_id,))
        u = q("SELECT id FROM users WHERE patient_id=?", (rx["patient_id"],), one=True)
        if u:
            notify(u["id"], "Some prescribed medicines are not fully available at the hospital pharmacy. "
                            "Open 'Smart Medicine Finder' to search partner pharmacies.")
    return short


def page(user):
    st.header("Hospital Pharmacy Inventory")
    t1, t2, t3, t4, t5 = st.tabs(["Stock overview", "Medicine catalog", "Stock-in", "Dispense prescriptions", "Transactions"])

    with t1:
        meds = q("SELECT * FROM medicines ORDER BY generic_name")
        rows, low, exp = [], [], []
        soon = (date.today() + timedelta(days=90)).isoformat()
        for m in meds:
            a = available_qty(m["id"])
            row = {"Medicine": med_label(m), "Usable qty": a, "Min stock": m["min_stock"],
                   "Alert": "LOW STOCK" if a < m["min_stock"] else "OK"}
            rows.append(row)
        c1, c2 = st.columns(2)
        c1.metric("Medicines in catalog", len(meds))
        c2.metric("Low-stock medicines", sum(1 for r in rows if r["Alert"] != "OK"))
        show_df(rows, "No medicines yet. Add some in the 'Medicine catalog' tab.")
        st.subheader("Batches (FEFO order)")
        b = q("""SELECT m.generic_name||' '||m.strength||' '||m.dosage_form AS medicine, b.batch_no, b.expiry_date, b.quantity,
                 CASE WHEN b.expiry_date<? THEN 'EXPIRED' WHEN b.expiry_date<=? THEN 'EXPIRING SOON (90 days)' ELSE 'OK' END AS alert
                 FROM medicine_batches b JOIN medicines m ON m.id=b.medicine_id ORDER BY b.expiry_date""", (today(), soon))
        show_df(b)

    with t2:
        with st.form("addmed", clear_on_submit=True):
            c1, c2 = st.columns(2)
            g = c1.text_input("Generic name *")
            br = c2.text_input("Brand name (optional)")
            c3, c4, c5, c6 = st.columns(4)
            s = c3.text_input("Strength * (e.g. 500 mg)")
            f = c4.selectbox("Dosage form", ["Tablet", "Capsule", "Syrup", "Suspension", "Injection", "Ointment", "Drops", "Other"])
            u = c5.text_input("Unit", "pc")
            ms = c6.number_input("Minimum stock", 0, 100000, 10)
            if st.form_submit_button("Add medicine"):
                if not g.strip() or not s.strip():
                    st.error("Generic name and strength are required.")
                else:
                    try:
                        run("INSERT INTO medicines(generic_name,brand_name,strength,dosage_form,unit,min_stock) VALUES(?,?,?,?,?,?)",
                            (g.strip(), br.strip(), s.strip(), f, u, ms))
                        audit(user, "ADD_MEDICINE", g)
                        st.success("Medicine added.")
                    except Exception:
                        st.error("That medicine already exists.")
        show_df(q("SELECT id, generic_name, brand_name, strength, dosage_form, unit, min_stock FROM medicines ORDER BY generic_name"))

    with t3:
        meds = q("SELECT * FROM medicines ORDER BY generic_name")
        if not meds:
            st.info("Add a medicine first.")
        else:
            with st.form("stockin", clear_on_submit=True):
                m = st.selectbox("Medicine", meds, format_func=med_label)
                c1, c2, c3 = st.columns(3)
                bn = c1.text_input("Batch number *")
                ex = c2.date_input("Expiration date *", date.today() + timedelta(days=365))
                qty = c3.number_input("Quantity *", 1, 1000000, 1)
                sup = st.text_input("Supplier (optional)")
                if st.form_submit_button("Record stock-in"):
                    if not bn.strip():
                        st.error("Batch number is required.")
                    elif ex < date.today():
                        st.error("Cannot stock-in an already expired batch.")
                    else:
                        sid = None
                        if sup.strip():
                            old = q("SELECT id FROM suppliers WHERE name=?", (sup.strip(),), one=True)
                            sid = old["id"] if old else run("INSERT INTO suppliers(name) VALUES(?)", (sup.strip(),))
                        ex_b = q("SELECT id FROM medicine_batches WHERE medicine_id=? AND batch_no=?", (m["id"], bn.strip()), one=True)
                        if ex_b:
                            run("UPDATE medicine_batches SET quantity=quantity+? WHERE id=?", (qty, ex_b["id"]))
                            bid = ex_b["id"]
                        else:
                            bid = run("INSERT INTO medicine_batches(medicine_id,batch_no,expiry_date,quantity,supplier_id) VALUES(?,?,?,?,?)",
                                      (m["id"], bn.strip(), ex.isoformat(), qty, sid))
                        run("INSERT INTO inventory_transactions(batch_id,medicine_id,tx_type,quantity,note,user_id,created_at) VALUES(?,?,'IN',?,?,?,?)",
                            (bid, m["id"], qty, "Stock-in", user["id"], now()))
                        audit(user, "STOCK_IN", f"{med_label(m)} x{qty}")
                        st.success("Stock recorded.")

    with t4:
        rx = q("""SELECT p.id, pt.full_name, p.status, p.created_at FROM prescriptions p JOIN patients pt ON pt.id=p.patient_id
                  WHERE p.status IN ('Pending','Needs Outside Pharmacy','Partially Dispensed') ORDER BY p.created_at""")
        if not rx:
            st.info("No prescriptions waiting.")
        for r in rx:
            with st.expander(f"Prescription #{r['id']} - {r['full_name']} - {r['status']}"):
                items = q("SELECT pi.*, m.generic_name, m.brand_name, m.strength, m.dosage_form FROM prescription_items pi JOIN medicines m ON m.id=pi.medicine_id WHERE prescription_id=?", (r["id"],))
                for i in items:
                    need = i["quantity"] - i["dispensed_qty"]
                    av = available_qty(i["medicine_id"])
                    st.write(f"**{med_label(i)}** - need {need}, hospital has {av}. Directions: {i['directions'] or '-'}")
                if st.button("Dispense what is available (FEFO)", key=f"disp{r['id']}"):
                    all_done = True
                    for i in items:
                        need = i["quantity"] - i["dispensed_qty"]
                        if need <= 0:
                            continue
                        if fefo_deduct(i["medicine_id"], need, user["id"], f"Dispensed Rx#{r['id']}"):
                            run("UPDATE prescription_items SET dispensed_qty=quantity WHERE id=?", (i["id"],))
                        else:
                            all_done = False
                    pat = q("SELECT patient_id FROM prescriptions WHERE id=?", (r["id"],), one=True)["patient_id"]
                    if all_done:
                        run("UPDATE prescriptions SET status='Dispensed' WHERE id=?", (r["id"],))
                    else:
                        run("UPDATE prescriptions SET status='Needs Outside Pharmacy' WHERE id=?", (r["id"],))
                        evaluate_prescription(r["id"])
                    audit(user, "DISPENSE", f"Rx#{r['id']}")
                    st.rerun()

    with t5:
        show_df(q("""SELECT t.created_at, m.generic_name||' '||m.strength AS medicine, t.tx_type, t.quantity, t.note
                     FROM inventory_transactions t JOIN medicines m ON m.id=t.medicine_id ORDER BY t.id DESC LIMIT 200"""))
