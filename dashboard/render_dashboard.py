#!/usr/bin/env python3
"""Render the Kindle Rebirth dashboard as a PW3-native grayscale PNG."""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1072, 1448
BLACK, DARK, MID, LIGHT, WHITE = 0, 55, 120, 215, 255

FONT_REGULAR_CANDIDATES = (
    os.environ.get("DASHBOARD_FONT", ""),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)
FONT_BOLD_CANDIDATES = (
    os.environ.get("DASHBOARD_FONT_BOLD", ""),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
)

WEATHER_TEXT = {
    0: "晴",
    1: "大致晴朗",
    2: "多云",
    3: "阴",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "密集毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "米雪",
    80: "阵雨",
    81: "中阵雨",
    82: "强阵雨",
    85: "阵雪",
    86: "强阵雪",
    95: "雷雨",
    96: "雷雨冰雹",
    99: "强雷雨冰雹",
}
WEEKDAYS = "一二三四五六日"


@dataclass
class AgendaItem:
    start: datetime | date
    end: datetime | date
    title: str
    location: str
    all_day: bool


def _font_path(candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError("No CJK font found. Install fonts-noto-cjk or set DASHBOARD_FONT.")


FONT_REGULAR = _font_path(FONT_REGULAR_CANDIDATES)
FONT_BOLD = _font_path(FONT_BOLD_CANDIDATES)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REGULAR, size)


def get_json(url: str, params: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
    query = urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "kindle-rebirth-dashboard/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def fetch_weather(place: dict[str, Any]) -> dict[str, Any]:
    return get_json(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "timezone": place["timezone"],
            "forecast_days": 4,
            "current": [
                "temperature_2m",
                "apparent_temperature",
                "relative_humidity_2m",
                "weather_code",
                "wind_speed_10m",
            ],
            "daily": [
                "weather_code",
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_probability_max",
            ],
        },
    )


def load_calendar(calendar_tz: ZoneInfo, now: datetime) -> list[AgendaItem]:
    calendar_bytes: bytes | None = None
    calendar_file = os.environ.get("ICAL_FILE")
    calendar_url = os.environ.get("ICAL_URL")

    if calendar_file:
        calendar_bytes = Path(calendar_file).read_bytes()
    elif calendar_url:
        if calendar_url.startswith("webcal://"):
            calendar_url = "https://" + calendar_url[len("webcal://") :]
        request = urllib.request.Request(calendar_url, headers={"User-Agent": "kindle-rebirth-dashboard/1.0"})
        with urllib.request.urlopen(request, timeout=25) as response:
            calendar_bytes = response.read()
    else:
        return []

    cal = Calendar.from_ical(calendar_bytes)
    calendar_now = now.astimezone(calendar_tz)
    range_start = datetime.combine(calendar_now.date(), time.min, tzinfo=calendar_tz)
    range_end = range_start + timedelta(days=8)
    events = recurring_ical_events.of(cal).between(range_start, range_end)
    agenda: list[AgendaItem] = []
    for event in events:
        raw_start = event.decoded("DTSTART")
        raw_end = event.decoded("DTEND", default=None)
        title = str(event.get("SUMMARY", "（无标题）")).strip()
        location = str(event.get("LOCATION", "")).strip()
        all_day = isinstance(raw_start, date) and not isinstance(raw_start, datetime)

        if all_day:
            start_value: datetime | date = raw_start
            end_value: datetime | date = raw_end or (raw_start + timedelta(days=1))
        else:
            if raw_start.tzinfo is None:
                raw_start = raw_start.replace(tzinfo=calendar_tz)
            start_value = raw_start.astimezone(calendar_tz)
            if raw_end is None:
                raw_end = raw_start + timedelta(hours=1)
            if raw_end.tzinfo is None:
                raw_end = raw_end.replace(tzinfo=calendar_tz)
            end_value = raw_end.astimezone(calendar_tz)

        agenda.append(AgendaItem(start_value, end_value, title, location, all_day))

    def sort_key(item: AgendaItem) -> datetime:
        if item.all_day:
            return datetime.combine(item.start, time.min, tzinfo=calendar_tz)
        return item.start

    return sorted(agenda, key=sort_key)


def load_notes(path: Path) -> list[str]:
    if not path.exists():
        return []
    notes: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+[.)]\s+", "", line)
        line = re.sub(r"[*_`]+", "", line).strip()
        if line:
            notes.append(line)
    return notes[:6]


