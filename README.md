# TALA-CARE CONNECT

Integrated Healthcare Management and Community Medical Assistance System for Talacogon, Agusan del Sur.
Built with **Python + Streamlit + SQLite**.

Two signature features:
1. **Smart Medicine Finder & Pharmacy Network**
2. **Patient Financial Assistance Navigator**

## Folder structure
```
tala-care-connect/
├── app.py                  # login, role menu, page routing
├── db.py                   # SQLite tables, seed data, password hashing
├── requirements.txt
├── .streamlit/config.toml  # theme colors
└── modules/
    ├── common.py           # shared helpers, Excel/PDF export, map links
    ├── hospital.py         # dashboard, patients, appointments, doctors, EMR, beds
    ├── inventory.py        # hospital pharmacy (FEFO, alerts, dispensing)
    ├── finder.py           # SIGNATURE 1 + partner pharmacy portal
    ├── assistance.py       # SIGNATURE 2 + social worker desk
    └── admin.py            # users, barangays, facilities, directory, reports, backup
```

## Run on your computer
```
pip install -r requirements.txt
streamlit run app.py
```
The database file `talacare.db` is created automatically on first run.

## Put on GitHub, then Streamlit Community Cloud
1. Create a new GitHub repository (example: `tala-care-connect`).
2. Upload ALL files and folders above (keep the `modules` folder). Do not upload `talacare.db`.
3. Go to https://share.streamlit.io, sign in with GitHub, click **Create app**.
4. Choose your repository, branch `main`, main file path `app.py`, then **Deploy**.

Git commands (optional):
```
git init
git add .
git commit -m "Tala-Care Connect"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/tala-care-connect.git
git push -u origin main
```

## IMPORTANT notes
- **Data can reset on Streamlit Cloud.** SQLite is a local file; the cloud app may restart and lose it.
  Use *Admin > Backup & restore* to download backups. For real use, move to a hosted database (e.g. PostgreSQL/Supabase).
- **Demo accounts** (shown on the login page) are for testing. Change or deactivate them in *Admin > Users*.
- **No invented data.** Barangays, facilities, pharmacies and assistance-office addresses/contacts are NOT pre-filled.
  Demo items are labeled DEMO/placeholder. The administrator must enter verified data from the LGU, PSGC/PSA and official agency sources.
  Assistance programs show "NOT YET VERIFIED" until a social worker confirms them and ticks the verification box.
- Real patient data needs proper security review and a data privacy officer (RA 10173). This is a capstone prototype.

## Demo logins
| Role | Username | Password |
|---|---|---|
| Administrator | admin | Admin@123 |
| Receptionist | reception | Reception@123 |
| Doctor | doctor | Doctor@123 |
| Nurse | nurse | Nurse@123 |
| Hospital Pharmacist | pharmacist | Pharma@123 |
| Social Worker | social | Social@123 |
| Partner Pharmacy | partner | Partner@123 |
| Patient | (use "Patient sign-up" tab) | |

## How to test the two signature features
**Smart Medicine Finder**
1. Sign up as a patient. Log in as `doctor`, open *Medical Records*, pick the patient, choose *Paracetamol*, save the consultation, then enter quantity **100** and save the prescription (hospital has only 30).
2. Log in as the patient: a notification appears and *Smart Medicine Finder* auto-searches partner pharmacies.
3. The result shows "REPORTED - not confirmed". Request a reservation.
4. Log in as `partner`: approve the request, or press *Confirm availability now* (status becomes CONFIRMED for 24 hours).

**Financial Assistance Navigator**
1. As the patient, open *Financial Assistance*, enter expense and affordable amount, press *Find possible assistance*.
2. In *My applications*, press *Ask a social worker to help*.
3. Log in as `social`, open *Social Worker Desk*, update status, missing requirements and notes.

## Safety rules built in
- Stock is always labeled **Confirmed** (within 24 h), **Reported**, or **Old record** (over 7 days), with the update time.
- Medicines are never substituted automatically; alternatives send a review request to pharmacists/doctors.
- Assistance results always say they are not approval guarantees; no one is auto-rejected.
- Social workers see assistance info only, not medical records.
