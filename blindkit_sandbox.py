import json
from pathlib import Path
from typing import Any, Dict, Iterable, Set

registry_path = Path(r"C:\Users\wt2341\OneDrive - cumc.columbia.edu\Desktop\blindkit_blinder\labels\registry.json")

DOMAINS = ("viral_aliquot", "physiology")

def _extract_domain_labels(assignment: Dict[str, Any]) -> Iterable[str]:
    """Yield labels from known domains inside an 'assignment' block."""
    for d in DOMAINS:
        sub = assignment.get(d)
        if isinstance(sub, dict):
            lbl = sub.get("label")
            if isinstance(lbl, str):
                yield lbl

def get_universe_labels(registry_path: Path) -> set[str]:
    """
    Return the full universe of labels across all animals, entries, and domains.
    """
    with registry_path.open("r") as f:
        data = json.load(f)

    labels: Set[str] = set()
    for entry in data.get("entries", []):
        assignment = entry.get("assignment", {})
        if isinstance(assignment, dict):
            labels.update(_extract_domain_labels(assignment))
    return labels

used = get_universe_labels(registry_path)
print(used)