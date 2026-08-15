# InsuranceAgent

Automates getting insurance quotes from [forsikringsguiden.dk](https://forsikringsguiden.dk/) so you
state your requirements once, and quotes for all your selected products come back together.

## How it works

1. **Profile** (`profile.json`): all your requirements/personal data in one place, stated once.
2. **Recorder** (`insurance_agent/recorder.py`): the *first* time you run a product flow, you walk
   through it manually (choose which button/input to use at each step). Every action is saved as a
   `Step` to `flows/<product>.json` -- this is the "learning" pass.
3. **Player** (`insurance_agent/player.py`): every time after that, the cached steps are replayed
   headlessly, pulling fill-in values from `profile.json` instead of asking you again.
4. **Extractor** (`insurance_agent/extractor.py`): scrapes the resulting quotes page for company +
   price pairs.
5. **Orchestrator** (`main.py`): loops over every product in your profile, records-if-missing or
   replays-if-cached, and prints all quotes together.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install playwright
playwright install chromium
Copy-Item profile.example.json profile.json
# edit profile.json with your real details
```

## Usage

Learn a flow once (interactive, headed browser):

```powershell
python main.py --record bilforsikring
```

Then get quotes for every product listed in `profile.json` (uses cached flows, records any that
are still missing):

```powershell
python main.py
```

Run fully headless once all flows are cached:

```powershell
python main.py --headless
```

## Notes / limitations

- `profile.json` contains personal data (postcode, birthdate, registration numbers, etc.) -- keep it
  out of version control (see `.gitignore`).
- The site may change its markup over time. If a cached flow stops working, `player.py` reports which
  step failed; re-record that product with `--record <product>`.
- The quote extractor uses heuristics (price-pattern text scanning) since the results page structure
  is only known once a flow has real data run through it. Re-check `extract_quotes` output the first
  time you run a product and tighten the heuristics if it picks up noise.
