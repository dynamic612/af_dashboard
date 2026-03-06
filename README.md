# Standalone Scores & Dominance Dashboard

Flask app that shows **scores** and **dominance state** using the **same API as `af get-rank`**. It does not import from `dominance_server.py` or `dashboard.html`.

## Features

- **Dashboard**: Same API as `af get-rank`; dominance computed from API data; metagraph for coldkey → Owner from `address.csv`.
- **Stats**: Rewarded UIDs, Displayed, Eligible, Team vs Enemy (in address.csv : not), Not received (per env), Dominated, Block.
- **Table**: UID (click to copy), Model (narrow + Rollout/Chute buttons), Owner (Taostats link; “Owner” for UID 0 / non-metagraph), Params, Size, Dom (click for detail modal), env scores (score [threshold] + received/total; click for sampling list), AVE, Age, Weight (%).
- **Actions**: Refresh & Calculate, Auto-Refresh (60s), Download script (batch chute scripts).
- **Dominance detail modal**: Click Dom count → list of UIDs that dominate that miner.
- **Sampling list modal**: Click env score cell → sampling list for that UID/env (from Affine API).
- **Commits page**: Nav “Commits” or `/#commits` → table of UID, Owner, Hotkey, Model, Revision, Block from chain.

## Setup

Use the **same Python** you will use to run the app (avoids "No module named 'flask'" when a different env is active):

```bash
cd /root/affine-cortex
python -m pip install -r standalone_dashboard/requirements.txt
```

## Run

```bash
cd /root/affine-cortex
python -m standalone_dashboard.run
```

Then open http://localhost:5000

### Run with PM2

From the `affine-cortex` directory:

```bash
cd /root/affine-cortex
pm2 start standalone_dashboard/ecosystem.config.cjs
```

Or with a custom cwd:

```bash
pm2 start /root/affine-cortex/standalone_dashboard/ecosystem.config.cjs --cwd /root/affine-cortex
```

Use env vars in the config or before start, e.g.:

```bash
API_URL=http://localhost:1999/api/v1 pm2 start standalone_dashboard/ecosystem.config.cjs
```

Useful commands: `pm2 status`, `pm2 logs standalone-dashboard`, `pm2 restart standalone-dashboard`, `pm2 stop standalone-dashboard`.

## Environment

- **API_URL** – Affine API base URL (default: `https://api.affine.io/api/v1`)
- **PORT** – Server port (default: `5000`)
- **ADDRESS_CSV** – Path to tab-separated `Wallet address, Manager(s)` file for Owner column (default: `../address.csv`). Owner (manager name) is looked up by coldkey. Coldkeys are resolved from the **Bittensor metagraph** (hotkey → coldkey per UID). Optional fallback: `hotkey_coldkey.csv` or `hotkey_to_coldkey.csv` in the same directory for hotkeys not on chain (e.g. AF).
- **NETUID** – Subnet UID for metagraph (default: `120`).
- **SUBTENSOR_NETWORK** – Bittensor network for metagraph (default: `finney`).

Example with local API:

```bash
API_URL=http://localhost:1999/api/v1 python -m standalone_dashboard.run
```

## Endpoints

- `GET /` – Dashboard HTML
- `GET /uid/<uid>` – Dashboard HTML (for deep link)
- `GET /commits` – Redirect to `/#commits`
- `GET /api/dominance` – Full dominance data (block, uids with scores and dominance state)
- `GET /api/dominance/<uid>` – Single UID dominance status
- `GET /api/dominance/<uid>/dominating` – UIDs that dominate this UID
- `POST /api/dominance/refresh` – Recompute and return dominance data
- `GET /api/scores` – Raw scores from Affine API (same as get-rank source)
- `GET /api/commits` – Commit info for all UIDs (metagraph + chain commitments)
- `GET /api/chute-id?model=...` – Resolve model to Chutes chute_id
- `GET /api/chute-script?model=...` – Chute source script for one model
- `POST /api/chute-scripts` – Batch chute scripts (body: `{"models": [...]}`)
- `POST /api/model-sizes` – Batch model size/params (body: `{"models": [...]}`)
- `GET /api/sampling-configs/all` – Sampling configs (Affine API config)
- `GET /api/sampling-list/<uid>/<env>` – Sampling list for UID/env (proxies to Affine API)
