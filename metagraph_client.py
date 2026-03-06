"""
Resolve hotkey -> coldkey from Bittensor metagraph; fetch commits (chain commitments).
Uses bittensor module only; no imports from dominance_server.
"""

import asyncio
import json
import os
from typing import Any, Dict, List, Optional

# Optional: only used when bittensor is available
_metagraph_cache: Optional[Dict[str, str]] = None


def get_hotkey_to_coldkey_from_metagraph(
    network: Optional[str] = None,
    netuid: Optional[int] = None,
    use_cache: bool = True,
) -> Dict[str, str]:
    """
    Load metagraph and return hotkey -> coldkey (wallet address) for all UIDs.
    Uses bt.subtensor(network).metagraph(netuid). coldkeys[uid] is the wallet for hotkeys[uid].
    """
    global _metagraph_cache
    if use_cache and _metagraph_cache is not None:
        return _metagraph_cache

    network = network or os.environ.get("SUBTENSOR_NETWORK", "finney")
    netuid = netuid if netuid is not None else int(os.environ.get("NETUID", "120"))

    try:
        import bittensor as bt
    except ImportError:
        return {}

    out: Dict[str, str] = {}
    try:
        subtensor = bt.subtensor(network=network)
        metagraph = subtensor.metagraph(netuid)
        hotkeys = getattr(metagraph, "hotkeys", None)
        coldkeys = getattr(metagraph, "coldkeys", None)
        if hotkeys is not None and coldkeys is not None:
            n = min(len(hotkeys), len(coldkeys))
            for uid in range(n):
                hk = hotkeys[uid]
                ck = coldkeys[uid]
                if hk is not None and ck is not None:
                    hk_str = str(hk).strip()
                    ck_str = str(ck).strip()
                    if hk_str and ck_str:
                        out[hk_str] = ck_str
        if use_cache:
            _metagraph_cache = out
    except Exception:
        pass
    return out


def clear_metagraph_cache() -> None:
    """Clear cached hotkey->coldkey map (e.g. after network change)."""
    global _metagraph_cache
    _metagraph_cache = None


def _load_coldkey_manager_map() -> Dict[str, str]:
    """Load address.csv (coldkey -> manager)."""
    csv_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    csv_path = os.environ.get("ADDRESS_CSV", os.path.join(csv_dir, "address.csv"))
    out: Dict[str, str] = {}
    if not os.path.isfile(csv_path):
        return out
    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split("\t")
                if len(parts) >= 2:
                    coldkey, manager = parts[0].strip(), parts[1].strip()
                    if coldkey and manager and not coldkey.lower().startswith("wallet"):
                        out[coldkey] = manager
    except Exception:
        pass
    return out


async def _get_commitments_async(network: str, netuid: int, endpoint: Optional[str] = None) -> Dict[str, List[Any]]:
    """Fetch all revealed commitments via async subtensor."""
    try:
        import bittensor as bt
        async_subtensor = bt.async_subtensor(network=network, chain_endpoint=endpoint) if endpoint else bt.async_subtensor(network=network)
        try:
            return await async_subtensor.get_all_revealed_commitments(netuid)
        finally:
            await async_subtensor.close()
    except Exception:
        return {}


def get_commits(
    network: Optional[str] = None,
    netuid: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Get commit info for all UIDs from metagraph + chain commitments.
    Returns { "block": int, "commits": [ { uid, hotkey, model, revision, block_number, model_display, manager, coldkey }, ... ] }.
    """
    network = network or os.environ.get("SUBTENSOR_NETWORK", "finney")
    netuid = netuid if netuid is not None else int(os.environ.get("NETUID", "120"))
    try:
        import bittensor as bt
    except ImportError:
        return {"block": 0, "commits": []}
    try:
        subtensor = bt.subtensor(network=network)
        metagraph = subtensor.metagraph(netuid)
        _block = getattr(metagraph, "block", 0)
        current_block = int(_block.item()) if hasattr(_block, "item") else int(_block)
        hotkeys = getattr(metagraph, "hotkeys", [])
        coldkeys = getattr(metagraph, "coldkeys", [])
        all_commitments = asyncio.run(_get_commitments_async(network, netuid))
        coldkey_manager_map = _load_coldkey_manager_map()
        commits: List[Dict[str, Any]] = []
        for uid in range(len(hotkeys)):
            hotkey = str(hotkeys[uid]).strip() if uid < len(hotkeys) else ""
            coldkey = str(coldkeys[uid]).strip() if uid < len(coldkeys) else None
            manager = coldkey_manager_map.get(coldkey) if coldkey else None
            model = revision = block_number = model_display = None
            commitments_list = all_commitments.get(hotkey, [])
            if commitments_list:
                block_number, data = commitments_list[-1]
                try:
                    if isinstance(data, str):
                        data = json.loads(data)
                    model = data.get("model") or None
                    revision = data.get("revision") or None
                    if model:
                        model_display = f"{model}@{revision}" if revision else model
                except (json.JSONDecodeError, TypeError):
                    pass
            display_block = 0 if uid == 0 else block_number
            commits.append({
                "uid": uid,
                "hotkey": hotkey,
                "model": model,
                "revision": revision,
                "block_number": display_block,
                "model_display": model_display,
                "manager": manager,
                "coldkey": coldkey,
            })
        return {"block": current_block, "commits": commits}
    except Exception:
        return {"block": 0, "commits": []}
