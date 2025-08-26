# BlindKit v4.0 — Quick Start (Experimenter, **blinded**)

> You operate **only** inside the EXPERIMENTER root. You should not have the blinder repo on your laptop.

## Environment (choose one)
**Conda (recommended)**
```bash
conda env create -f environment.yml
conda activate blindkit
```
**Or: venv + pip**
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Initialize your repo (on your laptop)
Create only the experimenter tree:
```bash
python blindkit_v4_0.py init-dual \
  --study-id STUDY_X \
  --experimenter-root ./study_X_experimenter \
  --only experimenter
```

**Layout**
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
python blindkit_v4_0.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage BEHAVIOR \
  --session 1 \
  --photo /path/to/RAT001_beh_s1.jpg
```

### Physiology (single terminal)
```bash
python blindkit_v4_0.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage PHYSIOLOGY \
  --photo /path/to/RAT001_phys.jpg
```

### Viral aliquot (single per animal)
```bash
python blindkit_v4_0.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage VIRAL \
  --photo /path/to/RAT001_viral.jpg
```

> Tip: if your label includes a QR, you can copy its JSON payload and pass it via `--qr-payload '...json...'` to avoid manual entry.

## Anatomy (blinded analysis)
Verify the blinded set before analysis:
```bash
python blindkit_v4_0.py verify-anatomy-blinded \
  --experimenter-root ./study_X_experimenter
```

## Provenance for legitimate edits (optional)
```bash
python blindkit_v4_0.py record-derivative \
  --experimenter-root ./study_X_experimenter \
  --parent /path/to/anatomy_blinded/ANA-XXXXXX/IDX_003-007.tif \
  --child  /path/to/anatomy_working/ANA-XXXXXX/IDX_003-007_adj.tif \
  --note "contrast +12%"
```

## Post‑hoc verification (after unblinding package is handed over)
```bash
python blindkit_v4_0.py verify-posthoc \
  --bundle ./study_X_unblinding_bundle.zip \
  --experimenter-root ./study_X_experimenter
```

## Audit log queries
```bash
# Last 20 actions you performed
python blindkit_v4_0.py audit-show --root ./study_X_experimenter --tail 20

# Only injection receipts for RAT001
python blindkit_v4_0.py audit-show --root ./study_X_experimenter --action inject-scan --animal RAT001

# Entries mentioning PHYSIOLOGY
python blindkit_v4_0.py audit-show --root ./study_X_experimenter --grep PHYSIOLOGY
```

## Good practice
- Keep this repo under version control (separate from the blinder repo).
- Do not create/modify any `labels/` files — those live in the blinder repo only.
- If a verify step fails, stop and contact the blinder for reconciliation.