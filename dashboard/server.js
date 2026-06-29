// npm install express jsonwebtoken dotenv
require("dotenv").config();
const express = require("express");
const jwt     = require("jsonwebtoken");
const path    = require("path");

const app = express();

// Serve the logos folder statically
app.use("/logos", express.static(path.join(__dirname, "logos")));

// ── Metabase config ──────────────────────────────────────────────
const METABASE_SITE_URL   = process.env.METABASE_SITE_URL;
const METABASE_SECRET_KEY = process.env.METABASE_SECRET_KEY;
const DASHBOARD_ID        = parseInt(process.env.DASHBOARD_ID || "6", 10);
const PORT                = parseInt(process.env.PORT || "3000", 10);
// ─────────────────────────────────────────────────────────────────

/** Generate a fresh signed URL (token expires in 10 min) */
function getEmbedUrl() {
  // ── Metabase-provided snippet ──────────────────────────────────
  const payload = {
    resource: { dashboard: DASHBOARD_ID },
    params: {},
    exp: Math.round(Date.now() / 1000) + (10 * 60) // 10 minute expiration
  };
  const token = jwt.sign(payload, METABASE_SECRET_KEY);

  const iframeUrl = METABASE_SITE_URL + "/embed/dashboard/" + token +
    "#background=transparent&bordered=false&titled=false";
  // ───────────────────────────────────────────────────────────────

  return iframeUrl;
}

// ── Main route: serves the full page ────────────────────────────
app.get("/", (req, res) => {
  const iframeUrl = getEmbedUrl();

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Parents Feedback Initiative</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }

    body {
      font-family: 'Segoe UI', sans-serif;
      background: #f4f6f9;
      display: flex;
      flex-direction: column;
      height: 100vh;
    }

    /* ── HEADER ── */
    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: #f7f7f7;
      padding: 14px 24px;
      border-bottom: 1px solid #e2e8f0;
      box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }

    .logo-box {
      display: flex;
      align-items: center;
      gap: 10px;
      width: 240px;
    }
    .logo-box.right { justify-content: flex-end; }

    .logo-box img {
      height: 68px;
      width: auto;
      object-fit: contain;
      border-radius: 4px;
    }

    .logo-box span {
      color: #475569;
      font-size: 12px;
      line-height: 1.4;
    }

    .header-title {
      flex: 1;
      text-align: center;
    }
    .header-title h1 {
      color: #1e293b;
      font-size: clamp(16px, 2.2vw, 26px);
      font-weight: 700;
      letter-spacing: 0.5px;
    }
    .header-title p {
      color: #64748b;
      font-size: 12px;
      margin-top: 3px;
    }

    /* ── IFRAME ── */
    .dashboard-wrapper {
      flex: 1;
      overflow: hidden;
    }
    .dashboard-wrapper iframe {
      width: 100%;
      height: 100%;
      border: none;
      display: block;
    }
  </style>
</head>
<body>

  <header class="header">

    <!-- LEFT: State logo -->
    <div class="logo-box left">
      <img
        src="/logos/apswreis.png"
        alt="AP State Logo"
      />
    </div>

    <!-- CENTER: Title -->
    <div class="header-title">
      <h1>Parents Feedback Initiative</h1>
      <p>School Education Department · Data Dashboard</p>
    </div>

    <!-- RIGHT: Company logo -->
    <div class="logo-box right">
      <img
        src="/logos/state.png"
        alt="AP State Logo"
      />
    </div>

  </header>

  <!-- Metabase signed embed -->
  <div class="dashboard-wrapper">
    <iframe
      src="${iframeUrl}"
      frameborder="0"
      allowtransparency
      title="Parents Feedback Initiative Dashboard"
    ></iframe>
  </div>

</body>
</html>`;

  res.send(html);
});

// ── Start ────────────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`✅  Dashboard running at http://localhost:${PORT}`);
});
