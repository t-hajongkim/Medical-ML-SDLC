"""Pure, aggregate-only evaluation of already supplied labels/probabilities.

Calling these calculation functions grants neither human approval nor clinical
use approval. A future upper-level caller must verify any required approval.
This executor is not connected to the current Actions path for PR1 planning.
The separate merged-plan runner in model_evaluation verifies the merge and calls
this library. It does not load models, predict, train, access the network/GitHub,
or implement a PR2 workflow.
``planning_complete`` only prevents calculation of incomplete metadata plans;
it is not evidence of human approval, authorship, or clinical performance.
"""

import math
from numbers import Integral

from app.blocks import metrics
from app.blocks.metrics import _validation


__all__ = ["execute_metrics", "execute_plan"]

_CONTEXT_FIELDS = {
    "y_true", "y_probability", "reference_probabilities", "sample_ids",
    "reference_sample_ids", "clinical_action", "comparison_model",
}
_PARAMETER_FIELDS = {"threshold", "thresholds", "risk_thresholds", "top_fraction", "n_bins"}
_THRESHOLD_IDS = {
    "confusion_matrix", "accuracy", "recall", "specificity", "precision",
    "negative_predictive_value", "f1",
}
_REASONS = {
    "missing_inputs", "empty_input", "missing_parameters", "missing_context",
    "zero_denominator", "both_classes_required", "positive_class_required",
}


def _registry():
    entries = {entry["id"]: entry for entry in metrics.load_catalog()["metrics"]}
    aliases = {
        name.strip().lower(): identifier
        for identifier, entry in entries.items()
        for name in [identifier, *entry["aliases"]]
    }
    return entries, aliases


def _canonical(name, aliases):
    if not isinstance(name, str) or name.strip().lower() not in aliases:
        raise ValueError("Unknown metric id")
    return aliases[name.strip().lower()]


def _selection(identifiers, aliases):
    if not isinstance(identifiers, (list, tuple)):
        raise ValueError("identifiers must be a list or tuple of registered metric ids")
    return list(dict.fromkeys(_canonical(name, aliases) for name in identifiers))


def _parameter_names(entry):
    return set(entry["requires_parameters"]) | (
        {"n_bins"} if entry["id"] == "calibration_curve" else set()
    )


def _parameters(values, allowed, *, allow_none=True):
    if not isinstance(values, dict):
        raise ValueError("Metric parameters must be a dict")
    if set(values) - allowed:
        raise ValueError("Unsupported metric parameter")
    result = {}
    for name, value in values.items():
        if value is None and allow_none and name != "n_bins":
            result[name] = None
        elif name in ("thresholds", "risk_thresholds"):
            result[name] = _validation.cutoffs(value, name)
        elif name == "n_bins":
            if isinstance(value, bool) or not isinstance(value, Integral) or value < 1:
                raise ValueError("n_bins must be a positive integer")
            result[name] = int(value)
        else:
            result[name] = _validation.probability(value, name)
            if name == "top_fraction" and result[name] == 0:
                raise ValueError("top_fraction must be greater than zero")
    return result


def _invalid_output():
    raise ValueError("Invalid registered metric output: expected aggregate-only result shape")


def _number(value):
    if type(value) not in (int, float) or (
        type(value) is float and not math.isfinite(value)
    ):
        _invalid_output()
    return value


def _count(value):
    if type(value) is not int or value < 0:
        _invalid_output()
    return value


def _probability(value):
    value = _number(value)
    if not 0 <= value <= 1:
        _invalid_output()
    return value


def _nullable_probability(value):
    return None if value is None else _probability(value)


def _text(value):
    if not isinstance(value, str) or not value.strip():
        _invalid_output()
    return value


def _boolean(value):
    if type(value) is not bool:
        _invalid_output()
    return value


def _list(value, validator):
    if not isinstance(value, list):
        _invalid_output()
    return [validator(item) for item in value]


def _record(value, fields, required=None):
    if (
        not isinstance(value, dict)
        or set(value) - set(fields)
        or set(fields if required is None else required) - set(value)
    ):
        _invalid_output()
    return {key: fields[key](item) for key, item in value.items()}


def _names(value, allowed):
    if (
        not isinstance(value, list) or not value
        or any(not isinstance(item, str) or item not in allowed for item in value)
    ):
        _invalid_output()
    return list(value)


def _counts(value):
    return _record(value, {key: _count for key in ("tn", "fp", "fn", "tp")})


def _matrix(value):
    if not isinstance(value, list) or len(value) != 2:
        _invalid_output()
    if any(not isinstance(row, list) or len(row) != 2 for row in value):
        _invalid_output()
    return [_list(row, _count) for row in value]


