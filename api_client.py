"""
Standalone API client for Affine API (same endpoints as af get-rank).
No imports from dominance_server or affine packages.
"""

import os
import math
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import requests

DEFAULT_API_URL = "https://api.affine.io/api/v1"
CHUTES_API_BASE = "https://api.chutes.ai/chutes/"
HF_SCRAPE_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
_RE_PARAMS = re.compile(r'["\']?(\d+(?:\.\d+)?[KMB])\s*params["\']?', re.I)
_RE_SIZE_GB = re.compile(r'["\']?(\d+(?:\.\d+)?)\s*GB["\']?', re.I)
_BYTES_PER_PARAM = 2

_chute_id_cache: Dict[str, Optional[str]] = {}
_model_size_cache: Dict[str, Tuple[Optional[str], Optional[float], Optional[float]]] = {}
Z_SCORE = 1.5
MIN_IMPROVEMENT = 0.02
MAX_IMPROVEMENT = 0.10


def get_api_base_url() -> str:
    return os.environ.get("API_URL", DEFAULT_API_URL).rstrip("/")


def fetch_all_sampling_configs() -> Dict[str, Any]:
    """GET /config from Affine API and return configs.environments."""
    base = get_api_base_url()
    try:
        r = requests.get(f"{base}/config", timeout=15)
        if r.ok:
            data = r.json() or {}
            return data.get("configs", {}).get("environments", {}) or data.get("param_value", {})
    except Exception:
        pass
    return {}


def _calculate_required_score(
    prior_score: float,
    prior_sample_count: int,
    z_score: float = Z_SCORE,
    min_improvement: float = MIN_IMPROVEMENT,
    max_improvement: float = MAX_IMPROVEMENT,
) -> float:
    if prior_sample_count <= 0:
        gap = max_improvement
    else:
        p = prior_score
        se = math.sqrt(p * (1.0 - p) / prior_sample_count)
        gap = z_score * se
        gap = max(gap, min_improvement)
        gap = min(gap, max_improvement)
    return min(prior_score + gap, 1.0)


def fetch_scores(base_url: Optional[str] = None) -> Dict[str, Any]:
    """GET /scores/latest?top=256 - same as af get-rank."""
    base_url = base_url or get_api_base_url()
    url = f"{base_url}/scores/latest"
    r = requests.get(url, params={"top": 256}, timeout=60)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and data.get("success") is False:
        raise RuntimeError(data.get("error", "Unknown API error"))
    return data


def fetch_environments(base_url: Optional[str] = None) -> List[str]:
    """GET /config/environments - enabled for scoring."""
    base_url = base_url or get_api_base_url()
    url = f"{base_url}/config/environments"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    config = r.json()
    if isinstance(config, dict):
        value = config.get("param_value")
        if isinstance(value, dict):
            enabled = [
                name
                for name, env_config in value.items()
                if isinstance(env_config, dict) and env_config.get("enabled_for_scoring", False)
            ]
            if enabled:
                return sorted(enabled)
    return []


def fetch_scorer_config(base_url: Optional[str] = None) -> Dict[str, Any]:
    """GET /scores/weights/latest."""
    base_url = base_url or get_api_base_url()
    url = f"{base_url}/scores/weights/latest"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("config", {}) if isinstance(data, dict) else {}


def reconstruct_from_api_scores(
    scores_data: Dict[str, Any],
    envs: Tuple[str, ...],
) -> Tuple[
    Dict[str, Dict[str, Dict[str, Any]]],
    Dict[str, Dict[str, Tuple[float, float]]],
    Dict[str, Dict[str, Dict[str, Any]]],
    Dict[str, Dict[str, float]],
    Dict[str, Dict[str, float]],
    Dict[str, Dict[str, int]],
    Dict[str, Dict[str, int]],
]:
    """
    Build stats, confidence_intervals, scores_by_env_map, etc. from API scores only.
    Hotkeys = only those in scores list (no metagraph).
    """
    stats: Dict[str, Dict[str, Dict[str, Any]]] = {}
    confidence_intervals: Dict[str, Dict[str, Tuple[float, float]]] = {}
    scores_by_env_map: Dict[str, Dict[str, Dict[str, Any]]] = {}
    completeness_map: Dict[str, Dict[str, float]] = {}
    thresholds_map: Dict[str, Dict[str, float]] = {}
    sample_counts_map: Dict[str, Dict[str, int]] = {}
    total_problems_map: Dict[str, Dict[str, int]] = {}

    scores_list = scores_data.get("scores", [])
    for score in scores_list:
        hotkey = score.get("miner_hotkey")
        if not hotkey:
            continue
        stats[hotkey] = {}
        confidence_intervals[hotkey] = {}
        scores_by_env_map[hotkey] = {}
        completeness_map[hotkey] = {}
        thresholds_map[hotkey] = {}
        sample_counts_map[hotkey] = {}
        total_problems_map[hotkey] = {}

        first_block = score.get("first_block", 0)
        scores_by_env = score.get("scores_by_env", {})

        for env_name, env_data in scores_by_env.items():
            scores_by_env_map[hotkey][env_name] = dict(env_data)

        for env in envs:
            if env not in scores_by_env:
                continue
            env_data = scores_by_env[env]
            env_score = env_data.get("score", 0.0)
            sample_count = env_data.get("sample_count", 0)
            completeness = env_data.get("completeness", 0.0)

            if sample_count > 0 and env_score > 0:
                threshold = _calculate_required_score(
                    env_score, sample_count, Z_SCORE, MIN_IMPROVEMENT, MAX_IMPROVEMENT
                )
            else:
                threshold = env_data.get("threshold", 0.0)

            if env in scores_by_env_map[hotkey]:
                scores_by_env_map[hotkey][env]["threshold"] = threshold

            total_score = env_score * sample_count if sample_count > 0 else 0.0
            stats[hotkey][env] = {
                "samples": sample_count,
                "total_score": total_score,
                "first_block": first_block,
            }
            if sample_count > 0:
                lower = max(0.0, threshold)
                upper = min(1.0, env_score)
                confidence_intervals[hotkey][env] = (lower, upper)
            completeness_map[hotkey][env] = completeness
            thresholds_map[hotkey][env] = threshold
            sample_counts_map[hotkey][env] = sample_count
            if completeness > 0:
                total_problems = int(round(sample_count / completeness))
            else:
                total_problems = sample_count if sample_count > 0 else 0
            total_problems_map[hotkey][env] = total_problems

    return (
        stats,
        confidence_intervals,
        scores_by_env_map,
        completeness_map,
        thresholds_map,
        sample_counts_map,
        total_problems_map,
    )


