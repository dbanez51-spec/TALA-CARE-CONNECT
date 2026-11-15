"""Small helpers shared by all pages."""
import io
import math
from datetime import date, datetime
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st

from db import q


def age_from(birthdate):
    try:
        b = date.fromisoformat(str(birthdate))
    except ValueError:
        return None
    t = date.today()
    return t.year - b.year - ((t.month, t.day) < (b.month, b.day))


def patient_options():
    rows = q("SELECT id, patient_code, full_name FROM patients ORDER BY full_name")
    return {f"{r['patient_code']} - {r['full_name']}": r["id"] for r in rows}


def barangay_options():
    rows = q("SELECT id, name FROM barangays ORDER BY name")
    return {r["name"]: r["id"] for r in rows}


def maps_link(address=None, lat=None, lon=None):
    """Google Maps link that needs no API key."""
    if lat is not None and lon is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
    if address:
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}"
    return None


def directions_link(address=None, lat=None, lon=None):
    dest = f"{lat},{lon}" if lat is not None and lon is not None else (quote_plus(address) if address else None)
    return f"https://www.google.com/maps/dir/?api=1&destination={dest}" if dest else None


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def show_df(rows, empty="No records found."):
    if not rows:
        st.info(empty)
        return
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def to_excel_bytes(df, sheet="Report"):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet[:30])
    return buf.getvalue()


def to_pdf_bytes(df, title):
    from fpdf import FPDF
    pdf = FPDF(orientation="L")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=8)
    pdf.cell(0, 6, f"Generated {datetime.now():%Y-%m-%d %H:%M}", new_x="LMARGIN", new_y="NEXT")
    if df.empty:
        pdf.cell(0, 8, "No data.")
    else:
        width = (pdf.w - 20) / len(df.columns)
        pdf.set_font("Helvetica", "B", 8)
        for c in df.columns:
            pdf.cell(width, 7, str(c)[:22], border=1)
        pdf.ln()
        pdf.set_font("Helvetica", size=7)
        for _, row in df.iterrows():
            for v in row:
                text = str(v) if v is not None else ""
                text = text.encode("latin-1", "replace").decode("latin-1")
                pdf.cell(width, 6, text[:28], border=1)
            pdf.ln()
    return bytes(pdf.output())


def download_buttons(df, name):
    c1, c2, c3 = st.columns(3)
    c1.download_button("Download Excel", to_excel_bytes(df, name), f"{name}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"x_{name}")
    c2.download_button("Download PDF", to_pdf_bytes(df, name), f"{name}.pdf", "application/pdf", key=f"p_{name}")
    c3.download_button("Download CSV", df.to_csv(index=False), f"{name}.csv", "text/csv", key=f"c_{name}")
