# BlindKit v1.7 — Quick Start (Experimenter side, **blinded**)

> You only use the **EXPERIMENTER ROOT**. You never touch the blinder repo.

## Setup
```bash
python -m venv venv && source venv/bin/activate
pip install pillow qrcode
```

## Paths
- EXPERIMENTER ROOT (your repo): `./study_X_experimenter/`

## A) Day‑to‑day injections (blinded)

### Behavior (4 sessions per animal)
1) Receive a syringe with QR/label.
2) Photograph syringe + label before injection.
3) Log a **receipt** at your root:
```bash
python blindkit_v1_7.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage BEHAVIOR \
  --session 1 \
  --photo /path/to/RAT001_beh_s1.jpg
```
This stores a receipt JSON under `EXPERIMENTER/receipts/`. Your colleague will **reconcile** it on their side.

### Physiology (single terminal injection)
```bash
python blindkit_v1_7.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage PHYSIOLOGY \
  --photo /path/to/RAT001_phys.jpg
```

### Viral aliquot (single per animal)
```bash
python blindkit_v1_7.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage VIRAL \
  --photo /path/to/RAT001_viral.jpg
```

## B) Anatomy (blinded analysis)
You will be handed a blinded folder under your root (`anatomy_blinded/`) and a blinded‑only manifest.

**Verify strictly**
```bash
python blindkit_v1_7.py verify-anatomy-blinded \
  --experimenter-root ./study_X_experimenter
```

*(For tolerant verification against resaves/moves, ask the blinder to run the cross‑ref on their side.)*

## C) Provenance of allowed edits (optional)
```bash
python blindkit_v1_7.py record-derivative \
  --experimenter-root ./study_X_experimenter \
  --parent /path/to/anatomy_blinded/ANA-XXXXXX/IDX_003-007.tif \
  --child  /path/to/anatomy_working/ANA-XXXXXX/IDX_003-007_adj.tif \
  --note   "contrast +12%"
```

**That’s it.** You never see the keys; your receipts + hashes ensure auditability without unblinding.
