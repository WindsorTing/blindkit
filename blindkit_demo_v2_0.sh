#!/usr/bin/env bash
set -euo pipefail

# Demo for BlindKit v2.0
# Creates two repos, registers animals, plans, issues overlays, performs a mock receipt,
# blinds some fake anatomy images, reconciles, and packages an unblinding ZIP.

BLINDER=./study_DEMO_blinder
EXPER=./study_DEMO_experimenter

echo "[1/9] Clean & init roots"
rm -rf "$BLINDER" "$EXPER"
python blindkit_v2_0.py init-dual --blinder-root "$BLINDER" --experimenter-root "$EXPER" --study-id DEMO_STUDY

echo "[2/9] Register animals"
python blindkit_v2_0.py register-animal --blinder-root "$BLINDER" --animal-id RAT001 --sex F --weight 230g
python blindkit_v2_0.py register-animal --blinder-root "$BLINDER" --animal-id RAT002 --sex M --weight 290g

echo "[3/9] Plans"
python blindkit_v2_0.py plan-behavior   --blinder-root "$BLINDER" --date-seed 20250821 --agents A B
python blindkit_v2_0.py plan-physiology --blinder-root "$BLINDER" --date-seed 20250821 --agents A B

echo "[4/9] Overlays (answer prompts quickly)"
printf "RAT001\n1\nPREP-SYR-001\n" | python blindkit_v2_0.py overlay-behavior --blinder-root "$BLINDER"
printf "RAT001\nPREP-SYR-TERM-001\n" | python blindkit_v2_0.py overlay-physiology --blinder-root "$BLINDER"
printf "RAT001\nCAP-L\n"            | python blindkit_v2_0.py overlay-aliquot   --blinder-root "$BLINDER"

echo "[5/9] Fake a receipt on experimenter side"
# NOTE: In real use you would scan a QR; here we simulate manual entry consistent with overlay data.
# You can also copy a photo into $EXPER/media/photos/RAT001/BEHAVIOR/ before running.
python blindkit_v2_0.py inject-scan \
  --experimenter-root "$EXPER" \
  --animal-id RAT001 \
  --stage BEHAVIOR \
  --session 1

echo "[6/9] Create mock anatomy tree (unblinded originals)"
mkdir -p histo_unblinded/RAT001
# Create 2 blank images if PIL is available
python - <<'PY'
from PIL import Image
import os
os.makedirs('histo_unblinded/RAT001', exist_ok=True)
Image.new('RGB',(80,80),'white').save('histo_unblinded/RAT001/INDEX 1-1.tif')
Image.new('RGB',(80,80),'white').save('histo_unblinded/RAT001/INDEX 1-2.tif')
print("Created two mock TIFFs")
PY

echo "[7/9] Blind anatomy"
python blindkit_v2_0.py blind-anatomy \
  --blinder-root "$BLINDER" \
  --experimenter-root "$EXPER" \
  --input-root histo_unblinded \
  --seal

echo "[8/9] Reconcile receipts"
python blindkit_v2_0.py reconcile-usage --blinder-root "$BLINDER" --experimenter-root "$EXPER"

echo "[9/9] Package unblinding bundle"
python blindkit_v2_0.py package-unblinding \
  --blinder-root "$BLINDER" \
  --experimenter-root "$EXPER" \
  --out ./DEMO_unblinding_bundle.zip

echo "Demo complete. Inspect $BLINDER and $EXPER; verify bundle:"
echo "python blindkit_v2_0.py verify-posthoc --bundle ./DEMO_unblinding_bundle.zip"
