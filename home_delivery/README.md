![Home Delivery — package tracking for Home Assistant](banner.png)

**Track every package and every letter from one place inside Home Assistant.**

Home Delivery combines **USPS, UPS, and FedEx** tracking, **USPS Informed Delivery** mail scans, and **spoken announcements** when something changes — all from a panel in your sidebar. Everything is configured in the UI; no YAML required.

---

#### Packages

Add a tracking number and the carrier is detected automatically. Each package can include **who it’s for** and **where it’s going** (home, work, grandma’s, etc.). View live status, full tracking history, and refresh on demand.

#### Mail today

Connect your email via IMAP to pull **USPS Informed Delivery** scans. See how many pieces are arriving today and preview them as an animated GIF in the panel and on a Home Assistant camera entity.

#### Smart polling

- **In transit** — checked about every hour  
- **Out for delivery** — checked every **5 minutes**  
- **Delivered** — polling slows until you remove the package  

#### TTS announcements

Optional spoken updates on your media players when:

- Tracking status changes  
- A package goes out for delivery  
- A package is delivered  
- New Informed Delivery mail is found  

Quiet hours keep announcements between the times you choose.

#### Home Assistant entities

The add-on installs a companion integration automatically:

- Active / out-for-delivery / delivered-today counts  
- USPS mail piece count  
- Mail preview camera  
- Per-package sensors with tracking details in attributes  

Use them in dashboards, notifications, and automations.

---

#### Supported carriers

- **USPS** — e.g. `9400111899560438600329`  
- **UPS** — e.g. `1Z999AA10123456784`  
- **FedEx** — e.g. `123456789012`  

---

#### Quick start

1. Install and **Start** this add-on.  
2. Open **Home Delivery** from the sidebar.  
3. Click **Add Package** and enter a tracking number.  
4. Optional: **Settings → Mail** for Informed Delivery, **Settings → TTS** for announcements.  

For IMAP setup, TTS options, polling intervals, and automation examples, see the **Documentation** tab.
