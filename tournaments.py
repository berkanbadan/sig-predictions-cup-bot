import json
from api import api_get

data = api_get("/tournaments")

print(json.dumps(data, indent=2))