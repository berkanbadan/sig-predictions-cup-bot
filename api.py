import os
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("SIG_API_KEY")
BASE_URL = os.getenv("SIG_BASE_URL")

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json",
}


def api_get(path, params=None):
    url = BASE_URL + path
    response = requests.get(url, headers=HEADERS, params=params, timeout=15)

    if response.status_code >= 400:
        print(f"Fehler {response.status_code}: {response.text[:300]}")
        response.raise_for_status()

    return response.json()

def api_post(path, body):
    url = BASE_URL + path
    response = requests.post(url, headers=HEADERS, json=body, timeout=15)

    if response.status_code >= 400:
        print(f"Fehler {response.status_code}: {response.text[:300]}")
        response.raise_for_status()

    return response.json()