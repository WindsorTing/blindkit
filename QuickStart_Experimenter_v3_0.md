# BlindKit v3.0 — Quick Start (Experimenter, **blinded**)

> You operate **only** inside the EXPERIMENTER root. Do not touch the blinder repo.

## Setup
```bash
python -m venv venv && source venv/bin/activate
pip install pillow qrcode
```

## Your repo (layout)
- `study_X_experimenter/`
  - `media/photos/<ANIMAL>/<STAGE>/...` — optional photos taken before injection
  - `receipts/*.json` — append-only injection receipts
  - `configs/anatomy_blinded_manifest.json` — blinded anatomy manifest
  - `provenance/*.json` — edit lineage (optional)
  - `audit/actions.jsonl` + `audit/actions.log` — your audit trail

## Day‑to‑day injections
Receive a syringe/aliquot with a blinded label (and QR). Photograph it, then log a receipt.

### Behavior (4 sessions per animal)
```bash
python blindkit_v3_0.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage BEHAVIOR \
  --session 1 \
  --photo /path/to/RAT001_beh_s1.jpg
```

### Physiology (single terminal)
```bash
python blindkit_v3_0.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage PHYSIOLOGY \
  --photo /path/to/RAT001_phys.jpg
```

### Viral aliquot (single per animal)
```bash
python blindkit_v3_0.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage VIRAL \
  --photo /path/to/RAT001_viral.jpg
```

## Anatomy (blinded analysis)
**Verify the blinded set before analysis.**
```bash
python blindkit_v3_0.py verify-anatomy-blinded \
  --experimenter-root ./study_X_experimenter
```

## Record legitimate edits (optional provenance)
```bash
python blindkit_v3_0.py record-derivative \
  --experimenter-root ./study_X_experimenter \
  --parent /path/to/anatomy_blinded/ANA-XXXXXX/IDX_003-007.tif \
  --child  /path/to/anatomy_working/ANA-XXXXXX/IDX_003-007_adj.tif \
  --note "contrast +12%"
```

## Post‑hoc verification (after unblinding package is handed over)
```bash
python blindkit_v3_0.py verify-posthoc \
  --bundle ./study_X_unblinding_bundle.zip \
  --experimenter-root ./study_X_experimenter
```

## Audit log queries
```bash
# Last 20 actions you performed
python blindkit_v3_0.py audit-show --root ./study_X_experimenter --tail 20

# Only injection receipts for RAT001
python blindkit_v3_0.py audit-show --root ./study_X_experimenter --action inject-scan --animal RAT001

# Entries mentioning PHYSIOLOGY
python blindkit_v3_0.py audit-show --root ./study_X_experimenter --grep PHYSIOLOGY
```

## Tips
- Keep your repository under version control (separate from the blinder repo).
- Never create or modify files under `labels/` — those live in the blinder repo only.
- If a verify step fails, stop and contact the blinder for reconciliation.