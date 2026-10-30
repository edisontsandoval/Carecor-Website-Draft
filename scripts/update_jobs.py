#!/usr/bin/env python3
"""Refresh the job listings on the Carecor draft site from the UKG job board.

Runs every morning (several times, for reliability) from GitHub Actions (.github/workflows/update-jobs.yml).
It reads every posting on the public Carecor job board, rewrites the job data
inside index.html and the "as of" date, and saves a copy to jobs.json.

Safety rules:
- If the board can't be reached, or it returns no postings at all, the site is
  left exactly as it was and the run is marked as failed so GitHub emails the
  repository owner.
- Only standard-library Python is used, so nothing needs installing.
"""
import json, os, re, sys, time, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

BOARD = "https://recruiting.ultipro.ca/BAY5100BYHC/JobBoard/c921bbcc-c132-45c9-86e9-3d15aa0bca3a"
ENDPOINT = BOARD + "/JobBoardView/LoadSearchResults"
SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
INDEX = os.path.join(SITE, "index.html")
JOBS_JSON = os.path.join(SITE, "jobs.json")
TZ = ZoneInfo("America/Toronto")
BLURB_MAX = 420

QUERY = {
    "opportunitySearch": {
        "Top": 200, "Skip": 0, "QueryString": "",
        "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}],
        "Filters": [
            {"t": "TermsSearchFilterDto", "fieldName": 4, "extra": None, "values": []},
            {"t": "TermsSearchFilterDto", "fieldName": 5, "extra": None, "values": []},
            {"t": "TermsSearchFilterDto", "fieldName": 6, "extra": None, "values": []},
        ],
    },
    "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [],
                      "Skills": [], "hasNoLicenses": False, "SkippedSkills": []},
}


def fetch():
    data = json.dumps(QUERY).encode()
    last = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(ENDPOINT, data=data, method="POST", headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (Carecor draft site job refresh)",
            })
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # network hiccup: wait and retry
            last = e
            print(f"Attempt {attempt} failed: {e}")
            time.sleep(20 * attempt)
    raise SystemExit(f"Could not reach the UKG job board: {last}")


def city_of(loc):
    addr = loc.get("Address") or {}
    city = addr.get("City")
    if not city:  # e.g. "Oxford County, ON"
        city = (loc.get("LocalizedName") or "").split(",")[0].strip() or None
    return city


def compact(o):
    locs = []
    for l in o.get("Locations") or []:
        addr = l.get("Address") or {}
        c = l.get("Coordinates") or {}
        lat, lon = c.get("Latitude"), c.get("Longitude")
        locs.append([city_of(l), (addr.get("State") or {}).get("Code"),
                     round(lat, 3) if lat is not None else None,
                     round(lon, 3) if lon is not None else None])
    blurb = (o.get("BriefDescription") or "").strip()
    return {
        "id": o["Id"], "t": o["Title"].strip(), "req": o.get("RequisitionNumber"),
        "ft": bool(o.get("FullTime")), "cat": o.get("JobCategoryName"),
        "d": (o.get("PostedDate") or "")[:10], "loc": locs, "b": blurb[:BLURB_MAX],
    }


def main():
    now = datetime.now(TZ)
    # Several runs are scheduled each morning because GitHub can start scheduled
    # jobs late or skip one when it is busy. Every run refreshes; if nothing on the
    # board changed since the last run that day, there is nothing to commit.
    raw = fetch()
    opps = raw.get("opportunities") or []
    if not opps:
        raise SystemExit("The job board returned no postings. Leaving the site unchanged.")
    jobs = [compact(o) for o in opps if o.get("Id") and o.get("Title")]

    html = open(INDEX, encoding="utf-8").read()
    m = re.search(r"var RAW=(.*?);/\*JOBS_END\*/", html, re.S)
    if not m:
        raise SystemExit("Could not find the job data in index.html.")
    old = json.loads(m.group(1))
    snap = f"{now:%B} {now.day}, {now.year}"

    new_json = json.dumps(jobs, ensure_ascii=False).replace("</", "<\\/")
    html = html[:m.start(1)] + new_json + html[m.end(1):]
    html, n = re.subn(r"var SNAP='[^']*';", f"var SNAP='{snap}';", html, count=1)
    if not n:
        raise SystemExit("Could not find the 'as of' date in index.html.")
    open(INDEX, "w", encoding="utf-8").write(html)
    with open(JOBS_JSON, "w", encoding="utf-8") as f:
        json.dump({"checked": now.isoformat(timespec="minutes"), "count": len(jobs), "jobs": jobs},
                  f, ensure_ascii=False, indent=1)

    old_ids = {j["id"]: j for j in old}
    new_ids = {j["id"]: j for j in jobs}
    added = [new_ids[i] for i in new_ids if i not in old_ids]
    removed = [old_ids[i] for i in old_ids if i not in new_ids]
    print(f"Checked the board on {snap}: {len(jobs)} openings ({len(added)} new, {len(removed)} removed).")
    for j in added:
        print(f"  + {j['req']}  {j['t']}")
    for j in removed:
        print(f"  - {j['req']}  {j['t']}")


if __name__ == "__main__":
    main()
