#!/usr/bin/env python3
"""Generate a self-updating GitHub profile activity card.

Uses only Python's standard library. In GitHub Actions it reads public profile
metadata from the REST API and contribution data from GitHub's GraphQL API.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence
from xml.sax.saxutils import escape

USERNAME = os.getenv("PROFILE_USERNAME", "marcossheredia")
EMAIL = os.getenv("PROFILE_EMAIL", "marcoss.heredia06@gmail.com")
OUTPUT = Path(os.getenv("PROFILE_CARD_OUTPUT", "assets/github-profile/profile-details.svg"))

INK = "#03191E"
SLATE = "#738290"
AMETHYST = "#9882AC"
PLATINUM = "#EBEBEB"
BURGUNDY = "#941C2F"


@dataclass(frozen=True)
class ProfileData:
    public_repos: int
    joined_at: datetime
    current_year_contributions: int
    week_labels: list[date]
    weekly_contributions: list[int]


def request_json(url: str, *, token: str | None = None, payload: dict | None = None) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-readme-card-generator",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API request failed ({exc.code}): {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub API request failed: {exc.reason}") from exc


def fetch_profile_data(username: str, token: str) -> ProfileData:
    user = request_json(f"https://api.github.com/users/{username}", token=token)
    joined_at = datetime.fromisoformat(user["created_at"].replace("Z", "+00:00"))
    public_repos = int(user["public_repos"])

    now = datetime.now(timezone.utc)
    year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    graph_start = now - timedelta(days=364)

    query = """
    query($login: String!, $yearFrom: DateTime!, $graphFrom: DateTime!, $to: DateTime!) {
      user(login: $login) {
        currentYear: contributionsCollection(from: $yearFrom, to: $to) {
          contributionCalendar { totalContributions }
        }
        lastYear: contributionsCollection(from: $graphFrom, to: $to) {
          contributionCalendar {
            weeks {
              firstDay
              contributionDays { contributionCount }
            }
          }
        }
      }
    }
    """
    payload = {
        "query": query,
        "variables": {
            "login": username,
            "yearFrom": year_start.isoformat().replace("+00:00", "Z"),
            "graphFrom": graph_start.isoformat().replace("+00:00", "Z"),
            "to": now.isoformat().replace("+00:00", "Z"),
        },
    }
    result = request_json("https://api.github.com/graphql", token=token, payload=payload)
    if result.get("errors"):
        raise RuntimeError(f"GitHub GraphQL returned errors: {result['errors']}")

    account = result.get("data", {}).get("user")
    if not account:
        raise RuntimeError(f"GitHub GraphQL did not return user {username!r}")

    current_total = int(account["currentYear"]["contributionCalendar"]["totalContributions"])
    weeks = account["lastYear"]["contributionCalendar"]["weeks"]
    week_labels = [date.fromisoformat(week["firstDay"]) for week in weeks]
    weekly = [sum(int(day["contributionCount"]) for day in week["contributionDays"]) for week in weeks]

    return ProfileData(
        public_repos=public_repos,
        joined_at=joined_at,
        current_year_contributions=current_total,
        week_labels=week_labels,
        weekly_contributions=weekly,
    )


def nice_axis_max(values: Sequence[int]) -> int:
    maximum = max(values, default=0)
    if maximum <= 10:
        return 10
    magnitude = 10 ** int(math.floor(math.log10(maximum)))
    normalized = maximum / magnitude
    if normalized <= 1:
        nice = 1
    elif normalized <= 2:
        nice = 2
    elif normalized <= 5:
        nice = 5
    else:
        nice = 10
    candidate = nice * magnitude
    if candidate < maximum:
        candidate = (2 if nice == 1 else 5 if nice == 2 else 10) * magnitude
    return int(candidate)


def chart_points(values: Sequence[int], x: float, y: float, width: float, height: float, axis_max: int) -> list[tuple[float, float]]:
    if not values:
        return [(x, y + height), (x + width, y + height)]
    step = width / max(1, len(values) - 1)
    return [
        (x + index * step, y + height - (value / axis_max) * height)
        for index, value in enumerate(values)
    ]


def smooth_path(points: Sequence[tuple[float, float]]) -> str:
    if not points:
        return ""
    if len(points) == 1:
        return f"M {points[0][0]:.2f} {points[0][1]:.2f}"

    commands = [f"M {points[0][0]:.2f} {points[0][1]:.2f}"]
    for index in range(len(points) - 1):
        p0 = points[index - 1] if index > 0 else points[index]
        p1 = points[index]
        p2 = points[index + 1]
        p3 = points[index + 2] if index + 2 < len(points) else p2
        c1x = p1[0] + (p2[0] - p0[0]) / 6
        c1y = p1[1] + (p2[1] - p0[1]) / 6
        c2x = p2[0] - (p3[0] - p1[0]) / 6
        c2y = p2[1] - (p3[1] - p1[1]) / 6
        commands.append(
            f"C {c1x:.2f} {c1y:.2f}, {c2x:.2f} {c2y:.2f}, {p2[0]:.2f} {p2[1]:.2f}"
        )
    return " ".join(commands)


def choose_label_indices(length: int, count: int = 7) -> list[int]:
    if length <= count:
        return list(range(length))
    return sorted({round(index * (length - 1) / (count - 1)) for index in range(count)})


def render_card(data: ProfileData) -> str:
    width, height = 1200, 340
    chart_x, chart_y, chart_w, chart_h = 458, 84, 634, 191
    axis_max = nice_axis_max(data.weekly_contributions)
    points = chart_points(data.weekly_contributions, chart_x, chart_y, chart_w, chart_h, axis_max)
    line_path = smooth_path(points)
    area_path = f"{line_path} L {chart_x + chart_w:.2f} {chart_y + chart_h:.2f} L {chart_x:.2f} {chart_y + chart_h:.2f} Z"

    labels = choose_label_indices(len(data.week_labels), 7)
    x_labels = []
    for index in labels:
        px = points[index][0]
        text = data.week_labels[index].strftime("%y/%m")
        x_labels.append(f'<text x="{px:.2f}" y="301" text-anchor="middle">{text}</text>')

    tick_count = 8
    y_ticks = []
    guide_lines = []
    for index in range(tick_count + 1):
        value = round(axis_max * (tick_count - index) / tick_count)
        py = chart_y + chart_h * index / tick_count
        guide_lines.append(f'<line x1="1092" y1="{py:.2f}" x2="1103" y2="{py:.2f}"/>')
        y_ticks.append(f'<text x="1108" y="{py + 5:.2f}">{value}</text>')

    joined = data.joined_at.strftime("%d %B %Y").lstrip("0")
    current_year = datetime.now(timezone.utc).year

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">GitHub profile details</title>
  <desc id="desc">{data.current_year_contributions} contributions in {current_year}, {data.public_repos} public repositories, GitHub member since {joined}, and contribution activity chart.</desc>

  <rect x="8" y="8" width="1184" height="324" rx="10" fill="{INK}" stroke="{SLATE}" stroke-width="2"/>

  <!-- Contributions icon -->
  <g transform="translate(55 57)" fill="none" stroke="{SLATE}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
    <path d="M12 2C6 2 2 6.6 2 12.4c0 4.7 3 8.7 7.2 10.1.5.1.7-.2.7-.5v-2c-2.9.6-3.5-1.2-3.5-1.2-.5-1.2-1.2-1.5-1.2-1.5-1-.7.1-.7.1-.7 1.1.1 1.7 1.1 1.7 1.1 1 1.7 2.6 1.2 3.2.9.1-.7.4-1.2.7-1.5-2.3-.3-4.7-1.2-4.7-5.1 0-1.1.4-2.1 1.1-2.8-.1-.3-.5-1.3.1-2.8 0 0 .9-.3 2.9 1.1a10.1 10.1 0 0 1 5.2 0c2-1.4 2.9-1.1 2.9-1.1.6 1.5.2 2.5.1 2.8.7.7 1.1 1.7 1.1 2.8 0 3.9-2.4 4.8-4.7 5.1.4.3.7.9.7 1.8V22c0 .3.2.6.7.5A10.6 10.6 0 0 0 22 12.4C22 6.6 18 2 12 2Z"/>
  </g>
  <text x="91" y="79" fill="{PLATINUM}" font-family="Segoe UI, Arial, sans-serif" font-size="22">{data.current_year_contributions} Contributions in {current_year}</text>

  <!-- Repository icon -->
  <g transform="translate(56 112)" fill="none" stroke="{SLATE}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
    <rect x="3" y="2" width="18" height="22" rx="2"/>
    <path d="M7 7h10M7 12h10M8 24v4l4-2 4 2v-4"/>
  </g>
  <text x="91" y="134" fill="{PLATINUM}" font-family="Segoe UI, Arial, sans-serif" font-size="22">{data.public_repos} Public Repos</text>

  <!-- Calendar icon -->
  <g transform="translate(56 167)" fill="none" stroke="{SLATE}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
    <rect x="2" y="5" width="23" height="20" rx="5"/>
    <path d="M8 2v6M19 2v6M2 11h23"/>
    <path d="M8 16h4M15 16h4M8 21h4"/>
  </g>
  <text x="91" y="189" fill="{PLATINUM}" font-family="Segoe UI, Arial, sans-serif" font-size="22">Joined GitHub {escape(joined)}</text>

  <!-- Email icon -->
  <g transform="translate(56 222)" fill="none" stroke="{SLATE}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
    <rect x="2" y="4" width="25" height="19" rx="3"/>
    <path d="m4 7 10.5 8L25 7"/>
  </g>
  <text x="91" y="244" fill="{PLATINUM}" font-family="Segoe UI, Arial, sans-serif" font-size="22">{escape(EMAIL)}</text>

  <line x1="56" y1="284" x2="391" y2="284" stroke="{BURGUNDY}" stroke-width="4" stroke-linecap="round"/>
  <circle cx="224" cy="284" r="7" fill="{BURGUNDY}" stroke="{PLATINUM}" stroke-width="3"/>

  <!-- Chart title -->
  <text x="885" y="61" text-anchor="middle" fill="{SLATE}" font-family="Segoe UI, Arial, sans-serif" font-size="15">contributions in the last year</text>

  <!-- Chart axes -->
  <g stroke="{SLATE}" stroke-width="2" fill="none" opacity="0.9">
    <line x1="{chart_x}" y1="{chart_y + chart_h}" x2="{chart_x + chart_w}" y2="{chart_y + chart_h}"/>
    <line x1="{chart_x + chart_w}" y1="{chart_y}" x2="{chart_x + chart_w}" y2="{chart_y + chart_h}"/>
    {''.join(guide_lines)}
  </g>

  <!-- Contribution area -->
  <path d="{area_path}" fill="{BURGUNDY}" opacity="0.82"/>
  <path d="{line_path}" fill="none" stroke="{AMETHYST}" stroke-width="3"/>

  <!-- Chart labels -->
  <g fill="{SLATE}" font-family="Segoe UI, Arial, sans-serif" font-size="14">
    {''.join(x_labels)}
    {''.join(y_ticks)}
  </g>
</svg>
'''


def demo_data() -> ProfileData:
    # Initial fallback shown before the first workflow execution.
    start = date.today() - timedelta(weeks=52)
    weekly = [0, 1, 2, 4, 6, 8, 11, 14, 18, 21, 24, 26, 23, 17, 10, 5, 4, 4, 4, 3, 3, 2, 1, 0, 0, 2, 5, 8, 12, 17, 22, 28, 35, 42, 49, 57, 65, 71, 73, 62, 44, 27, 14, 18, 30, 42, 47, 44, 31, 17, 8, 12, 25]
    labels = [start + timedelta(weeks=index) for index in range(len(weekly))]
    return ProfileData(
        public_repos=6,
        joined_at=datetime(2024, 9, 26, 8, 23, 3, tzinfo=timezone.utc),
        current_year_contributions=250,
        week_labels=labels,
        weekly_contributions=weekly,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Generate the initial offline fallback card")
    args = parser.parse_args()

    if args.demo:
        data = demo_data()
    else:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise RuntimeError("GITHUB_TOKEN is required when --demo is not used")
        data = fetch_profile_data(USERNAME, token)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render_card(data), encoding="utf-8")
    print(f"Generated {OUTPUT}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # Keep workflow logs concise and actionable.
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
