# BlindKit v1.6 — Quick Start (Blinder)

> You run overlays (issue labels) and create the blinded anatomy set. The experimenter stays blinded.

## Install

```bash
python -m venv venv && source venv/bin/activate
pip install pillow qrcode
```

## 1) Behavior overlay (per session)

```bash
python blindkit_v1_6.py overlay-behavior --study-root ./study_X
# prompted: Animal ID, Session(1-4), base SYRINGE_ID, optional photo
# A label file (and optional QR) is created; registry updated.
```

## 2) Physiology overlay (one per animal; plan required)

```bash
python blindkit_v1_6.py overlay-physiology --study-root ./study_X
# shows planned agent (to you only), prompts for base SYRINGE_ID, logs overlay
```

## 3) Viral aliquot overlay (single per animal; micro label)

```bash
python blindkit_v1_6.py overlay-aliquot --study-root ./study_X
# prompts for ALIQUOT_ID, creates micro label, updates registry
```

## 4) Anatomy blinding (create blinded copy for experimenter)

```
python blindkit_v1_6.py blind-anatomy \
  --study-root ./study_X \
  --input-root  /data/histo_unblinded \
  --output-root /data/histo_blinded \
  --seal \
  --working-root /data/histo_working
```

- Parses **INDEX M-N** from filenames, preserves order, strips metadata.
- Writes per‑folder `series.csv` with **SHA‑256 + dHash** of original & blinded files.
- Global manifests: `configs/anatomy_blind_map.{json,csv}`, `configs/anatomy_crossref.{json,csv}` (+ `.sha256`).
- `--seal` creates a **ZIP archive** of blinded output + manifests (with `.sha256` pin).
- `--working-root` (optional) makes a separate writable copy for analysis.

### After handoff
- Give the experimenter: `/data/histo_blinded` (or working copy).
- Keep the sealed archive & manifests in `study_X/configs/` (private).

## 5) Crossref verification (optional QA before handoff)

```bash
# strict
python blindkit_v1_6.py verify-anatomy-crossref --study-root ./study_X
# tolerant to path moves / benign resaves
python blindkit_v1_6.py verify-anatomy-crossref --study-root ./study_X --relax-paths --dhash-threshold 6
```

## 6) Logs / Registry sanity
```bash
python blindkit_v1_6.py verify-all --study-root ./study_X
```
