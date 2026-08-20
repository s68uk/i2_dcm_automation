# **i2 DCM Briefing Automation**  
Daily distressed‑debt capital markets briefing generator for **i2 Capital Markets**, running on a **Raspberry Pi 400** and powered by the **Claude API**.

This project fetches public restructuring‑related news, filters it for distressed‑debt relevance, sends the data to Claude for summarisation, and emails a formatted briefing to the i2 team every morning.

---

## **Features**
- Automated daily briefing (via cron)
- Public RSS feed ingestion (no paid subscriptions required)
- Distressed‑debt keyword filtering
- Claude‑generated professional briefing
- Email delivery via SMTP (Microsoft 365 compatible)
- Lightweight Python environment suitable for Raspberry Pi
- Clean separation of code (GitHub) and secrets (Pi `.env`)

---

## **Repository Structure**
```
i2-dcm-briefing/
│
├── i2_dcm_briefing.py        # Main automation script
├── requirements.txt          # Python dependencies
├── README.md                 # Project documentation
├── .gitignore                # Excludes secrets, logs, venv
└── (no secrets committed)
```

---

## **Prerequisites**
- Raspberry Pi 400 running Raspberry Pi OS (Debian-based)
- Python 3.9+
- A Claude API key (provided by your organisation admin)
- SMTP credentials (Microsoft 365 or equivalent)
- Cron enabled on the Pi (default)

---

## **Installation (Raspberry Pi)**

### **1. Clone the repository**
```
cd ~
git clone https://github.com/<your-username>/i2-dcm-briefing.git
cd i2-dcm-briefing
```

### **2. Create and activate a virtual environment**
```
python3 -m venv venv
source venv/bin/activate
```

### **3. Install dependencies**
```
pip install -r requirements.txt
```

---

## **Environment Variables**
Create a `.env` file **on the Pi only** (never commit this):

```
nano ~/i2-dcm-briefing/.env
```

Add:

```
ANTHROPIC_API_KEY=""
SMTP_HOST="smtp.office365.com"
SMTP_PORT="587"
SMTP_USER="your_email"
SMTP_PASS="your_app_password"
BRIEFING_SENDER="your_email"
```

Load these automatically by adding to `.bashrc`:

```
export $(grep -v '^#' ~/i2-dcm-briefing/.env | xargs)
```

Reload shell:

```
source ~/.bashrc
```

---

## **Running the Script Manually**
```
source ~/i2-dcm-briefing/venv/bin/activate
python i2_dcm_briefing.py
```

---

## **Scheduling Daily Execution (Cron)**

Open crontab:

```
crontab -e
```

Add:

```
30 6 * * * /home/pi/i2-dcm-briefing/venv/bin/python /home/pi/i2-dcm-briefing/i2_dcm_briefing.py >> /home/pi/i2-dcm-briefing/briefing.log 2>&1
```

This runs the briefing every day at **06:30 UK time**.

---

## **Updating the Script from GitHub**
```
cd ~/i2-dcm-briefing
git pull
```

If dependencies changed:

```
source venv/bin/activate
pip install -r requirements.txt
```

---

## **Security Notes**
- **Never commit `.env` or API keys**  
- `.gitignore` already excludes secrets, logs, and virtual environments  
- API keys remain **only** on the Raspberry Pi  
- SMTP credentials should be app‑passwords where possible

---

## **Troubleshooting**
### **Check logs**
```
cat ~/i2-dcm-briefing/briefing.log
```

### **Check cron status**
```
systemctl status cron
```

### **Check environment variables**
```
env | grep ANTHROPIC
```

---

## **License**
Internal i2 Capital Markets automation project — not for external distribution
