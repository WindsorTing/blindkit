# BlindKit v2.0 — Quick Start (Experimenter, **blinded**)

> You operate **only** inside the EXPERIMENTER ROOT. You never touch the blinder repo.

## Setup (once)
```bash
python -m venv venv && source venv/bin/activate
pip install pillow qrcode
```

## Your repo
- EXPERIMENTER ROOT: `./study_X_experimenter/`

## A) Day‑to‑day injections (blinded)

### Behavior (4 sessions per animal)
1) Receive syringe with QR/label from blinder.
2) Photograph syringe + label before injection.
3) Log a **receipt** in your repo:
```bash
python blindkit_v2_0.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage BEHAVIOR \
  --session 1 \
  --photo /path/to/RAT001_beh_s1.jpg
```

### Physiology (single terminal injection)
```bash
python blindkit_v2_0.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage PHYSIOLOGY \
  --photo /path/to/RAT001_phys.jpg
```

### Viral aliquot (single per animal)
```bash
python blindkit_v2_0.py inject-scan \
  --experimenter-root ./study_X_experimenter \
  --animal-id RAT001 \
  --stage VIRAL \
  --photo /path/to/RAT001_viral.jpg
```

## B) Anatomy (blinded analysis)

**Verify your blinded set strictly**
```bash
python blindkit_v2_0.py verify-anatomy-blinded \
  --experimenter-root ./study_X_experimenter
```

*(If files were resaved/moved and strict verify fails, ask the blinder to cross‑reference using their private crossref.)*

## C) Record legitimate edits (optional provenance)
```bash
python blindkit_v2_0.py record-derivative \
  --experimenter-root ./study_X_experimenter \
  --parent /path/to/anatomy_blinded/ANA-XXXXXX/IDX_003-007.tif \
  --child  /path/to/anatomy_working/ANA-XXXXXX/IDX_003-007_adj.tif \
  --note "contrast +12%"
```

## D) Post‑hoc (after unblinding)
Ask the blinder for the **unblinding bundle** ZIP. Anyone can verify:
```bash
python blindkit_v2_0.py verify-posthoc --bundle ./study_X_unblinding_bundle.zip
```