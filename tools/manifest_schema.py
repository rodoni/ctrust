"""Compatibility normalization for slice manifests."""

from copy import deepcopy


class ManifestSchemaError(ValueError):
    pass


def _alias(item, canonical, legacy):
    canonical_value = item.get(canonical)
    legacy_value = item.get(legacy)
    if canonical_value is not None and legacy_value is not None and canonical_value != legacy_value:
        raise ManifestSchemaError(
            f"Conflicting manifest fields {canonical!r} and {legacy!r} in slice {item.get('id', '<unknown>')}"
        )
    return canonical_value if canonical_value is not None else legacy_value


def normalize_slice(item):
    """Return a normalized copy without rewriting the user's manifest."""
    normalized = deepcopy(item)
    objective = _alias(normalized, "objective", "goal")
    completion = _alias(normalized, "completion_criteria", "conclusion")
    if objective is not None:
        normalized["objective"] = objective
    if completion is not None:
        normalized["completion_criteria"] = completion
    return normalized
