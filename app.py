"""
Standalone Flask app: scores and dominance state using the same API as af get-rank.
Does not import from dominance_server or dashboard.
"""

import json
import os
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, redirect, render_template, request

from .api_client import (
    check_dominance,
    fetch_environments,
    fetch_scorer_config,
    fetch_scores,
    get_api_base_url,
    fetch_all_sampling_configs,
    fetch_chute_script_sync,
    fetch_sampling_list,
    get_chute_id_sync,
    get_model_sizes_batch,
    reconstruct_from_api_scores,
)
from .metagraph_client import get_hotkey_to_coldkey_from_metagraph, get_commits

app = Flask(__name__, template_folder="templates")

BLOCK_TIME_SECONDS = 12
SECONDS_PER_DAY = 60 * 60 * 24
MIN_COMPLETENESS = 0.9


def _csv_dir() -> str:
    """Directory containing this app (standalone_dashboard)."""
    return os.path.dirname(os.path.abspath(__file__))


def _address_csv_path() -> str:
    """Resolve address.csv: ADDRESS_CSV env, else standalone_dashboard/address.csv, else ../address.csv."""
    env_path = os.environ.get("ADDRESS_CSV")
    if env_path and os.path.isfile(env_path):
        return os.path.abspath(env_path)
    if env_path:
        return os.path.abspath(env_path)
    base = _csv_dir()
    for candidate in (os.path.join(base, "address.csv"), os.path.join(base, "..", "address.csv")):
        p = os.path.normpath(os.path.abspath(candidate))
        if os.path.isfile(p):
            return p
    return os.path.normpath(os.path.join(base, "address.csv"))


def _rank_data_json_path() -> str:
    """Path to cached rank data (get-rank API response). Override with RANK_DATA_JSON env."""
    env_path = os.environ.get("RANK_DATA_JSON")
    if env_path:
        return os.path.abspath(env_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "rank_data.json")


def _is_valid_rank_data(data: Any) -> bool:
    """Return True if data looks like a valid get-rank response (scores list + block_number)."""
    if not isinstance(data, dict):
        return False
    if "block_number" not in data:
        return False
    scores = data.get("scores")
    if not isinstance(scores, list):
        return False
    for s in scores:
        if not isinstance(s, dict) or not s.get("miner_hotkey"):
            return False
    return True


def _save_rank_data_to_file(data: Dict[str, Any]) -> None:
    """Write rank data to JSON file only when data is valid."""
    if not _is_valid_rank_data(data):
        return
    path = _rank_data_json_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _load_rank_data_from_file() -> Optional[Dict[str, Any]]:
    """Load cached rank data from JSON file. Returns None if missing/invalid."""
    path = _rank_data_json_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if _is_valid_rank_data(data):
            return data
    except Exception:
        pass
    return None


def _load_coldkey_manager_map() -> Dict[str, str]:
    """Load address.csv (tab: Wallet address, Manager(s)). Coldkey -> manager name."""
    csv_path = _address_csv_path()
    out: Dict[str, str] = {}
    if not os.path.isfile(csv_path):
        return out
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 2:
                    coldkey, manager = parts[0].strip(), parts[1].strip()
                    if coldkey and manager and not coldkey.lower().startswith("wallet"):
                        out[coldkey] = manager
    except Exception:
        pass
    return out


def _load_hotkey_to_coldkey() -> Dict[str, str]:
    """Optional: hotkey_coldkey.csv in standalone_dashboard (tab or comma: hotkey, coldkey) to resolve manager from address.csv."""
    for name in ("hotkey_coldkey.csv", "hotkey_to_coldkey.csv"):
        path = os.path.join(_csv_dir(), name)
        if not os.path.isfile(path):
            continue
        out: Dict[str, str] = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t") if "\t" in line else line.split(",")
                    if len(parts) >= 2:
                        hk, ck = parts[0].strip(), parts[1].strip()
                        if hk and ck:
                            out[hk] = ck
            return out
        except Exception:
            pass
    return {}