def _labels(value):
    if type(value) is not list or len(value) != 2:
        _invalid_output()
    if any(type(item) is not int for item in value) or value != [0, 1]:
        _invalid_output()
    return [0, 1]


_DETAIL_VALIDATORS = {
    "definition": _text, "sample_count": _count, "positive_count": _count,
    "negative_count": _count, "denominator": _count,
    "not_trapezoidal_pr_area": _boolean, "strategy": _text, "n_bins": _count,
    "bin_rule": _text, "threshold": _probability, "positive_rule": _text,
    "counts": _counts, "matrix_axes": _text, "numerator": _count,
    "clinical_action": _text, "interpretation": _text,
    "risk_thresholds": lambda value: _list(value, _probability),
    "category_rule": _text, "paired_sample_ids_verified": _boolean,
    "comparison_model": _text, "precondition": _text,
    "movements": lambda value: _record(value, {
        key: _count for key in ("event_up", "event_down", "nonevent_up", "nonevent_down")
    }),
    "top_fraction": _probability, "rounding": _text, "alert_count": _count,
    "captured_positives": _count,
    "selected_indices": lambda value: _list(value, _count),
    "tie_policy": _text, "boundary_tie": _boolean,
    "reason": _text,
    "missing_inputs": lambda value: _names(value, _CONTEXT_FIELDS - {
        "clinical_action", "comparison_model",
    }),
    "missing_parameters": lambda value: _names(value, _PARAMETER_FIELDS),
    "missing_context": lambda value: _names(value, {"clinical_action", "comparison_model"}),
}
_BASE_DETAILS = {"definition", "sample_count", "denominator"}
_DETAIL_KEYS = {
    "roc_auc": _BASE_DETAILS | {"positive_count", "negative_count"},
    "average_precision": _BASE_DETAILS | {"positive_count", "not_trapezoidal_pr_area"},
    "brier_score": _BASE_DETAILS,
    "calibration_curve": _BASE_DETAILS | {"strategy", "n_bins", "bin_rule"},
    "decision_curve": _BASE_DETAILS | {"clinical_action", "positive_rule", "interpretation"},
    "categorical_nri": _BASE_DETAILS | {
        "positive_count", "negative_count", "risk_thresholds", "category_rule",
        "paired_sample_ids_verified", "comparison_model", "interpretation",
        "precondition", "movements",
    },
}
for _identifier in _THRESHOLD_IDS:
    _DETAIL_KEYS[_identifier] = _BASE_DETAILS | {"threshold", "positive_rule", "counts"} | (
        {"matrix_axes"} if _identifier == "confusion_matrix" else {"numerator"}
    )
for _identifier in ("recall_at_k", "precision_at_k"):
    _DETAIL_KEYS[_identifier] = _BASE_DETAILS | {
        "top_fraction", "rounding", "alert_count", "captured_positives",
        "positive_count", "selected_indices", "tie_policy", "boundary_tie",
    }


def _value(identifier, value):
    if identifier == "confusion_matrix":
        return _record(value, {
            **{key: _count for key in ("tn", "fp", "fn", "tp")},
            "labels": _labels, "matrix": _matrix,
        })
    if identifier == "calibration_curve":
        return _list(value, lambda row: _record(row, {
            "bin": _count, "lower": _probability, "upper": _probability,
            "upper_inclusive": _boolean, "count": _count,
            "mean_predicted_probability": _nullable_probability,
            "observed_event_rate": _nullable_probability,
        }))
    if identifier == "decision_curve":
        return _list(value, lambda row: _record(row, {
            "threshold": _probability, "model_net_benefit": _number,
            "treat_all_net_benefit": _number, "treat_none_net_benefit": _number,
        }))
    if identifier == "categorical_nri":
        return _record(value, {
            key: _number for key in ("event_component", "nonevent_component", "total")
        })
    return _probability(value)


def _aggregate(output, identifier):
    if not isinstance(output, dict) or set(output) != {"block", "status", "value", "details"}:
        _invalid_output()
    if output["block"] != identifier or output["status"] not in ("completed", "not_applicable"):
        _invalid_output()
    completed = output["status"] == "completed"
    names = _DETAIL_KEYS[identifier] | {
        "reason", "missing_inputs", "missing_parameters", "missing_context",
    }
    validators = {name: _DETAIL_VALIDATORS[name] for name in names}
    if identifier == "calibration_curve":
        validators["denominator"] = _text
    elif identifier == "categorical_nri":
        validators["denominator"] = lambda value: _record(
            value, {"event": _count, "nonevent": _count},
        )
    details = _record(
        output["details"], validators,
        _DETAIL_KEYS[identifier] if completed else {"reason"},
    )
    if completed:
        if any(name in details for name in ("reason", "missing_inputs", "missing_parameters", "missing_context")):
            _invalid_output()
        value = _value(identifier, output["value"])
        if identifier == "calibration_curve" and len(value) != details["n_bins"]:
            _invalid_output()
        if identifier in ("calibration_curve", "decision_curve") and not value:
            _invalid_output()
    else:
        if output["value"] is not None or details["reason"] not in _REASONS:
            _invalid_output()
        value = None
    # The registry's top-fraction blocks expose selected row positions. Keep their
    # aggregate counts/tie policy, but never publish those row-level positions.
    details.pop("selected_indices", None)
    return {
        "block": identifier, "status": output["status"], "value": value, "details": details,
    }


