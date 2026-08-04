"""Predict what running an entry point would cost, without running it.

Everything here is read off the script's source and the repo's data files. The
numbers are approximations and the report says so — but they are *specific*
approximations, which is what makes a refusal checkable: "gpt2-large is about
3.1 GB and the ceiling is 512 MB" can be argued with. "Too expensive" cannot.

Erring high is the right bias. An underestimate spends the user's budget on a
run that was never going to fit; an overestimate produces a refusal that names
the model and the size, which the user can override with a bigger ceiling.
"""

from __future__ import annotations

import re

from ..map.analysis import forces_cpu, referenced_model_ids
from ..map.inventory import RepoInventory, ScriptFile
from .budget import CostEstimate

#: Published checkpoint sizes in MB for families whose name carries no
#: parameter count. Approximate, and only ever used to refuse or allow.
KNOWN_WEIGHTS_MB: dict[str, float] = {
    "gpt2": 548.0,
    "gpt2-medium": 1520.0,
    "gpt2-large": 3130.0,
    "gpt2-xl": 6430.0,
    "bert-base-uncased": 440.0,
    "bert-large-uncased": 1340.0,
    "roberta-base": 500.0,
    "roberta-large": 1430.0,
    "distilgpt2": 353.0,
    "t5-small": 242.0,
    "t5-base": 892.0,
    "t5-large": 2950.0,
}

#: `pythia-1.4b`, `opt-350m`, `llama-2-7b` — the count is in the name.
_PARAMETER_COUNT = re.compile(r"(\d+(?:\.\d+)?)\s*([bm])(?![a-z0-9])", re.IGNORECASE)

#: Two bytes per parameter is fp16, the usual serving precision on the Hub.
MB_PER_MILLION_PARAMETERS = 2.0

#: Coarse CPU scoring throughput, used only to compare against `--max-hours`.
SECONDS_PER_ROW_PER_GB = 0.05

#: Charged when a script clearly touches a GPU and we cannot see how long for.
DEFAULT_GPU_HOURS = 0.5


def weight_size_mb(model_id: str) -> float | None:
    """Approximate download size for a Hugging Face model id, or None."""
    name = model_id.rsplit("/", 1)[-1].lower()
    if name in KNOWN_WEIGHTS_MB:
        return KNOWN_WEIGHTS_MB[name]
    match = _PARAMETER_COUNT.search(name)
    if match is None:
        return None
    count = float(match.group(1))
    millions = count * 1000.0 if match.group(2).lower() == "b" else count
    return millions * MB_PER_MILLION_PARAMETERS


def largest_input_rows(inventory: RepoInventory) -> int:
    return max((artifact.n_rows for artifact in inventory.artifacts), default=0)


def estimate(script: ScriptFile, inventory: RepoInventory) -> CostEstimate:
    """What running `script` in this repo is predicted to cost."""
    download_basis: list[str] = []
    wall_basis: list[str] = []
    gpu_basis: list[str] = []
    download_mb = 0.0
    weights_gb = 0.0

    for model_id in referenced_model_ids(script):
        size = weight_size_mb(model_id)
        if size is None:
            continue
        download_mb += size
        weights_gb += size / 1024.0
        download_basis.append(f"{script.path} loads {model_id} (~{size / 1024:.1f} GB of weights)")

    rows = largest_input_rows(inventory)
    wall_seconds = rows * max(weights_gb, 0.0) * SECONDS_PER_ROW_PER_GB
    if rows and weights_gb:
        wall_basis.append(
            f"scoring {rows} rows against ~{weights_gb:.1f} GB of weights is roughly "
            f"{_duration(wall_seconds)} on CPU"
        )

    gpu_hours = 0.0
    if weights_gb and not forces_cpu(script):
        if "cuda" in script.text or "gpu" in script.text.lower():
            gpu_hours = DEFAULT_GPU_HOURS
            gpu_basis.append(f"{script.path} selects a GPU device and no run length is documented")
    elif weights_gb:
        gpu_basis.append(f"{script.path} pins itself to CPU, so no GPU time is at stake")

    return CostEstimate(
        wall_hours=wall_seconds / 3600.0,
        gpu_hours=gpu_hours,
        download_mb=download_mb,
        basis=tuple([*download_basis, *wall_basis, *gpu_basis]),
        wall_basis=tuple(wall_basis),
        gpu_basis=tuple(gpu_basis),
        download_basis=tuple(download_basis),
    )


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    return f"{seconds / 3600:.1f} hours"
