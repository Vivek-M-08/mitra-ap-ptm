# Node.js Metabase Embed Setup Guide

This guide describes how to configure, run, and customize the Metabase dashboard embed server.

---

## Prerequisites

Make sure you have **Node.js** (v18 or higher recommended) and **npm** installed on your system. You can verify their installation by running:
```bash
node -v
npm -v
```

---

## Getting Started

### 1. Install Dependencies
Navigate to the `dashboard/` directory and install the required npm packages:
```bash
npm install
```
This will install `express`, `jsonwebtoken` (for signing JWT tokens), and `dotenv` (for environment configuration).

### 2. Configure Environment Variables
Create a file named `.env` in the `dashboard/` folder:
```bash
touch .env
```
Open `.env` and fill in your Metabase settings:
```env
# The URL where your Metabase instance is hosted
METABASE_SITE_URL=http://mitrametabase.dev.nidhi.apcfss.in

# The secret key provided in Metabase Admin Settings -> Embedding
METABASE_SECRET_KEY=bb38a7b988607d3e8b830ea6fd4e

# The ID of the dashboard you want to embed
DASHBOARD_ID=6

# The port where this Node server will listen
PORT=3000
```
> **Note:** The `.env` file contains sensitive credentials and is already configured to be ignored by Git via the root `.gitignore`.

### 3. Start the Server
Start the local server using npm:
```bash
npm start
```
Or start it directly with Node:
```bash
node server.js
```

Once started, open your web browser and navigate to:
**[http://localhost:3000](http://localhost:3000)**

---

## Project Structure & How it Works

*   **`server.js`**: The main entry point of the application. It:
    1.  Loads environment configuration using `dotenv`.
    2.  Sets up an Express web server.
    3.  Serves the images inside the `logos/` folder statically.
    4.  Generates a signed JWT token using your Metabase Secret Key and dashboard ID on each page load.
    5.  Renders the HTML template containing the custom header layout and the embed `<iframe>`.
*   **`logos/`**: Static assets directory.
    *   `state.png`: AP Government official emblem (used on the right side).
    *   `apswreis.png`: APSWREIS logo (used on the left side).
*   **`index.html`**: A static reference layout file.

---

## Styling Customizations

### Header Color
The header background color is customized to match the `#f7f7f7` background of the logo PNGs. If you need to change the background or text colors in the future, edit the styling variables in [`server.js`](file:///Users/user/Documents/AI/Mitra-AP-PTM/dashboard/server.js):
```css
.header {
  background: #f7f7f7;   /* Change header background color */
  ...
}
.header-title h1 {
  color: #1e293b;        /* Change title text color */
}
```
