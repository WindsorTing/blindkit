# BlindKit v1.7 — Quick Start (Blinder side, **keys & audit**)

> You manage the **BLINDER ROOT** with true keys and the **EXPERIMENTER ROOT** for blinded outputs.

## Recommended repos
- BLINDER ROOT (private git): `./study_X_blinder/`
- EXPERIMENTER ROOT (separate git, shared with experimenter): `./study_X_experimenter/`

## One‑time init
```bash
python -m venv venv && source venv/bin/activate
pip install pillow qrcode

python blindkit_v1_7.py init-dual \
  --blinder-root ./study_X_blinder \
  --experimenter-root ./study_X_experimenter \
  --study-id STUDY_X
```

## Register animals (BLINDER)
```bash
python blindkit_v1_7.py register-animal \
  --blinder-root ./study_X_blinder \
  --animal-id RAT001 --sex F --weight 230g
# repeat...
```

## Plans (BLINDER, reproducible seeds)
### Behavior (2×A, 2×B per animal)
```bash
python blindkit_v1_7.py plan-behavior \
  --blinder-root ./study_X_blinder \
  --date-seed 20250821 \
  --agents A B
```

### Physiology (50/50 cohort)
```bash
python blindkit_v1_7.py plan-physiology \
  --blinder-root ./study_X_blinder \
  --date-seed 20250821 \
  --agents A B
```

## Overlays / Labels (BLINDER)
### Behavior (per session)
```bash
python blindkit_v1_7.py overlay-behavior \
  --blinder-root ./study_X_blinder
# Prompts: Animal ID, SESSION (1-4), SYRINGE_ID
```

### Physiology (one per animal)
```bash
python blindkit_v1_7.py overlay-physiology \
  --blinder-root ./study_X_blinder
```

### Viral aliquot (micro‑label)
```bash
python blindkit_v1_7.py overlay-aliquot \
  --blinder-root ./study_X_blinder
```

## Handoff: injections
- Give the experimenter the **physical** labeled syringes/aliquots.
- They will log receipts in their EXPERIMENTER ROOT.

## Reconcile receipts (BLINDER)
```bash
python blindkit_v1_7.py reconcile-usage \
  --blinder-root ./study_X_blinder \
  --experimenter-root ./study_X_experimenter
```
This marks the matching registry entries as **USED** and attaches the injection photo hash.

## Anatomy blinding (BLINDER → EXPERIMENTER)
```bash
python blindkit_v1_7.py blind-anatomy \
  --blinder-root ./study_X_blinder \
  --experimenter-root ./study_X_experimenter \
  --input-root  /data/histo_unblinded \
  --allow-missing-index \
  --seal
```
- Writes full cross‑ref (with originals) to **BLINDER/configs**.
- Writes **blinded images** to **EXPERIMENTER/anatomy_blinded** and a blinded‑only manifest to **EXPERIMENTER/configs**.
- `--seal` creates a ZIP archive in **BLINDER/archives** (+ `.sha256`).

*(You can run stricter cross‑reference checks manually by comparing BLINDER crossref to EXPERIMENTER files.)*

## Git/Immutability tips
- Keep BLINDER and EXPERIMENTER in **separate private repos**.
- Commit after each logical step; push to a remote for timestamping.
- Tag releases at milestones (e.g., after blinding handoff).
