import os
from dotenv import load_dotenv

load_dotenv()  # liest die .env-Datei ein

key = os.getenv("SIG_API_KEY")

if key is None:
    print("Fehler: Kein Key gefunden. Prüf die .env-Datei.")
else:
    print(f"Key gefunden, Länge: {len(key)} Zeichen")