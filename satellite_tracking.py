from datetime import datetime, timedelta, timezone
import threading

import requests
from skyfield.api import EarthSatellite, load, wgs84


class SatelliteTrackingService:
    """Fetch satellite data and generate interpolated az/el track points."""

    SATELLITE_LIST_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=json"
    SATELLITE_LIST_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"
    SATELLITE_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=TLE"

    def __init__(self, cache_ttl_seconds=1800):
        self.cache_ttl_seconds = cache_ttl_seconds
        self.ts = load.timescale()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "RotatorScheduler/1.0 (+https://localhost)",
                "Accept": "application/json,text/plain;q=0.9,*/*;q=0.8",
            }
        )
        self._lock = threading.Lock()
        self._satellite_cache = []
        self._satellite_cache_expires = datetime.min.replace(tzinfo=timezone.utc)

    @staticmethod
    def _extract_norad_from_tle_line1(line1):
        # TLE line 1 format: "1 NNNNNU ..."; NORAD is columns 3-7.
        if not line1 or not line1.startswith("1 "):
            return None
        field = line1[2:8].strip()
        digits = "".join(ch for ch in field if ch.isdigit())
        if not digits:
            return None
        try:
            return int(digits)
        except ValueError:
            return None

    def _parse_satellite_list_from_tle(self, tle_text):
        lines = [line.strip() for line in tle_text.splitlines() if line.strip()]
        satellites = []

        i = 0
        while i < len(lines):
            name = None
            line1 = None
            line2 = None

            if lines[i].startswith("1 "):
                # Some streams omit the name line.
                line1 = lines[i]
                if i + 1 < len(lines) and lines[i + 1].startswith("2 "):
                    line2 = lines[i + 1]
                    i += 2
                else:
                    i += 1
            else:
                name = lines[i]
                if i + 2 < len(lines) and lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
                    line1 = lines[i + 1]
                    line2 = lines[i + 2]
                    i += 3
                else:
                    i += 1

            if not line1 or not line2:
                continue

            norad_id = self._extract_norad_from_tle_line1(line1)
            if norad_id is None:
                continue

            satellites.append(
                {
                    "norad_id": norad_id,
                    "name": (name or f"NORAD {norad_id}").strip(),
                }
            )

        return satellites

    @staticmethod
    def _parse_iso_datetime(value):
        if not value:
            raise ValueError("Missing required datetime value")

        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            normalized = value.strip()
            if normalized.endswith("Z"):
                normalized = normalized[:-1] + "+00:00"
            dt = datetime.fromisoformat(normalized)
        else:
            raise ValueError("Unsupported datetime format")

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _refresh_satellite_cache(self):
        now = datetime.now(timezone.utc)
        with self._lock:
            if now < self._satellite_cache_expires and self._satellite_cache:
                return

            satellites = []
            try:
                response = self.session.get(self.SATELLITE_LIST_URL, timeout=20)
                response.raise_for_status()
                raw_list = response.json()

                for entry in raw_list:
                    name = (entry.get("OBJECT_NAME") or "").strip()
                    norad_id = entry.get("NORAD_CAT_ID")
                    if not name or norad_id is None:
                        continue
                    satellites.append(
                        {
                            "norad_id": int(norad_id),
                            "name": name,
                        }
                    )
            except requests.RequestException:
                # Some networks/CDN policies block JSON endpoint; fallback to TLE list.
                tle_response = self.session.get(self.SATELLITE_LIST_TLE_URL, timeout=20)
                tle_response.raise_for_status()
                satellites = self._parse_satellite_list_from_tle(tle_response.text)

            satellites.sort(key=lambda item: item["name"])
            self._satellite_cache = satellites
            self._satellite_cache_expires = now + timedelta(seconds=self.cache_ttl_seconds)

    def list_satellites(self, query=None, limit=200):
        self._refresh_satellite_cache()

        with self._lock:
            satellites = list(self._satellite_cache)

        if query:
            q = query.strip().lower()
            if q:
                satellites = [
                    sat
                    for sat in satellites
                    if q in sat["name"].lower() or q in str(sat["norad_id"])
                ]

        if limit is not None:
            limit = max(1, int(limit))
            satellites = satellites[:limit]

        return satellites

    def _get_satellite_from_tle(self, norad_id):
        response = self.session.get(self.SATELLITE_TLE_URL.format(norad_id=norad_id), timeout=15)
        response.raise_for_status()

        lines = [line.strip() for line in response.text.splitlines() if line.strip()]
        if len(lines) < 2:
            raise ValueError(f"Could not load TLE for NORAD {norad_id}")

        if len(lines) >= 3 and lines[1].startswith("1 ") and lines[2].startswith("2 "):
            name = lines[0]
            line1 = lines[1]
            line2 = lines[2]
        elif len(lines) >= 2 and lines[0].startswith("1 ") and lines[1].startswith("2 "):
            name = f"NORAD {norad_id}"
            line1 = lines[0]
            line2 = lines[1]
        else:
            raise ValueError(f"Invalid TLE format for NORAD {norad_id}")

        return EarthSatellite(line1, line2, name, self.ts)

    def get_next_passes(
        self,
        norad_id,
        observer_lat,
        observer_lon,
        observer_elevation_m,
        window_start,
        window_hours=24,
        min_elevation_degrees=5,
        max_passes=12,
    ):
        satellite = self._get_satellite_from_tle(int(norad_id))
        observer = wgs84.latlon(float(observer_lat), float(observer_lon), elevation_m=float(observer_elevation_m))

        start_dt = self._parse_iso_datetime(window_start)
        end_dt = start_dt + timedelta(hours=float(window_hours))

        t0 = self.ts.from_datetime(start_dt)
        t1 = self.ts.from_datetime(end_dt)

        times, events = satellite.find_events(observer, t0, t1, altitude_degrees=float(min_elevation_degrees))

        passes = []
        current_pass = None

        for t, event_code in zip(times, events):
            event_dt = t.utc_datetime().replace(tzinfo=timezone.utc)

            if event_code == 0:
                current_pass = {
                    "rise_time": event_dt.isoformat(),
                    "rise_time_unix": int(event_dt.timestamp()),
                }
            elif event_code == 1 and current_pass is not None:
                topocentric = (satellite - observer).at(t)
                alt, _, _ = topocentric.altaz()
                current_pass["max_elevation"] = round(float(alt.degrees), 3)
                current_pass["max_time"] = event_dt.isoformat()
            elif event_code == 2 and current_pass is not None:
                current_pass["set_time"] = event_dt.isoformat()
                current_pass["set_time_unix"] = int(event_dt.timestamp())

                rise_dt = self._parse_iso_datetime(current_pass["rise_time"])
                set_dt = self._parse_iso_datetime(current_pass["set_time"])
                current_pass["duration_seconds"] = int((set_dt - rise_dt).total_seconds())

                passes.append(current_pass)
                current_pass = None

                if len(passes) >= int(max_passes):
                    break

        return passes

    def generate_track_points(
        self,
        norad_id,
        observer_lat,
        observer_lon,
        observer_elevation_m,
        pass_start,
        pass_end,
        point_interval_seconds=30,
    ):
        interval_seconds = int(point_interval_seconds)
        if interval_seconds <= 0:
            raise ValueError("point_interval_seconds must be > 0")

        satellite = self._get_satellite_from_tle(int(norad_id))
        observer = wgs84.latlon(float(observer_lat), float(observer_lon), elevation_m=float(observer_elevation_m))

        start_dt = self._parse_iso_datetime(pass_start)
        end_dt = self._parse_iso_datetime(pass_end)
        if end_dt <= start_dt:
            raise ValueError("pass_end must be after pass_start")

        sample_times = []
        cursor = start_dt
        while cursor < end_dt:
            sample_times.append(cursor)
            cursor += timedelta(seconds=interval_seconds)

        if not sample_times or sample_times[-1] != end_dt:
            sample_times.append(end_dt)

        points = []
        for sample_dt in sample_times:
            t = self.ts.from_datetime(sample_dt)
            topocentric = (satellite - observer).at(t)
            alt, az, _ = topocentric.altaz()

            offset_seconds = int(round((sample_dt - start_dt).total_seconds()))
            points.append(
                {
                    "azimuth": round(float(az.degrees) % 360.0, 3),
                    "elevation": round(max(float(alt.degrees), 0.0), 3),
                    "time_offset": offset_seconds,
                }
            )

        if len(points) < 2:
            raise ValueError("Generated pass has too few points")

        points[0]["time_offset"] = 0
        points[-1]["time_offset"] = int(round((end_dt - start_dt).total_seconds()))
        return points