import json
from Old.insurance_agent.models import Flow

profile = json.load(open("profile.json", encoding="utf-8"))

import Old.main as main
quotes = main.run_product("bilforsikring", profile, headless=True)
print(quotes)