def get_all_dominance_data(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Compute dominance from rank data. Dashboard display uses only rank_data.json
    (no API call). API is used only when force_refresh=True (e.g. POST /api/dominance/refresh);
    then the response is saved to the JSON file only if valid.
    """
    scores_data: Optional[Dict[str, Any]] = None
    if force_refresh:
        try:
            scores_data = fetch_scores(get_api_base_url())
            if scores_data and _is_valid_rank_data(scores_data):
                _save_rank_data_to_file(scores_data)
        except Exception:
            pass
    if not scores_data or not _is_valid_rank_data(scores_data):
        scores_data = _load_rank_data_from_file()
    if not scores_data or not scores_data.get("block_number"):
        return {
            "block": 0,
            "uids": [],
            "total_uids": 0,
            "pareto_frontier_count": 0,
            "dominated_count": 0,
            "min_completeness": MIN_COMPLETENESS,
            "env_min_completeness": {},
            "displayed_count": 0,
        }

    base_url = get_api_base_url()
    environments: List[str] = []
    scorer_config: Dict[str, Any] = {}
    try:
        environments = fetch_environments(base_url) or []
        scorer_config = fetch_scorer_config(base_url) or {}
    except Exception:
        pass
    scores_list = scores_data.get("scores", [])
    block_number = scores_data.get("block_number", 0)
    default_min_completeness = scorer_config.get("min_completeness", MIN_COMPLETENESS)

    # Envs from API or from scores
    if environments:
        ENVS = tuple(environments)
    else:
        env_set = set()
        for score in scores_list:
            env_set.update(score.get("scores_by_env", {}).keys())
        ENVS = tuple(sorted(env_set)) if env_set else ()

    if not ENVS:
        return {
            "block": block_number,
            "uids": [],
            "total_uids": len(scores_list),
            "pareto_frontier_count": 0,
            "dominated_count": 0,
            "min_completeness": default_min_completeness,
            "env_min_completeness": {},
            "displayed_count": 0,
        }

    # Hotkeys = only those in API scores (order = get-rank order)
    hotkeys_ordered = [s.get("miner_hotkey") for s in scores_list if s.get("miner_hotkey")]
    hotkey_to_rank = {hk: r for r, hk in enumerate(hotkeys_ordered, 1)}
    hotkey_to_api_uid = {}
    hotkey_to_api_first_block = {}
    for score in scores_list:
        hk = score.get("miner_hotkey")
        if hk:
            uid = score.get("uid")
            if uid is not None:
                hotkey_to_api_uid[hk] = int(uid)
            hotkey_to_api_first_block[hk] = score.get("first_block")

    # Build stats etc. from API scores only (keyed by hotkeys in scores_list)
    scores_data_in = {"scores": scores_list, "block_number": block_number}
    (
        stats,
        confidence_intervals,
        scores_by_env_map,
        completeness_map,
        thresholds_map,
        sample_counts_map,
        total_problems_map,
    ) = reconstruct_from_api_scores(scores_data_in, ENVS)

    # Env config for min_completeness per env
    env_configs: Dict[str, Dict] = {}
    try:
        import requests as req
        r = req.get(f"{base_url}/config/environments", timeout=10)
        if r.ok:
            data = r.json()
            val = data.get("param_value") or {}
            if isinstance(val, dict):
                env_configs = val
    except Exception:
        pass

    def get_env_min_completeness(env_name: str) -> float:
        if env_name in env_configs and isinstance(env_configs[env_name], dict):
            return env_configs[env_name].get("min_completeness", default_min_completeness)
        return default_min_completeness

    # Scores (overall) and active set
    scores: Dict[str, float] = {}
    active_hks = set()
    for score in scores_list:
        hk = score.get("miner_hotkey")
        if hk:
            scores[hk] = score.get("overall_score", 0.0)
            miner_comp = completeness_map.get(hk, {})
            if any(
                miner_comp.get(e, 0) >= get_env_min_completeness(e)
                for e in ENVS
                if miner_comp.get(e, 0) > 0
            ):
                active_hks.add(hk)

    # Accuracies
    accuracies: Dict[str, Dict[str, float]] = {}
    for hk in hotkeys_ordered:
        accuracies[hk] = {}
        for e in ENVS:
            env_stats = stats.get(hk, {}).get(e, {"samples": 0, "total_score": 0.0})
            sam = env_stats.get("samples", 0)
            tot = env_stats.get("total_score", 0.0)
            accuracies[hk][e] = tot / sam if sam > 0 else 0.0

    coldkey_manager_map = _load_coldkey_manager_map()
    # Hotkey -> coldkey: from Bittensor metagraph first, then optional CSV fallback
    hotkey_to_coldkey = get_hotkey_to_coldkey_from_metagraph()
    hotkeys_in_metagraph = set(hotkey_to_coldkey.keys())
    csv_hotkey_to_coldkey = _load_hotkey_to_coldkey()
    for hk, ck in csv_hotkey_to_coldkey.items():
        if hk not in hotkey_to_coldkey:
            hotkey_to_coldkey[hk] = ck

    def manager_and_coldkey(hotkey: str, display_uid: int, score_entry: Optional[Dict] = None) -> Tuple[str, Optional[str]]:
        # UID 0 or hotkeys not in metagraph (e.g. AF) display as "Owner"
        if display_uid == 0 or hotkey not in hotkeys_in_metagraph:
            coldkey = hotkey_to_coldkey.get(hotkey)
            if not coldkey and score_entry:
                coldkey = score_entry.get("coldkey") or score_entry.get("wallet_address")
            return "Owner", coldkey
        coldkey = None
        if score_entry:
            coldkey = score_entry.get("coldkey") or score_entry.get("wallet_address")
        if not coldkey:
            coldkey = hotkey_to_coldkey.get(hotkey)
        manager = coldkey_manager_map.get(coldkey, "-") if coldkey else "-"
        return manager, coldkey

    uid_statuses: List[Dict[str, Any]] = []

    for idx, target_hotkey in enumerate(hotkeys_ordered):
        display_uid = hotkey_to_api_uid.get(target_hotkey, idx)
        api_first_block = hotkey_to_api_first_block.get(target_hotkey)
        if api_first_block is not None:
            display_first_block = api_first_block
            display_age_days = (block_number - api_first_block) * BLOCK_TIME_SECONDS / SECONDS_PER_DAY
        else:
            display_first_block = None
            display_age_days = 0.0

        target_has_data = any(
            stats.get(target_hotkey, {}).get(e, {}).get("samples", 0) > 0 for e in ENVS
        )
        env_scores_dict = {e: accuracies.get(target_hotkey, {}).get(e, 0.0) for e in ENVS}
        env_ci_dict = {
            e: confidence_intervals.get(target_hotkey, {}).get(e, (0.0, 0.0)) for e in ENVS
        }
        env_completeness_dict = {e: completeness_map.get(target_hotkey, {}).get(e, 0.0) for e in ENVS}
        env_thresholds_dict = {e: thresholds_map.get(target_hotkey, {}).get(e, 0.0) for e in ENVS}
        env_sample_counts_dict = {
            e: sample_counts_map.get(target_hotkey, {}).get(e, 0) for e in ENVS
        }
        env_total_problems_dict = {
            e: total_problems_map.get(target_hotkey, {}).get(e, 0) for e in ENVS
        }
        target_points = scores.get(target_hotkey, 0.0)
        target_model = None
        for s in scores_list:
            if s.get("miner_hotkey") == target_hotkey:
                m = s.get("model", "")
                rev = s.get("model_revision", "")
                if m:
                    target_model = f"{m}@{rev}" if rev else m
                break

        target_completeness = completeness_map.get(target_hotkey, {})
        target_is_active = any(
            target_completeness.get(e, 0) >= get_env_min_completeness(e)
            for e in ENVS
            if target_completeness.get(e, 0) > 0
        )
        envs_with_data = [e for e in ENVS if target_completeness.get(e, 0) > 0]
        target_is_eligible = (
            all(
                target_completeness.get(e, 0) >= get_env_min_completeness(e)
                for e in envs_with_data
            )
            if envs_with_data
            else False
        )

        if not target_has_data:
            uid_statuses.append({
                "uid": display_uid,
                "hotkey": target_hotkey,
                "is_dominated": False,
                "dominating_uids": [],
                "dominated_by_count": 0,
                "dominating_active_count": 0,
                "dominating_non_active_count": 0,
                "on_pareto_frontier": True,
                "has_data": False,
                "is_active": False,
                "is_eligible": False,
                "age_days": 0.0,
                "first_block": display_first_block,
                "points": 0.0,
                "env_scores": env_scores_dict,
                "env_confidence_intervals": env_ci_dict,
                "env_completeness": env_completeness_dict,
                "env_thresholds": env_thresholds_dict,
                "env_sample_counts": env_sample_counts_dict,
                "env_total_problems": env_total_problems_dict,
                "model_name": target_model,
                "model_size": None,
                "model_size_gb": None,
                "model_size_b": None,
                "manager": manager_and_coldkey(target_hotkey, display_uid, next((s for s in scores_list if s.get("miner_hotkey") == target_hotkey), None))[0],
                "coldkey": manager_and_coldkey(target_hotkey, display_uid, next((s for s in scores_list if s.get("miner_hotkey") == target_hotkey), None))[1],
                "rank": hotkey_to_rank.get(target_hotkey, 999),
                "af": False,
            })
            continue

        target_first_block = None
        for e in ENVS:
            fb = stats.get(target_hotkey, {}).get(e, {}).get("first_block")
            if fb is not None:
                target_first_block = fb
                break

        dominating_api_uids: List[int] = []
        dominating_active = 0
        dominating_non_active = 0

        for candidate_hotkey in hotkeys_ordered:
            if candidate_hotkey == target_hotkey:
                continue
            candidate_has_data = any(
                stats.get(candidate_hotkey, {}).get(e, {}).get("samples", 0) > 0 for e in ENVS
            )
            if not candidate_has_data:
                continue
            candidate_first_block = None
            for e in ENVS:
                fb = stats.get(candidate_hotkey, {}).get(e, {}).get("first_block")
                if fb is not None:
                    candidate_first_block = fb
                    break
            if candidate_first_block is None or target_first_block is None:
                continue
            if candidate_first_block > target_first_block:
                continue
            try:
                if check_dominance(
                    candidate_hotkey,
                    target_hotkey,
                    ENVS,
                    scores_by_env_map,
                    stats,
                ):
                    c_uid = hotkey_to_api_uid.get(candidate_hotkey, hotkeys_ordered.index(candidate_hotkey))
                    dominating_api_uids.append(c_uid)
                    c_comp = completeness_map.get(candidate_hotkey, {})
                    if any(
                        c_comp.get(e, 0) >= get_env_min_completeness(e)
                        for e in ENVS
                        if c_comp.get(e, 0) > 0
                    ):
                        dominating_active += 1
                    else:
                        dominating_non_active += 1
            except Exception:
                continue

        dominating_api_uids.sort()
        is_dominated = len(dominating_api_uids) > 0
        uid_statuses.append({
            "uid": display_uid,
            "hotkey": target_hotkey,
            "is_dominated": is_dominated,
            "dominating_uids": dominating_api_uids,
            "dominated_by_count": len(dominating_api_uids),
            "dominating_active_count": dominating_active,
            "dominating_non_active_count": dominating_non_active,
            "on_pareto_frontier": not is_dominated,
            "has_data": True,
            "is_active": target_is_active,
            "is_eligible": target_is_eligible,
            "age_days": display_age_days,
            "first_block": display_first_block,
            "points": target_points,
            "env_scores": env_scores_dict,
            "env_confidence_intervals": env_ci_dict,
            "env_completeness": env_completeness_dict,
            "env_thresholds": env_thresholds_dict,
            "env_sample_counts": env_sample_counts_dict,
            "env_total_problems": env_total_problems_dict,
            "model_name": target_model,
            "model_size": None,
            "model_size_gb": None,
            "model_size_b": None,
            "manager": manager_and_coldkey(target_hotkey, display_uid, next((s for s in scores_list if s.get("miner_hotkey") == target_hotkey), None))[0],
            "coldkey": manager_and_coldkey(target_hotkey, display_uid, next((s for s in scores_list if s.get("miner_hotkey") == target_hotkey), None))[1],
            "rank": hotkey_to_rank.get(target_hotkey, 999),
            "af": False,
        })

    # AF miners: in API scores but not in our hotkeys_ordered (here hotkeys_ordered IS from API, so no AF unless we had metagraph)
    # So we skip AF for this standalone app unless we add a separate "all metagraph" source.

    uid_statuses.sort(key=lambda u: (u["rank"], 0))
    pareto_count = sum(1 for u in uid_statuses if u["on_pareto_frontier"] and u["has_data"])
    dominated_count = sum(1 for u in uid_statuses if u["is_dominated"])
    env_min_completeness_dict = {e: get_env_min_completeness(e) for e in ENVS}

    return {
        "block": block_number,
        "uids": uid_statuses,
        "total_uids": len(hotkeys_ordered),
        "pareto_frontier_count": pareto_count,
        "dominated_count": dominated_count,
        "min_completeness": default_min_completeness,
        "env_min_completeness": env_min_completeness_dict,
        "displayed_count": len(uid_statuses),
    }


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/dominance")
def api_dominance():
    try:
        data = get_all_dominance_data()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "uids": [], "block": 0})


@app.route("/api/scores")
def api_scores():
    """Raw scores from API (same as get-rank source)."""
    try:
        base_url = get_api_base_url()
        data = fetch_scores(base_url)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/chute-id")
def api_chute_id():
    """Resolve model name to Chutes chute_id. Query param: model=owner/repo or owner/repo@revision."""
    model = request.args.get("model", "").strip()
    if not model:
        return jsonify({"chute_id": None})
    try:
        chute_id = get_chute_id_sync(model)
        return jsonify({"chute_id": chute_id})
    except Exception:
        return jsonify({"chute_id": None})


MODEL_SIZES_BATCH_SIZE = 20


@app.route("/api/model-sizes", methods=["POST"])
def api_model_sizes():
    """Batch fetch model size/params. JSON body: { "models": ["model1", ...], "offset": 0 }.
    Returns up to MODEL_SIZES_BATCH_SIZE results from the given offset. Client should call with offset=0, then offset=20, ... until no more."""
    try:
        body = request.get_json() or {}
        models = body.get("models") or []
        if not isinstance(models, list):
            models = []
        offset = max(0, int(body.get("offset", 0)))
        chunk = models[offset : offset + MODEL_SIZES_BATCH_SIZE]
        results = get_model_sizes_batch(chunk, max_per_batch=MODEL_SIZES_BATCH_SIZE)
        return jsonify({
            "results": results,
            "offset": offset,
            "next_offset": offset + len(chunk) if offset + len(chunk) < len(models) else None,
        })
    except Exception as e:
        return jsonify({"results": {}, "error": str(e)})


@app.route("/api/dominance/<int:uid>")
def api_dominance_uid(uid: int):
    """Get dominance status for a single UID."""
    try:
        data = get_all_dominance_data()
        uid_status = next((u for u in data["uids"] if u["uid"] == uid), None)
        if uid_status is None:
            return jsonify({"error": f"UID {uid} not found"}), 404
        return jsonify(uid_status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/dominance/<int:uid>/dominating")
def api_dominance_uid_dominating(uid: int):
    """Get full details of UIDs that dominate this UID."""
    try:
        data = get_all_dominance_data()
        uid_status = next((u for u in data["uids"] if u["uid"] == uid), None)
        if uid_status is None:
            return jsonify({"error": f"UID {uid} not found"}), 404
        dominating_uids = uid_status.get("dominating_uids") or []
        dominating_details = []
        for dom_uid in dominating_uids:
            dom_status = next((u for u in data["uids"] if u["uid"] == dom_uid), None)
            if dom_status:
                dominating_details.append(dom_status)
        return jsonify({
            "uid": uid,
            "dominating_uids": dominating_details,
            "total_count": len(dominating_details),
            "expected_count": len(dominating_uids),
            "active_count": uid_status.get("dominating_active_count", 0),
            "non_active_count": uid_status.get("dominating_non_active_count", 0),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/dominance/refresh", methods=["POST"])
def api_dominance_refresh():
    """Force refresh: fetch rank API, save to rank_data.json if valid, then return dominance data."""
    try:
        data = get_all_dominance_data(force_refresh=True)
        return jsonify({
            "success": True,
            "message": f"Dominance data refreshed for block {data['block']}",
            "data": data,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/chute-script")
def api_chute_script():
    """Fetch Chutes source script for a model. Query param: model=."""
    model = request.args.get("model", "").strip()
    if not model:
        return jsonify({"success": False, "error": "model required"})
    try:
        chute_id = get_chute_id_sync(model)
        if not chute_id:
            return jsonify({"success": False, "error": "No chute found for this model", "model": model})
        payload = fetch_chute_script_sync(chute_id)
        if not payload:
            return jsonify({"success": False, "error": "Could not fetch chute script", "model": model, "chute_id": chute_id})
        payload["model"] = model
        payload["success"] = True
        return jsonify(payload)
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "model": model})


@app.route("/api/chute-scripts", methods=["POST"])
def api_chute_scripts():
    """Batch fetch chute scripts. JSON body: { "models": ["model1", ...] }."""
    body = request.get_json() or {}
    models = [m.strip() for m in (body.get("models") or []) if m and str(m).strip()]
    scripts = {}
    errors = {}
    for model in models:
        try:
            chute_id = get_chute_id_sync(model)
            if not chute_id:
                errors[model] = "No chute found"
                continue
            payload = fetch_chute_script_sync(chute_id)
            if not payload:
                errors[model] = "Could not fetch script"
                continue
            payload["model"] = model
            scripts[model] = payload
        except Exception as e:
            errors[model] = str(e)
    return jsonify({"scripts": scripts, "errors": errors})


@app.route("/api/commits")
def api_commits():
    """Get commit info (model, revision, block) for all UIDs from chain."""
    try:
        data = get_commits()
        commits = data.get("commits") or []
        # Team = UIDs whose coldkey is in address.csv; enemy = rest
        team_uid_count = sum(1 for c in commits if c.get("manager"))
        enemy_uid_count = len(commits) - team_uid_count
        data["team_uid_count"] = team_uid_count
        data["enemy_uid_count"] = enemy_uid_count
        return jsonify(data)
    except Exception as e:
        return jsonify({"block": 0, "commits": [], "error": str(e)})


@app.route("/uid/<int:uid>")
def uid_page(uid: int):
    """Serve dashboard with optional detail UID (same HTML, client can open detail modal)."""
    return render_template("dashboard.html")


@app.route("/commits")
def commits_redirect():
    """Redirect to main dashboard with #commits so client shows commits view."""
    return redirect("/#commits", code=302)


@app.route("/api/sampling-configs/all")
def api_sampling_configs_all():
    """Get all sampling configs from Affine API config."""
    try:
        configs = fetch_all_sampling_configs()
        return jsonify({
            "success": True,
            "environments": configs,
            "total_environments": len(configs),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "environments": {}})


@app.route("/api/sampling-list/<int:uid>/<path:env>")
def api_sampling_list(uid: int, env: str):
    """Get sampling list for UID and environment (proxies to Affine API)."""
    try:
        data = fetch_sampling_list(uid, env)
        return jsonify(data)
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "uid": uid, "env": env})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG", "0") == "1")
