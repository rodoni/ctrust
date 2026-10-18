import re
import unicodedata

NAMING_RULE_VERSION = "semantic-v1"
GENERIC = {"slice", "module", "unit", "implementation", "impl", "function", "functions"}
RUST_KEYWORDS = {"as", "break", "const", "continue", "crate", "else", "enum", "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod", "move", "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while", "async", "await", "dyn"}
ID_PLACEHOLDERS = {"s", "slice", "unit"}


def tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", ascii_value)
    raw = re.split(r"[^A-Za-z0-9]+", ascii_value)
    result = []
    for token in raw:
        if not token:
            continue
        token = token.lower()
        if token in GENERIC or token.isdigit():
            continue
        result.append(token)
    return result


def slice_description(slice_id: str) -> str:
    parts = [part for part in re.split(r"[-_]+", slice_id or "") if part]
    if len(parts) >= 3 and parts[1].isdigit():
        parts = parts[2:]
    elif parts and parts[0].lower() in {"slice", "unit", "kin", "parser"}:
        parts = parts[1:]
    return " ".join(parts)


def _candidate(source, value):
    parts = tokens(value or "")
    if not parts:
        return None
    stem = "_".join(parts)
    if stem[0].isdigit():
        stem = f"m_{stem}"
    if stem in RUST_KEYWORDS:
        stem = f"m_{stem}"
    return stem, source


def semantic_name(slice_input):
    # Free-form objectives are deliberately below the stable slice ID. This
    # prevents a prose paragraph from becoming a Rust filename/module name.
    description_parts = tokens(slice_description(slice_input.slice_id))
    candidates = [
        ("override", slice_input.name_override),
        ("slice_description", None if description_parts in ([], ["s"], ["unit"]) else " ".join(description_parts)),
        ("objective", slice_input.objective),
        ("behavior", slice_input.behavior_summary),
        ("contract", slice_input.contract_name),
        ("functions", " ".join(f.c_name or f.rust_name for f in slice_input.functions)),
    ]
    for source, value in candidates:
        result = _candidate(source, value)
        if result:
            return result
    return None, "missing"