def text_width(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.FreeTypeFont) -> int:
    box = draw.textbbox((0, 0), text, font=text_font)
    return box[2] - box[0]


def fit_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, text_font: ImageFont.FreeTypeFont) -> str:
    if text_width(draw, text, text_font) <= max_width:
        return text
    result = text
    while result and text_width(draw, result + "…", text_font) > max_width:
        result = result[:-1]
    return result.rstrip() + "…"


def wrap_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, text_font: ImageFont.FreeTypeFont) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for char in text:
        trial = current + char
        if current and text_width(draw, trial, text_font) > max_width:
            lines.append(current)
            current = char
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def section_title(draw: ImageDraw.ImageDraw, y: int, title: str, detail: str = "") -> None:
    draw.text((48, y), title, fill=BLACK, font=font(38, True))
    if detail:
        detail_font = font(23)
        draw.text((WIDTH - 48 - text_width(draw, detail, detail_font), y + 11), detail, fill=MID, font=detail_font)
    draw.line((48, y + 56, WIDTH - 48, y + 56), fill=BLACK, width=3)


def draw_header(draw: ImageDraw.ImageDraw, now: datetime, clocks: list[dict[str, str]]) -> None:
    draw.rectangle((0, 0, WIDTH, 174), fill=BLACK)
    left_date = f"{now.month}月{now.day}日"
    left_weekday = f"星期{WEEKDAYS[now.weekday()]}"
    draw.text((44, 21), left_date, fill=WHITE, font=font(58, True))
    draw.text((48, 96), left_weekday, fill=LIGHT, font=font(30, True))

    start_x, cell_w = 402, 207
    for index, clock in enumerate(clocks):
        local_now = now.astimezone(ZoneInfo(clock["timezone"]))
        x = start_x + index * cell_w
        draw.text((x, 19), clock["name"], fill=LIGHT, font=font(27, True))
        draw.text((x, 58), local_now.strftime("%H:%M"), fill=WHITE, font=font(50, True))
        if index:
            draw.line((x - 20, 20, x - 20, 125), fill=DARK, width=2)

    shanghai_now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    snapshot = f"时间快照 · 生成于 {shanghai_now.strftime('%H:%M')}（上海时间）"
    snapshot_font = font(20, True)
    snapshot_x = start_x + ((WIDTH - start_x) - text_width(draw, snapshot, snapshot_font)) // 2
    draw.text((snapshot_x, 139), snapshot, fill=LIGHT, font=snapshot_font)


def draw_weather(draw: ImageDraw.ImageDraw, y: int, weather_data: list[tuple[dict[str, Any], dict[str, Any]]]) -> int:
    section_title(draw, y, "天气", "未来 3 日")
    top = y + 70
    card_gap, card_w, card_h = 22, 477, 267
    for index, (place, data) in enumerate(weather_data):
        x = 48 + index * (card_w + card_gap)
        draw.rounded_rectangle((x, top, x + card_w, top + card_h), radius=18, outline=BLACK, width=3)
        current = data["current"]
        daily = data["daily"]
        condition = WEATHER_TEXT.get(int(current["weather_code"]), "未知")
        draw.text((x + 24, top + 14), place["name"], fill=BLACK, font=font(33, True))
        draw.text((x + 22, top + 56), f"{round(current['temperature_2m'])}°", fill=BLACK, font=font(72, True))
        draw.text((x + 143, top + 74), condition, fill=DARK, font=font(31, True))
        draw.text(
            (x + 143, top + 116),
            f"体感 {round(current['apparent_temperature'])}°  湿度 {round(current['relative_humidity_2m'])}%",
            fill=MID,
            font=font(21, True),
        )
        draw.line((x + 22, top + 157, x + card_w - 22, top + 157), fill=LIGHT, width=2)
        day_w = (card_w - 44) // 3
        for day_index in range(1, 4):
            day_date = date.fromisoformat(daily["time"][day_index])
            dx = x + 22 + (day_index - 1) * day_w
            draw.text((dx, top + 170), f"周{WEEKDAYS[day_date.weekday()]}", fill=DARK, font=font(23, True))
            draw.text(
                (dx, top + 205),
                f"{round(daily['temperature_2m_max'][day_index])}°/{round(daily['temperature_2m_min'][day_index])}°",
                fill=BLACK,
                font=font(25, True),
            )
            rain = daily["precipitation_probability_max"][day_index]
            if rain is not None and rain >= 30:
                draw.text((dx, top + 235), f"雨 {round(rain)}%", fill=MID, font=font(20, True))
    return top + card_h


