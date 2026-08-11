# Kindle Rebirth Dashboard

The renderer creates an exact 1072×1448 8-bit grayscale PNG for Kindle Paperwhite 3.

## Data sources

- Weather: Open-Meteo, Shanghai Pudong and Guildford, Surrey
- Clocks: Shanghai, Los Angeles and London, calculated with IANA time zones
- Calendar: iCloud public calendar URL stored as the `ICAL_URL` GitHub Actions secret; timed events are displayed in Shanghai time
- Notes: edit `dashboard/notes.md`

## Local preview

```sh
python3 -m venv .venv
.venv/bin/pip install -r dashboard/requirements.txt
ICAL_FILE=dashboard/sample-calendar.ics DASHBOARD_SLUG=preview .venv/bin/python dashboard/render_dashboard.py
```

The preview is written to `public/preview/dashboard.png`.

## GitHub configuration

Create these repository Actions secrets:

- `ICAL_URL`: the iCloud public calendar URL (`webcal://` or `https://`)
- `DASHBOARD_SLUG`: a long random URL path, for example 24 random hexadecimal characters

Set Pages source to **GitHub Actions**. The workflow renders every 15 minutes and after changes under `dashboard/`.
