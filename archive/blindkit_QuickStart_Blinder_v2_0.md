# BlindKit v2.0 — Quick Start (Blinder, **keys & audit**)

> You manage both roots: private **BLINDER** (keys) and shared **EXPERIMENTER** (blinded outputs).

## Recommended repos
- BLINDER ROOT (private git): `./study_X_blinder/`
- EXPERIMENTER ROOT (separate git): `./study_X_experimenter/`

## One‑time init
```bash
python -m venv venv && source venv/bin/activate
pip install pillow qrcode

python blindkit_v2_0.py init-dual \
  --blinder-root ./study_X_blinder \
  --experimenter-root ./study_X_experimenter \
  --study-id STUDY_X
```

## Register animals (BLINDER)
```bash
python blindkit_v2_0.py register-animal \
  --blinder-root ./study_X_blinder \
  --animal-id RAT001 --sex F --weight 230g
# repeat per animal
```

## Plans (BLINDER; reproducible with YYYYMMDD seeds)

### Behavior (2×A, 2×B per animal)
```bash
python blindkit_v2_0.py plan-behavior \
  --blinder-root ./study_X_blinder \
  --date-seed 20250821 \
  --agents A B
```

### Physiology (50/50 cohort)
```bash
python blindkit_v2_0.py plan-physiology \
  --blinder-root ./study_X_blinder \
  --date-seed 20250821 \
  --agents A B
```

## Overlays / Labels (BLINDER)
### Behavior (per session)
```bash
python blindkit_v2_0.py overlay-behavior --blinder-root ./study_X_blinder
# Prompts: Animal ID, SESSION (1-4), SYRINGE_ID
```

### Physiology (one per animal)
```bash
python blindkit_v2_0.py overlay-physiology --blinder-root ./study_X_blinder
```

### Viral aliquot (micro‑label)
```bash
python blindkit_v2_0.py overlay-aliquot --blinder-root ./study_X_blinder
```

## Handoff & reconciliation
- Hand syringes/aliquots to experimenter. They log receipts.
- Reconcile receipts → mark overlays USED:
```bash
python blindkit_v2_0.py reconcile-usage \
  --blinder-root ./study_X_blinder \
  --experimenter-root ./study_X_experimenter
```

## Anatomy blinding (BLINDER → EXPERIMENTER)
```bash
python blindkit_v2_0.py blind-anatomy \
  --blinder-root ./study_X_blinder \
  --experimenter-root ./study_X_experimenter \
  --input-root  /data/histo_unblinded \
  --allow-missing-index \
  --seal
```
- Crossref with originals → **BLINDER/configs/anatomy_crossref.json**
- Blinded copies + manifest → **EXPERIMENTER/anatomy_blinded/** and **EXPERIMENTER/configs/anatomy_blinded_manifest.json**
- `--seal` creates **BLINDER/archives/anatomy_blinded_<timestamp>.zip** (+ `.sha256`).

## Post‑hoc packaging for review (BLINDER)
Create a single ZIP with keys + receipts + reports:
```bash
python blindkit_v2_0.py package-unblinding \
  --blinder-root ./study_X_blinder \
  --experimenter-root ./study_X_experimenter \
  --out ./study_X_unblinding_bundle.zip
```
Anyone can verify integrity:
```bash
python blindkit_v2_0.py verify-posthoc --bundle ./study_X_unblinding_bundle.zip
```