def check_dominance(
    candidate_hotkey: str,
    target_hotkey: str,
    envs: Tuple[str, ...],
    scores_by_env_map: Dict[str, Dict[str, Dict[str, Any]]],
    stats: Dict[str, Dict[str, Dict[str, Any]]],
) -> bool:
    """
    True if candidate dominates target (validator-style: first_block ordering, threshold beat in all envs).
    """
    candidate_scores = scores_by_env_map.get(candidate_hotkey, {})
    target_scores = scores_by_env_map.get(target_hotkey, {})
    if not candidate_scores or not target_scores:
        return False

    candidate_first_block = None
    target_first_block = None
    for env in envs:
        c_env = stats.get(candidate_hotkey, {}).get(env, {})
        t_env = stats.get(target_hotkey, {}).get(env, {})
        if candidate_first_block is None and c_env.get("first_block") is not None:
            candidate_first_block = c_env["first_block"]
        if target_first_block is None and t_env.get("first_block") is not None:
            target_first_block = t_env["first_block"]

    if candidate_first_block is None or target_first_block is None:
        return False

    if candidate_first_block < target_first_block:
        miner_a_hotkey, miner_b_hotkey = candidate_hotkey, target_hotkey
        miner_a_scores, miner_b_scores = candidate_scores, target_scores
        checking_a_dominates_b = True
    elif target_first_block < candidate_first_block:
        miner_a_hotkey, miner_b_hotkey = target_hotkey, candidate_hotkey
        miner_a_scores, miner_b_scores = target_scores, candidate_scores
        checking_a_dominates_b = False
    else:
        return False

    eps = 1e-9
    a_wins_count = 0
    b_wins_count = 0
    valid_envs = []
    for env in envs:
        miner_a_data = miner_a_scores.get(env, {})
        miner_b_data = miner_b_scores.get(env, {})
        miner_a_score = miner_a_data.get("score", 0.0)
        miner_b_score = miner_b_data.get("score", 0.0)
        miner_a_samples = miner_a_data.get("sample_count", 0)
        miner_b_samples = miner_b_data.get("sample_count", 0)
        if miner_a_samples == 0 or miner_b_samples == 0:
            continue
        valid_envs.append(env)
        threshold = miner_a_data.get("threshold")
        if threshold is None:
            threshold = _calculate_required_score(
                miner_a_score, miner_a_samples, Z_SCORE, MIN_IMPROVEMENT, MAX_IMPROVEMENT
            )
        b_wins_env = miner_b_score > (threshold + eps)
        if b_wins_env:
            b_wins_count += 1
        else:
            a_wins_count += 1

    if not valid_envs:
        return False
    a_dominates_b = a_wins_count == len(valid_envs)
    b_dominates_a = b_wins_count == len(valid_envs)
    if checking_a_dominates_b:
        return a_dominates_b
    return b_dominates_a


def _parse_params_str_to_b(params_str: Optional[str]) -> Optional[float]:
    if not params_str or not isinstance(params_str, str):
        return None
    m = re.match(r"([\d,.]+)\s*([KMB])?", params_str.strip(), re.I)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (m.group(2) or "B").upper()
    if suffix == "K":
        return val * 1e-6
    if suffix == "M":
        return val * 1e-3
    return val