def agenda_label(item: AgendaItem, now: datetime, calendar_tz: ZoneInfo) -> tuple[str, str]:
    item_date = item.start if item.all_day else item.start.date()
    calendar_date = now.astimezone(calendar_tz).date()
    if item_date == calendar_date:
        day = "今天"
    elif item_date == calendar_date + timedelta(days=1):
        day = "明天"
    else:
        day = f"{item_date.month}/{item_date.day} 周{WEEKDAYS[item_date.weekday()]}"
    when = "全天" if item.all_day else item.start.strftime("%H:%M")
    return day, when


def draw_agenda(
    draw: ImageDraw.ImageDraw,
    y: int,
    agenda: list[AgendaItem],
    now: datetime,
    calendar_tz: ZoneInfo,
) -> int:
    section_title(draw, y, "日程", "未来 7 天 · 上海时间")
    row_y = y + 70
    if not agenda:
        draw.text((55, row_y + 28), "暂无日程，或尚未设置 ICAL_URL", fill=MID, font=font(27))
        return row_y + 92

    for index, item in enumerate(agenda[:6]):
        day, when = agenda_label(item, now, calendar_tz)
        draw.text((54, row_y + 5), day, fill=DARK, font=font(25, True))
        draw.text((54, row_y + 40), when, fill=MID, font=font(23, True))
        draw.line((174, row_y + 3, 174, row_y + 69), fill=BLACK if index == 0 else LIGHT, width=3)
        title = fit_text(draw, item.title, 805, font(30, True))
        draw.text((197, row_y + 3), title, fill=BLACK, font=font(30, True))
        if item.location:
            location = fit_text(draw, item.location, 805, font(22, True))
            draw.text((197, row_y + 43), location, fill=MID, font=font(22, True))
        if index < min(len(agenda), 6) - 1:
            draw.line((48, row_y + 82, WIDTH - 48, row_y + 82), fill=LIGHT, width=2)
        row_y += 88
    return row_y


def draw_notes(draw: ImageDraw.ImageDraw, y: int, notes: list[str]) -> int:
    section_title(draw, y, "备忘")
    row_y = y + 68
    if not notes:
        draw.text((55, row_y + 10), "notes.md 目前为空", fill=MID, font=font(30, True))
        return row_y + 55
    body_font = font(28, True)
    for note in notes[:5]:
        lines = wrap_text(draw, note, 910, body_font)[:2]
        draw.ellipse((55, row_y + 13, 66, row_y + 24), fill=BLACK)
        for line_index, line in enumerate(lines):
            draw.text((84, row_y + line_index * 33), line, fill=BLACK, font=body_font)
        row_y += max(44, len(lines) * 37 + 8)
        if row_y > HEIGHT - 55:
            break
    return row_y


def main() -> int:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    display_tz = ZoneInfo(config["display_timezone"])
    calendar_tz = ZoneInfo(config.get("calendar_timezone", config["display_timezone"]))
    now = datetime.now(timezone.utc).astimezone(display_tz)

    weather_data: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for place in config["weather"]:
        weather_data.append((place, fetch_weather(place)))
    agenda = load_calendar(calendar_tz, now)
    notes = load_notes(ROOT / "notes.md")

    image = Image.new("L", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    draw_header(draw, now, config["clocks"])
    weather_bottom = draw_weather(draw, 201, weather_data)
    agenda_bottom = draw_agenda(draw, weather_bottom + 29, agenda, now, calendar_tz)
    notes_y = max(1056, agenda_bottom + 18)
    draw_notes(draw, notes_y, notes)

    shanghai_generated = now.astimezone(ZoneInfo("Asia/Shanghai"))
    generated = shanghai_generated.strftime("数据生成于 %m-%d %H:%M（上海时间）")
    footer_font = font(22, True)
    draw.text((WIDTH - 48 - text_width(draw, generated, footer_font), HEIGHT - 34), generated, fill=MID, font=footer_font)

    output_dir = Path(os.environ.get("OUTPUT_DIR", ROOT.parent / "public"))
    slug = os.environ.get("DASHBOARD_SLUG", "preview").strip("/") or "preview"
    output = output_dir / slug / "dashboard.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG", optimize=True)
    print(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Dashboard generation failed: {exc}", file=sys.stderr)
        raise
