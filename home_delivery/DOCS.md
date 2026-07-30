# Home Delivery

Package tracking and USPS Informed Delivery for Home Assistant.

## Features

- **Multi-carrier tracking** — USPS, UPS, and FedEx with automatic carrier detection
- **USPS Informed Delivery** — View scanned images of incoming mail via IMAP
- **TTS announcements** — Spoken updates for status changes, out-for-delivery, and delivered
- **Adaptive polling** — Hourly by default, every 5 minutes when out for delivery
- **Beautiful UI** — Dark/light themed dashboard with auto-saving settings

## Installation

1. Add this repository to your Home Assistant Add-on Store
2. Install the **Home Delivery** add-on
3. Start the add-on
4. Open the Web UI from the sidebar

The add-on automatically installs a companion integration that creates sensors in Home Assistant.

## Configuration

All configuration is done through the add-on's web interface (Ingress panel). Settings are saved automatically as you make changes.

### Adding Packages

1. Click **Add Package** on the dashboard
2. Enter the tracking number — carrier is auto-detected from the format:
   - **USPS**: 20-22 digits starting with 9 (e.g., `9400111899560438600329`)
   - **UPS**: Starts with 1Z followed by 16 characters (e.g., `1Z999AA10123456784`)
   - **FedEx**: 12 or 15 digits (e.g., `123456789012`)
3. Optionally add who the package is for and the destination (e.g., "Grandma", "Work")
4. Click **Add**

### Mail Settings (USPS Informed Delivery)

To receive mail piece scans:

1. Sign up for [USPS Informed Delivery](https://informeddelivery.usps.com/)
2. Go to **Settings** > **Mail** in the add-on
3. Enter your IMAP server details:
   - Gmail: `imap.gmail.com`, port `993`
   - Outlook: `outlook.office365.com`, port `993`
4. Enter your email credentials
   - For Gmail with 2FA, create an [App Password](https://myaccount.google.com/apppasswords)
5. Enable mail tracking

The add-on checks for the daily Informed Delivery digest email and extracts mail scan images.

### TTS Settings

1. Go to **Settings** > **TTS**
2. Enable TTS announcements
3. Select which events trigger announcements:
   - **Status Changes** — Any tracking update
   - **Out for Delivery** — Package is on the truck
   - **Delivered** — Package has arrived
   - **Mail Arrived** — Informed Delivery found mail
4. Set **Quiet Hours** to prevent announcements during sleep (e.g., 21:00 - 08:00)

TTS uses Home Assistant's TTS services via the Supervisor API. Configure your media players in HA first.

### Polling Intervals

Default polling intervals can be adjusted in **Settings** > **General**:

- **Default Interval**: 3600 seconds (1 hour) for packages in transit
- **Out for Delivery Interval**: 300 seconds (5 minutes) for faster updates on delivery day

## Home Assistant Integration

The add-on automatically installs a custom integration that creates these entities:

| Entity | Type | Description |
|--------|------|-------------|
| `sensor.home_delivery_active_packages` | Sensor | Count of packages in transit |
| `sensor.home_delivery_out_for_delivery` | Sensor | Count of packages out for delivery |
| `sensor.home_delivery_delivered_today` | Sensor | Count of packages delivered today |
| `sensor.home_delivery_usps_mail` | Sensor | Count of mail pieces today |
| `camera.home_delivery_usps_mail_preview` | Camera | Animated GIF of mail scans |
| `sensor.home_delivery_package_*` | Sensor | Per-package status with tracking attributes |

### Package Sensor Attributes

Each package sensor includes these attributes:

- `tracking_number` — Full tracking number
- `carrier` — usps, ups, or fedex
- `recipient` — Who the package is for
- `destination` — Where it's going
- `status_detail` — Detailed status message
- `out_for_delivery` — Boolean
- `delivered` — Boolean
- `tracking_url` — Link to carrier tracking page
- `events` — List of recent tracking events

### Automation Examples

**Announce when any package is delivered:**

```yaml
automation:
  - alias: "Package Delivered Announcement"
    trigger:
      - platform: state
        entity_id: sensor.home_delivery_delivered_today
    condition:
      - condition: template
        value_template: "{{ trigger.to_state.state | int > trigger.from_state.state | int }}"
    action:
      - service: notify.mobile_app
        data:
          title: "Package Delivered"
          message: "A package has been delivered!"
```

**Flash lights when out for delivery:**

```yaml
automation:
  - alias: "Package Out for Delivery"
    trigger:
      - platform: state
        entity_id: sensor.home_delivery_out_for_delivery
    condition:
      - condition: template
        value_template: "{{ trigger.to_state.state | int > 0 }}"
    action:
      - service: light.turn_on
        target:
          entity_id: light.front_porch
        data:
          flash: short
```

## Troubleshooting

### Package not updating

- Check the add-on logs for scraping errors
- Some carrier pages may block automated access temporarily
- Try clicking **Refresh** on the package card

### Mail not showing

- Verify IMAP credentials are correct
- Check that Informed Delivery emails are arriving in the configured folder
- Gmail users must use an App Password if 2FA is enabled

### TTS not playing

- Verify TTS is configured in Home Assistant
- Check that media players are available
- Ensure current time is within the allowed announcement hours

## Privacy

- All scraping happens locally in the add-on container
- No external services beyond carrier tracking websites
- IMAP credentials stored locally in `/config` (add-on config directory)
- No data is sent to third parties

## Support

For issues and feature requests, visit the [GitHub repository](https://github.com/zodyking/Home-Delivery/issues).
