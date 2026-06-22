# Mitra AP PTM Tools & Dashboard

This repository contains tools, data cleanup utilities, and the dashboard wrapper for the **Parents Feedback Initiative** (School Education Department, Government of Andhra Pradesh).

## Structure

*   **`dashboard/`**: Contains the Node.js Express server (`server.js`) that signs the JWT token to securely embed the Metabase Dashboard and serves a custom header styled with the AP state emblem and APSWREIS logos.
*   **`data cleanup script /`**: Python scripts for database cleaning.
*   **`school_cleanup/`**: Python utility to clean and retain designated school records.

---

## Setup & Running the Dashboard

### 1. Configure Environment Variables
Inside the `dashboard/` directory, create a `.env` file (this is already ignored by Git to protect secrets):
```env
METABASE_SITE_URL=http://mitrametabase.dev.nidhi.apcfss.in
METABASE_SECRET_KEY=bb38a7b988607d3e8b830ea6fd4e
DASHBOARD_ID=6
PORT=3000
```

### 2. Install Dependencies & Run
Navigate to the `dashboard` directory and run:
```bash
cd dashboard
npm install
npm start
```
Open **http://localhost:3000** in your browser to view the dashboard.