def _scrape_hf_model_size(repo_id: str, revision: Optional[str], token: Optional[str]) -> Tuple[Optional[str], Optional[float]]:
    headers = {"User-Agent": HF_SCRAPE_UA, "Accept": "text/html,application/xhtml+xml"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params_str, size_gb = None, None
    try:
        r = requests.get(f"https://huggingface.co/{repo_id}", headers=headers, timeout=10)
        if r.ok:
            m = _RE_PARAMS.search(r.text)
            if m:
                params_str = m.group(1)
    except Exception:
        pass
    try:
        branch = revision or "main"
        r = requests.get(f"https://huggingface.co/{repo_id}/tree/{branch}", headers=headers, timeout=10)
        if r.ok:
            m = _RE_SIZE_GB.search(r.text)
            if m:
                size_gb = float(m.group(1).replace(",", ""))
    except Exception:
        pass
    return params_str, size_gb


def get_model_size_sync(model_id: str) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    """Returns (size_str, size_gb, size_b). Cached."""
    default = (None, None, None)
    if not model_id:
        return default
    if model_id in _model_size_cache:
        return _model_size_cache[model_id]
    repo_id = model_id.split("@")[0].strip()
    revision = model_id.split("@", 1)[1].strip() if "@" in model_id else None
    token = os.environ.get("HF_TOKEN")
    params_str, size_gb = _scrape_hf_model_size(repo_id, revision, token)
    if params_str is not None or size_gb is not None:
        size_b = _parse_params_str_to_b(params_str)
        if size_b is not None and size_b >= 1e-3:
            size_str = params_str or (f"{int(size_b)}B" if size_b >= 1.0 else f"{size_b:.1f}B")
            if size_gb is None:
                size_gb = size_b * _BYTES_PER_PARAM
            _model_size_cache[model_id] = (size_str, size_gb, size_b)
            return (size_str, size_gb, size_b)
        if size_gb is not None and size_gb > 0:
            _model_size_cache[model_id] = (params_str, size_gb, _parse_params_str_to_b(params_str))
            return (params_str, size_gb, _parse_params_str_to_b(params_str))
    _model_size_cache[model_id] = default
    return default


def get_model_sizes_batch(models: List[str], max_per_batch: int = 10) -> Dict[str, Dict[str, Any]]:
    """Fetch size_str, size_gb, size_b for up to max_per_batch models. Returns { model_id: { size_str, size_gb, size_b } }."""
    results = {}
    for m in models[:max_per_batch]:
        size_str, size_gb, size_b = get_model_size_sync(m)
        results[m] = {"size_str": size_str, "size_gb": size_gb, "size_b": size_b}
    return results


def get_chute_id_sync(model_id: str) -> Optional[str]:
    if not model_id or not model_id.strip():
        return None
    model_id = model_id.strip()
    if model_id in _chute_id_cache:
        return _chute_id_cache[model_id]
    headers = {"User-Agent": HF_SCRAPE_UA, "Accept": "application/json"}
    try:
        url = CHUTES_API_BASE + "?name=" + urllib.parse.quote(model_id, safe="/")
        r = requests.get(url, headers=headers, timeout=6)
        if r.ok:
            data = r.json()
            items = data.get("items") or []
            chute_id = items[0].get("chute_id") if items else None
            if chute_id:
                _chute_id_cache[model_id] = chute_id
                return chute_id
    except Exception:
        pass
    if "@" in model_id:
        repo_only = model_id.split("@", 1)[0].strip()
        if repo_only:
            try:
                url = CHUTES_API_BASE + "?name=" + urllib.parse.quote(repo_only, safe="/")
                r = requests.get(url, headers=headers, timeout=6)
                if r.ok:
                    data = r.json()
                    items = data.get("items") or []
                    chute_id = items[0].get("chute_id") if items else None
                    _chute_id_cache[model_id] = chute_id
                    return chute_id
            except Exception:
                pass
    _chute_id_cache[model_id] = None
    return None


def fetch_chute_script_sync(chute_id: str) -> Optional[Dict[str, Any]]:
    """Fetch Chutes app page for chute_id?tab=source and extract script (source) and metadata."""
    if not chute_id or not chute_id.strip():
        return None
    chute_id = chute_id.strip()
    url = f"https://chutes.ai/app/chute/{chute_id}?tab=source"
    headers = {"User-Agent": HF_SCRAPE_UA, "Accept": "text/html"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if not r.ok:
            return None
        html = r.text
    except Exception:
        return None
    out: Dict[str, Any] = {"chute_id": chute_id, "source": None, "engine_args": None, "name": None}
    m2 = re.search(r"id:\s*2\s*,\s*data:\s*\"((?:[^\"\\]|\\.)*)\"\s*,\s*error:", html)
    if m2:
        raw = m2.group(1)
        raw = raw.replace("\\n", "\n").replace("\\t", "\t").replace("\\\"", '"').replace("\\\\", "\\")
        out["source"] = raw
        ea = re.search(r"engine_args\s*=\s*\((.*?)\)\s*(?:,|\n)\s*(?:\)|\w)", raw, re.DOTALL)
        if ea:
            out["engine_args"] = ea.group(1).strip()
    m1 = re.search(r"id:\s*1\s*,\s*data:\s*(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})\s*,\s*error:", html)
    if m1:
        try:
            import json as _json
            data1 = _json.loads(m1.group(1))
            out["name"] = data1.get("name")
        except Exception:
            pass
    return out if out.get("source") else None
