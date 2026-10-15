"""Evaluation-only, lazy metric registry with no implicit metric selection.

``load_catalog()`` returns fresh JSON metadata. ``run_metrics(ids, context,
parameters_by_id=None)`` returns canonical results in first-requested order,
deduplicating aliases. Parameters are supplied per metric/alias, never inferred
from EDA or selected from evaluation labels. No ML dependency is imported here.
"""

from importlib import import_module
import json
from pathlib import Path


_PREFIX = "app.blocks.metrics."
_FILES = frozenset({
    "roc_auc", "average_precision", "brier_score", "calibration_curve",
    "confusion_matrix", "accuracy", "recall", "specificity", "precision",
    "negative_predictive_value", "f1", "decision_curve", "categorical_nri",
    "recall_at_k", "precision_at_k",
})
_CATALOG_PATH = Path(__file__).resolve().parents[3] / "config" / "metric-catalog.json"
_FIELDS = {
    "id", "name", "description", "module", "aliases", "requires",
    "requires_parameters", "requires_context",
}


def load_catalog() -> dict:
    """Read and validate metadata; only statically allowlisted module files exist."""
    try:
        catalog = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("Cannot read the evaluation metric catalog") from error
    if (
        not isinstance(catalog, dict)
        or set(catalog) != {"schema_version", "stage", "metrics"}
        or type(catalog["schema_version"]) is not int
        or catalog["schema_version"] != 1
        or catalog["stage"] != "evaluation"
        or not isinstance(catalog["metrics"], list)
    ):
        raise ValueError("Invalid evaluation metric catalog schema")
    seen = set()
    identifiers = set()
    for entry in catalog["metrics"]:
        if not isinstance(entry, dict) or set(entry) != _FIELDS:
            raise ValueError("Invalid metric catalog entry fields")
        identifier = entry["id"]
        if not isinstance(identifier, str) or identifier not in _FILES:
            raise ValueError("Metric id is outside the static allowlist")
        if identifier in identifiers:
            raise ValueError("Duplicate metric id in catalog")
        identifiers.add(identifier)
        if entry["module"] != _PREFIX + identifier:
            raise ValueError("Metric module must match the trusted prefix and static file allowlist")
        if any(not isinstance(entry[key], str) or not entry[key].strip() for key in ("name", "description")):
            raise ValueError("Metric name and description must be nonempty strings")
        for key in ("aliases", "requires", "requires_parameters", "requires_context"):
            if not isinstance(entry[key], list) or any(
                not isinstance(value, str) or not value.strip() for value in entry[key]
            ):
                raise ValueError(f"Metric {key} must be a list of nonempty strings")
            if len(set(entry[key])) != len(entry[key]):
                raise ValueError(f"Duplicate metric {key} entries")
        for token in [identifier, *entry["aliases"]]:
            normalized = token.strip().lower()
            if normalized in seen:
                raise ValueError("Ambiguous or duplicate metric aliases")
            seen.add(normalized)
    if identifiers != _FILES:
        raise ValueError("Metric catalog must contain the complete static metric allowlist")
    return catalog


def run_metrics(ids, context: dict, parameters_by_id: dict | None = None) -> list[dict]:
    """Run explicitly requested blocks, once per canonical id, without model IO."""
    if not isinstance(ids, (list, tuple)) or any(not isinstance(item, str) for item in ids):
        raise ValueError("ids must be a list or tuple of metric identifiers")
    if not isinstance(context, dict):
        raise ValueError("context must be a dict")
    if parameters_by_id is None:
        parameters_by_id = {}
    if not isinstance(parameters_by_id, dict):
        raise ValueError("parameters_by_id must be a dict or None")
    catalog = load_catalog()
    aliases = {}
    for entry in catalog["metrics"]:
        for name in [entry["id"], *entry["aliases"]]:
            aliases[name.strip().lower()] = entry["id"]

    def canonical(name):
        if not isinstance(name, str) or name.strip().lower() not in aliases:
            raise ValueError(f"Unknown metric id: {name!r}")
        return aliases[name.strip().lower()]

    selected = list(dict.fromkeys(canonical(identifier) for identifier in ids))
    parameters = {}
    for name, values in parameters_by_id.items():
        identifier = canonical(name)
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise ValueError("Each parameters_by_id value must be a dict or None")
        if identifier in parameters and parameters[identifier] != values:
            raise ValueError(f"Conflicting parameters supplied for aliases of {identifier}")
        parameters[identifier] = values
    return [
        import_module(_PREFIX + identifier).run(context, parameters.get(identifier))
        for identifier in selected
    ]