def execute_metrics(identifiers, context, parameters_by_id=None):
    """Run only explicit registered ids/aliases and return aggregate-only results.

    Aliases execute once in first-requested order. Missing prerequisites preserve
    each block's ``not_applicable`` result; no threshold is invented. Unknown or
    conflicting parameters and invalid block output raise ValueError without
    embedding labels, probabilities, sample identifiers, or reference arrays.
    This calculation API does not confer or verify human/clinical approval.
    """
    if not isinstance(context, dict):
        raise ValueError("context must be a dict")
    entries, aliases = _registry()
    selected = _selection(identifiers, aliases)
    if parameters_by_id is None:
        parameters_by_id = {}
    if not isinstance(parameters_by_id, dict):
        raise ValueError("parameters_by_id must be a dict or None")
    parameters = {}
    for name, values in parameters_by_id.items():
        identifier = _canonical(name, aliases)
        values = _parameters({} if values is None else values, _parameter_names(entries[identifier]))
        if identifier in parameters and parameters[identifier] != values:
            raise ValueError("Conflicting parameters supplied for metric aliases")
        parameters[identifier] = values
    execution_context = {key: context[key] for key in _CONTEXT_FIELDS if key in context}
    output = metrics.run_metrics(selected, execution_context, parameters)
    if not isinstance(output, list) or len(output) != len(selected):
        _invalid_output()
    return [_aggregate(item, identifier) for item, identifier in zip(output, selected)]


def execute_plan(plan, context):
    """Execute the evaluation metadata returned by planning_eval.build_plans.

    Require schema_version=1, stage=evaluation, ai_status=provided and literal
    planning_complete=True solely to reject incomplete plans, not to establish
    human approval. The merged-plan controller checks approval separately;
    PR1 recommendation tools do not call this calculation function.

    Flat plan parameters are routed only to applicable selected blocks. Explicit
    plan descriptions can supply missing context, never replace conflicting
    context or fabricate clinical actions/reference models. Null thresholds stay
    unspecified. Neither the plan nor the caller's context is modified.
    """
    required = {
        "schema_version", "stage", "ai_status", "planning_complete", "selected_ids",
        "threshold", "parameters", "clinical_action", "comparison_model",
    }
    if not isinstance(plan, dict) or required - set(plan):
        raise ValueError("Evaluation plan is missing required metadata fields")
    if type(plan["schema_version"]) is not int or plan["schema_version"] != 1:
        raise ValueError("Evaluation plan requires schema_version 1")
    if plan["stage"] != "evaluation":
        raise ValueError("Evaluation plan requires stage evaluation")
    if plan["ai_status"] != "provided" or plan["planning_complete"] is not True:
        raise ValueError("Evaluation plan requires provided AI metadata and planning_complete=True")
    if not isinstance(plan["selected_ids"], list):
        raise ValueError("Evaluation plan selected_ids must be a list")
    if not isinstance(context, dict):
        raise ValueError("context must be a dict")
    entries, aliases = _registry()
    selected = _selection(plan["selected_ids"], aliases)
    parameters = _parameters(plan["parameters"], _PARAMETER_FIELDS, allow_none=False)
    if plan["threshold"] is not None:
        threshold = _validation.probability(plan["threshold"], "threshold")
        if "threshold" in parameters and parameters["threshold"] != threshold:
            raise ValueError("Conflicting plan threshold and parameters.threshold")
        parameters["threshold"] = threshold
    execution_context = {key: context[key] for key in _CONTEXT_FIELDS if key in context}
    for name in ("clinical_action", "comparison_model"):
        planned = plan[name]
        current = execution_context.get(name)
        for value in (planned, current):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be nonempty text or None")
        if planned is not None:
            if current is not None and current != planned:
                raise ValueError(f"Conflicting plan and context {name}")
            execution_context[name] = planned
    by_id = {
        identifier: {
            name: value for name, value in parameters.items()
            if name in _parameter_names(entries[identifier])
        }
        for identifier in selected
    }
    return execute_metrics(selected, execution_context, by_id)
