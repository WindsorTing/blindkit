# BlindKit v1.6 — Quick Start (Experimenter)

> You are **blinded** during injections & analysis. Your colleague runs overlays & anatomy blinding.

## Install

```bash
python -m venv venv && source venv/bin/activate
pip install pillow qrcode
```

## 1) Initialize and register animals

```bash
python blindkit_v1_6.py init-study --study-root ./study_X --study-id STUDY_X
python blindkit_v1_6.py register-animal --study-root ./study_X --animal-id RAT001 --sex F --weight 230g
# repeat for all animals
```

## 2) Behavior plan (2×A, 2×B per animal; reproducible)

```bash
python blindkit_v1_6.py plan-behavior --study-root ./study_X --date-seed 20250821 --agents A B
python blindkit_v1_6.py verify-behavior-plan --study-root ./study_X
```

### Injection day (behavior)
Your colleague issues the label. **You** inject blinded:

```bash
python blindkit_v1_6.py inject-scan --study-root ./study_X \
  --animal-id RAT001 --stage BEHAVIOR --session 1 \
  --photo /path/to/photo.jpg
# optionally: --qr-payload '{"animal":"RAT001","stage":"BEHAVIOR","session":1,...}'
```

## 3) Physiology plan (50/50 across cohort; reproducible)

```bash
python blindkit_v1_6.py plan-physiology --study-root ./study_X --date-seed 20250821 --agents A B
python blindkit_v1_6.py verify-physiology-plan --study-root ./study_X
```

### Injection (physiology)
Your colleague issues the label. **You** inject blinded:

```bash
python blindkit_v1_6.py inject-scan --study-root ./study_X \
  --animal-id RAT001 --stage PHYSIOLOGY \
  --photo /path/to/photo.jpg
```

## 4) Viral aliquot (single per animal)
(Overlay by colleague; you inject as below.)

```bash
python blindkit_v1_6.py inject-scan --study-root ./study_X \
  --animal-id RAT001 --stage VIRAL \
  --photo /path/to/photo.jpg
```

## 5) Anatomy — analyze blinded images
Your colleague hands you a **blinded copy**. Verify integrity (strict or tolerant mode):

```bash
# strict
python blindkit_v1_6.py verify-anatomy-crossref --study-root ./study_X
# tolerant to benign resaves / moved paths
python blindkit_v1_6.py verify-anatomy-crossref --study-root ./study_X --relax-paths --dhash-threshold 6
```

If you make scientifically acceptable edits, record provenance (optional but recommended):

```bash
python blindkit_v1_6.py record-derivative --study-root ./study_X \
  --parent /data/histo_blinded/ANA-AB12CD/IDX_003-007.tif \
  --child  /data/histo_working/ANA-AB12CD/IDX_003-007_adj.tif \
  --note "contrast +12%"
```

## 6) Verify chain & finalize
```bash
python blindkit_v1_6.py verify-all --study-root ./study_X
```
