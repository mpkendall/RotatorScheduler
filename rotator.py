import requests
import json
import re

class Rotator:
    def __init__(self, base_url, bearer_token="changeme"):
        self.base_url = base_url
        self.bearer_token = bearer_token
        self.timeout = 5

    def _get_headers(self):
        """Get headers with bearer token authentication"""
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "Content-Type": "application/json"
        }

    def get_status(self):
        try:
            response = requests.get(
                f"{self.base_url}/status",
                headers=self._get_headers(),
                timeout=self.timeout
            )
            response.raise_for_status()
            
            text = response.text.strip()
            if not text:
                return None

            # Try parsing text format: "Azimuth: 123.45 (1.23 deg/s), Elevation: 45.67 (0.50 deg/s)"
            # Using regex to extract values
            match = re.search(r"Azimuth:\s*([\d.-]+)\s*\(([\d.-]+)\s*deg/s\),\s*Elevation:\s*([\d.-]+)\s*\(([\d.-]+)\s*deg/s\)", text)
            
            if match:
                return {
                    "azimuth": float(match.group(1)),
                    "az_speed": float(match.group(2)),
                    "elevation": float(match.group(3)),
                    "el_speed": float(match.group(4))
                }

            # Fallback to JSON if regex fails
            try:
                return response.json()
            except json.JSONDecodeError:
                print(f"Invalid response format: {text}")
                return None

        except requests.RequestException as e:
            print(f"Error getting rotator status: {e}")
            return None

    def ping(self):
        try:
            response = requests.get(
                f"{self.base_url}/ping",
                headers=self._get_headers(),
                timeout=2
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def move_to(self, azimuth, elevation):
        try:
            params = {"az": azimuth, "el": elevation}
            response = requests.get(
                f"{self.base_url}/command",
                params=params,
                headers=self._get_headers(),
                timeout=self.timeout
            )
            response.raise_for_status()
            
            if response.text.strip():
                try:
                    return response.json()
                except json.JSONDecodeError:
                    # If we get an empty or invalid response, treat as success
                    return {"status": "ok", "az": azimuth, "el": elevation}
            else:
                # Empty response is treated as success
                return {"status": "ok", "az": azimuth, "el": elevation}
        except requests.RequestException as e:
            print(f"Error sending rotator command: {e}")
            return {"status": "error", "message": str(e)}



