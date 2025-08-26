#!/usr/bin/env python3
"""
BlindKit v1.6 – Unified blinding & randomization toolkit with auditable anatomy pipeline

Features
- VIRAL aliquot micro-labels (single per animal)
- BEHAVIOR plan (4 sessions, per-animal balance 2×A/2×B; date-seeded)
- PHYSIOLOGY plan (one per animal; cohort 50/50; date-seeded)
- Label registry (issued/used), dual logs (JSONL hash-chain + CSV), digest report
- Optional QR codes on labels (requires `qrcode`)
- Photo hashing for overlays/injections
- ANATOMY blinder with:
  * Metadata stripping
  * Order preservation via "INDEX M-N" filename parsing
  * Cross-reference manifest per image with SHA-256 + dHash (perceptual)
  * Sealed ZIP archive (+ .sha256) and optional working copy
  * Path-relaxed, perceptual-aware verifier
  * Parent→Child provenance for derived/edited images

Python 3.8+
Optional libs: Pillow (anatomy), qrcode (labels)
"""

import argparse, csv, datetime, hashlib, json, os, pathlib, random, re, shutil, sys, zipfile
from collections import defaultdict
from typing import List, Tuple, Optional

# Optional QR codes
try:
    import qrcode
    HAS_QR = True
except Exception:
    HAS_QR = False

# Optional Pillow (required for anatomy)
try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except Exception:
    HAS_PIL = False

# ---------------- Common helpers ----------------
def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def timestamp():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat()

def ensure_dirs(root: pathlib.Path, subs=("logs","labels","media/photos","configs")):
    for s in subs:
        os.makedirs(root / s, exist_ok=True)

def copy_photo(src_path: str, dst_dir: pathlib.Path, dst_name: str):
    if not src_path: return "", ""
    src = pathlib.Path(src_path)
    if not src.exists():
        print(f"[!] Photo not found, skipping: {src}")
        return "", ""
    os.makedirs(dst_dir, exist_ok=True)
    dst = dst_dir / (dst_name + src.suffix.lower())
    try:
        shutil.copy2(src, dst)
        return str(dst.resolve()), sha256_file(dst)
    except Exception as e:
        print(f"[!] Could not copy photo: {e}")
        return "", ""

# ---------------- Stage rules & checksums ----------------
def stage_rule(stage: str):
    s = stage.upper()
    if s == "VIRAL": return (False, True)
    if s == "BEHAVIOR": return (True, False)
    if s in ("PHYSIOLOGY", "ANATOMY"): return (False, True)
    return (False, True)

def check_lengths(stage: str):
    return (2, 2) if stage.upper() == "VIRAL" else (4, 4)

def compute_checks(dummy: str, animal: str, stage: str):
    base = f"{dummy}{animal}{stage}"
    c1 = hashlib.sha256(base.encode()).hexdigest().upper()
    c2 = hashlib.sha1(base.encode()).hexdigest().upper()
    n1, n2 = check_lengths(stage)
    return c1[:n1], c2[:n2]

_MICRO_ALPH = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
def make_micro_dummy(k: int = 4) -> str:
    import random
    return "".join(random.choice(_MICRO_ALPH) for _ in range(k))

# ---------------- Logging (JSONL+CSV+digest) ----------------
def append_log(study_root: pathlib.Path, event: dict):
    ensure_dirs(study_root)
    # JSONL hash-chain
    jpath = study_root / "logs" / "events.jsonl"
    prev_hash = None
    if jpath.exists():
        with open(jpath, "rb") as f:
            try:
                prev_hash = json.loads(f.readlines()[-1])["event_hash"]
            except Exception:
                prev_hash = None
    event["prev_hash"] = prev_hash
    serial = json.dumps(event, sort_keys=True)
    event["event_hash"] = sha256_str(serial)
    with open(jpath, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

    # CSV (human)
    cpath = study_root / "logs" / "events.csv"
    header = ["ts","event","animal","stage","session","dummy","check1","check2",
              "syringe_id","label_id","photo_path","photo_hash"]
    write_header = not cpath.exists()
    with open(cpath, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if write_header: w.writeheader()
        w.writerow({k: event.get(k, "") for k in header})

    # Digest
    with open(cpath, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: (r.get("animal",""), r.get("stage",""), str(r.get("session","")), r.get("ts","")))
    digest = study_root / "logs" / "digest_report.md"
    with open(digest, "w", encoding="utf-8") as f:
        f.write("# Audit Digest Report\n\n")
        f.write("| Animal | Stage | Session | Dummy | Check1 | Check2 | Syringe/Container | Label | Timestamp | Photo? |\n")
        f.write("|--------|-------|---------|-------|--------|--------|--------------------|-------|-----------|--------|\n")
        for r in rows:
            photo_mark = "Yes" if r.get("photo_hash") else "No"
            f.write(f"| {r.get('animal','')} | {r.get('stage','')} | {r.get('session','')} | {r.get('dummy','')} | "
                    f"{r.get('check1','')} | {r.get('check2','')} | {r.get('syringe_id','')} | {r.get('label_id','')} | "
                    f"{r.get('ts','')} | {photo_mark} |\n")

# ---------------- Registry (JSON+CSV) ----------------
def registry_paths(root: pathlib.Path):
    return (root / "labels" / "registry.json", root / "labels" / "registry.csv")

def load_registry(root: pathlib.Path):
    j, _ = registry_paths(root)
    if j.exists():
        return json.load(open(j, encoding="utf-8"))
    return {"entries": []}

def save_registry(root: pathlib.Path, reg: dict):
    j, c = registry_paths(root)
    with open(j, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2)
    header = ["ts_overlay","animal","stage","session","dummy","check1","check2",
              "syringe_id","label_id","status","ts_inject","inject_photo_hash"]
    with open(c, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header); w.writeheader()
        for e in reg["entries"]:
            w.writerow({
                "ts_overlay": e.get("ts_overlay",""),
                "animal": e.get("animal",""),
                "stage": e.get("stage",""),
                "session": e.get("session",""),
                "dummy": e.get("dummy",""),
                "check1": e.get("check1",""),
                "check2": e.get("check2",""),
                "syringe_id": e.get("syringe_id",""),
                "label_id": e.get("label_id",""),
                "status": e.get("status",""),
                "ts_inject": e.get("ts_inject",""),
                "inject_photo_hash": e.get("inject_photo_hash",""),
            })

def any_issued_for_stage(reg: dict, animal: str, stage: str, session=None):
    res = [e for e in reg["entries"] if e["animal"] == animal and e["stage"] == stage]
    if session is not None:
        res = [e for e in res if e.get("session") == session]
    return res

def first_issued_unused_match(reg: dict, animal: str, stage: str, dummy: str, label_id: str, session=None):
    for e in reg["entries"]:
        if (e["animal"] == animal and e["stage"] == stage and
            e["dummy"] == dummy and e["label_id"] == label_id and
            e["status"] == "issued" and (session is None or e.get("session") == session)):
            return e
    return None

# ---------------- Animals & seeds ----------------
def animals_list(root: pathlib.Path):
    ans = []
    p = root / "animals.jsonl"
    if not p.exists(): return ans
    with open(p, encoding="utf-8") as f:
        for line in f:
            try: ans.append(json.loads(line)["animal"])
            except Exception: pass
    return ans

def seeded_rng(date_seed: str, animal: str):
    base = int(date_seed)
    ah = int(sha256_str(animal)[:8], 16)
    return random.Random(base ^ ah)

# ---------------- Behavior plan ----------------
def plan_behavior(root: pathlib.Path, date_seed: str, A: str, B: str):
    out_json = root / "configs" / "behavior_plan.json"
    out_csv  = root / "configs" / "behavior_plan.csv"
    ans = animals_list(root)
    if not ans: raise SystemExit("[!] No animals registered.")
    plan = {"date_seed": date_seed, "agents": [A, B], "sessions": 4, "assignments": {}}
    for a in sorted(ans):
        seq = [A, A, B, B]
        seeded_rng(date_seed, a).shuffle(seq)
        plan["assignments"][a] = [{"session": i+1, "agent": seq[i]} for i in range(4)]
    with open(out_json, "w", encoding="utf-8") as f: json.dump(plan, f, indent=2)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["animal","session","agent"])
        for a in sorted(plan["assignments"].keys()):
            for r in plan["assignments"][a]:
                w.writerow([a, r["session"], r["agent"]])
    return out_json, out_csv

def verify_behavior(root: pathlib.Path):
    p = root / "configs" / "behavior_plan.json"
    if not p.exists(): print("[!] behavior_plan.json not found."); return False
    disk = json.load(open(p, encoding="utf-8"))
    seed = disk["date_seed"]; A, B = disk["agents"]
    ans = animals_list(root)
    ok = True; totA = totB = 0
    for a in sorted(ans):
        seq = [A, A, B, B]; seeded_rng(seed, a).shuffle(seq)
        recomputed = [{"session": i+1, "agent": seq[i]} for i in range(4)]
        if recomputed != disk["assignments"].get(a, []):
            print(f"[!] Mismatch for {a}"); ok = False
        for x in recomputed:
            if x["agent"] == A: totA += 1
            else: totB += 1
    if totA != totB:
        print(f"[!] Balance violation: {A}={totA}, {B}={totB}"); ok = False
    if ok: print(f"[✓] Behavior plan verified. Balanced: {A}={totA}, {B}={totB}")
    return ok

# ---------------- Physiology plan ----------------
def plan_physiology(root: pathlib.Path, date_seed: str, A: str, B: str, allow_imbalance=False):
    out_json = root / "configs" / "physiology_plan.json"
    out_csv  = root / "configs" / "physiology_plan.csv"
    ans = animals_list(root)
    if not ans: raise SystemExit("[!] No animals registered.")
    rng = random.Random(int(date_seed))
    order = sorted(ans); rng.shuffle(order)
    n = len(order)
    if n % 2 == 1 and not allow_imbalance:
        raise SystemExit(f"[!] Odd N={n}. Add/remove one or pass --allow-imbalance.")
    half = n // 2
    assign = {a: (A if i < half else B) for i, a in enumerate(order)}
    plan = {"date_seed": date_seed, "agents": [A, B], "assignments": assign}
    with open(out_json, "w", encoding="utf-8") as f: json.dump(plan, f, indent=2)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["animal","agent","rank"])
        for i, a in enumerate(order): w.writerow([a, assign[a], i+1])
    return out_json, out_csv

def verify_physiology(root: pathlib.Path):
    p = root / "configs" / "physiology_plan.json"
    if not p.exists(): print("[!] physiology_plan.json not found."); return False
    disk = json.load(open(p, encoding="utf-8"))
    seed = disk["date_seed"]; A, B = disk["agents"]
    ans = animals_list(root)
    rng = random.Random(int(seed)); order = sorted(ans); rng.shuffle(order)
    half = len(order)//2
    recompute = {a: (A if i < half else B) for i, a in enumerate(order)}
    ok = recompute == disk["assignments"]
    cntA = sum(1 for a in ans if recompute.get(a)==A)
    cntB = sum(1 for a in ans if recompute.get(a)==B)
    if len(order) % 2 == 0 and cntA != cntB: ok = False
    if ok:
        msg = f"[✓] Physiology plan verified. Counts: {A}={cntA}, {B}={cntB}"
        if len(order)%2==1: msg += " (odd N → ±1 imbalance)"
        print(msg)
    else:
        print("[!] Physiology plan mismatch or imbalance.")
    return ok

# ---------------- Perceptual hashing (dHash) ----------------
def dhash_image(path: pathlib.Path, size: int = 8) -> str:
    """
    Difference hash: returns 16-char hex (64-bit) string.
    Robust to small brightness/contrast changes or re-saves.
    """
    if not HAS_PIL:
        raise RuntimeError("Pillow is required for perceptual hashing.")
    with Image.open(path) as im:
        im = im.convert("L").resize((size + 1, size), Image.BILINEAR)
        diff_bits = []
        for y in range(size):
            for x in range(size):
                left = im.getpixel((x, y))
                right = im.getpixel((x + 1, y))
                diff_bits.append(1 if right > left else 0)
        val = 0
        for b in diff_bits:
            val = (val << 1) | b
        return f"{val:016x}"

def dhash_hamming(a_hex: str, b_hex: str) -> int:
    return bin(int(a_hex, 16) ^ int(b_hex, 16)).count("1")

# ---------------- Anatomy blinder ----------------
IMAGE_EXTS = {".jpg",".jpeg",".png",".tif",".tiff"}

def is_image(p: pathlib.Path) -> bool:
    return p.is_file() and p.suffix.lower() in IMAGE_EXTS

_PATTERNS = [
    re.compile(r"(?i)index[\s_]*([0-9]+)[\s_-]*[-–][\s_-]*([0-9]+)"),
    re.compile(r"(?i)index[\s_]*([0-9]+)[\s_-]+([0-9]+)"),
    re.compile(r"(?i)idx[\s_]*([0-9]+)[\s_-]*[-–][\s_-]*([0-9]+)"),
]
def parse_index_pair(name: str) -> Optional[Tuple[int,int]]:
    for pat in _PATTERNS:
        m = pat.search(name)
        if m:
            try:
                return int(m.group(1)), int(m.group(2))
            except Exception:
                pass
    return None

def strip_metadata_copy(src: pathlib.Path, dst: pathlib.Path):
    if not HAS_PIL:
        raise RuntimeError("Pillow not installed; run: pip install pillow")
    with Image.open(src) as im:
        # normalize orientation & mode
        im = ImageOps.exif_transpose(im)
        if im.mode in ("P","PA"):
            im = im.convert("RGBA" if im.mode=="PA" else "RGB")
        fmt = (dst.suffix.lower().replace(".","") or im.format or "PNG").upper()
        params = {}
        if fmt == "JPEG":
            params.update({"quality": 95, "optimize": True})
        elif fmt in ("TIFF","TIF"):
            params.update({"compression": "tiff_deflate"})
        im.save(dst, format=fmt, **params)

def collect_animals(input_root: pathlib.Path) -> List[pathlib.Path]:
    return [p for p in sorted(input_root.iterdir()) if p.is_dir()]

def collect_images(animal_dir: pathlib.Path) -> List[pathlib.Path]:
    return [p for p in sorted(animal_dir.rglob("*")) if is_image(p)]

def blind_animal_folder(animal_dir: pathlib.Path, out_root: pathlib.Path, blinded_id: str, strict_index=True):
    imgs = collect_images(animal_dir)
    if not imgs: return 0, []
    parsed, missing = [], []
    for img in imgs:
        pair = parse_index_pair(img.name)
        (parsed if pair else missing).append((pair, img))
    if missing and strict_index:
        print(f"[!] {animal_dir.name}: {len(missing)} files missing 'INDEX M-N'. Example: {missing[0][1].name}")
        raise SystemExit("Aborting due to missing INDEX labels (strict mode).")
    if missing and not strict_index:
        baseM = 9999
        for i, (_, img) in enumerate(missing, 1):
            parsed.append(((baseM, i), img))
    else:
        parsed = [(p, img) for (p,img) in parsed]

    parsed = [((p[0], p[1]), img) for (p, img) in parsed]
    parsed.sort(key=lambda x: (x[0][0], x[0][1], x[1].name))

    out_dir = out_root / blinded_id
    os.makedirs(out_dir, exist_ok=True)

    rows = []
    for (M,N), src in parsed:
        original_sha256 = sha256_file(src)
        original_dhash  = dhash_image(src)

        dst = out_dir / f"IDX_{M:03d}-{N:03d}{src.suffix.lower()}"
        strip_metadata_copy(src, dst)

        blinded_sha256 = sha256_file(dst)
        blinded_dhash  = dhash_image(dst)

        rows.append({
            "M": M,
            "N": N,
            "original_relpath": str(src),
            "original_sha256": original_sha256,
            "original_dhash": original_dhash,
            "blinded_filename": dst.name,
            "blinded_relpath": str(dst.relative_to(out_root)),
            "blinded_sha256": blinded_sha256,
            "blinded_dhash": blinded_dhash,
        })

    with open(out_dir / "series.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["M","N","blinded_filename","blinded_sha256","blinded_dhash","original_sha256","original_dhash","original_relpath"]
        )
        w.writeheader()
        for r in rows:
            w.writerow({
                "M": r["M"], "N": r["N"],
                "blinded_filename": r["blinded_filename"],
                "blinded_sha256": r["blinded_sha256"],
                "blinded_dhash": r["blinded_dhash"],
                "original_sha256": r["original_sha256"],
                "original_dhash": r["original_dhash"],
                "original_relpath": r["original_relpath"],
            })

    with open(out_dir / "README.txt", "w", encoding="utf-8") as f:
        f.write("Blinded histology images. Order encoded as IDX_MMM-NNN.ext.\n")
        f.write("series.csv records original+blinded SHA-256 and dHash signatures.\n")

    return len(rows), rows

def _index_hashes(root: pathlib.Path) -> dict:
    idx = defaultdict(list)
    for p in root.rglob("*"):
        if p.is_file():
            try:
                idx[sha256_file(p)].append(p)
            except Exception:
                pass
    return idx

def _seal_zip(output_root: pathlib.Path, study_root: pathlib.Path, extra_paths: list) -> pathlib.Path:
    archives = study_root / "archives"; os.makedirs(archives, exist_ok=True)
    stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    zip_path = archives / f"anatomy_blinded_{stamp}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in output_root.rglob("*"):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(output_root.parent)))
        for p in extra_paths:
            p = pathlib.Path(p)
            if p.exists():
                zf.write(p, arcname=f"manifests/{p.name}")
    with open(str(zip_path) + ".sha256", "w", encoding="utf-8") as f:
        f.write(sha256_file(zip_path))
    return zip_path

def _copytree(src: pathlib.Path, dst: pathlib.Path):
    if not dst.exists(): os.makedirs(dst, exist_ok=True)
    for p in src.rglob("*"):
        rel = p.relative_to(src)
        target = dst / rel
        if p.is_dir():
            os.makedirs(target, exist_ok=True)
        else:
            shutil.copy2(p, target)

# ---------------- Commands ----------------
def cmd_init_study(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    with open(root / "study_meta.json", "w", encoding="utf-8") as f:
        json.dump({"study_id": a.study_id, "created": timestamp()}, f, indent=2)
    save_registry(root, {"entries": []})
    print("[+] Initialized study:", a.study_id)

def cmd_register_animal(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    with open(root / "animals.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"animal": a.animal_id, "sex": a.sex, "weight": a.weight, "ts": timestamp()}) + "\n")
    print("[+] Registered animal", a.animal_id)

def cmd_randomize(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    ans = animals_list(root)
    if not ans: print("[!] No animals registered."); return
    random.shuffle(ans); groups = a.groups
    if not groups: print("[!] Provide --groups"); return
    mapping = {x: groups[i % len(groups)] for i, x in enumerate(ans)}
    out = pathlib.Path(a.mapping_out); os.makedirs(out.parent, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f: json.dump(mapping, f, indent=2)
    with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["animal","group"]); [w.writerow([x, mapping[x]]) for x in ans]
    print("[+] Randomization complete.\n    JSON →", out, "\n    CSV  →", out.with_suffix(".csv"))

def cmd_randomize_viral(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    ans = animals_list(root)
    if not ans: print("[!] No animals registered."); return
    if len(a.choices) != 2: print("[!] Provide exactly two choices, e.g. --choices A B"); return
    random.shuffle(ans); mapping = {x: a.choices[i%2] for i, x in enumerate(ans)}
    out = pathlib.Path(a.mapping_out); os.makedirs(out.parent, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f: json.dump(mapping, f, indent=2)
    with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["animal","viral_choice"]); [w.writerow([x, mapping[x]]) for x in ans]
    print("[+] Viral randomization complete.\n    JSON →", out, "\n    CSV  →", out.with_suffix(".csv"))

def cmd_plan_behavior(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    j,c = plan_behavior(root, a.date_seed, a.agents[0], a.agents[1])
    print("[+] Behavior plan written.\n    JSON →", j, "\n    CSV  →", c)

def cmd_verify_behavior(a):
    if not verify_behavior(pathlib.Path(a.study_root)): sys.exit(1)

def cmd_plan_physiology(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    j,c = plan_physiology(root, a.date_seed, a.agents[0], a.agents[1], allow_imbalance=a.allow_imbalance)
    print("[+] Physiology plan written.\n    JSON →", j, "\n    CSV  →", c)
    if a.allow_imbalance: print("[i] allow_imbalance=True; counts may differ by 1 for odd N.")

def cmd_verify_physiology(a):
    if not verify_physiology(pathlib.Path(a.study_root)): sys.exit(1)

def cmd_overlay_behavior(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    p = root / "configs" / "behavior_plan.json"
    if not p.exists(): print("[!] behavior_plan.json not found. Run plan-behavior first."); return
    plan = json.load(open(p, encoding="utf-8"))
    animal = input("Animal ID: ").strip()
    try:
        session = int(input("Behavior SESSION (1-4): ").strip())
    except Exception:
        print("[!] Session must be 1..4."); return
    if session not in (1,2,3,4): print("[!] Session must be 1..4."); return
    if not any(r["session"]==session for r in plan["assignments"].get(animal, [])):
        print(f"[!] No planned agent for {animal} session {session}."); return
    syringe_id = input("Base SYRINGE_ID (from preparer's sticker): ").strip()
    stage = "BEHAVIOR"
    dummy = f"BEH{session}-{os.urandom(2).hex().upper()}"
    check1, check2 = compute_checks(dummy, animal, stage)
    label_id = os.urandom(3).hex().upper(); ts = timestamp()
    lbl_dir = root / "labels"; fname = f"{animal}_BEH{session}_{label_id}"
    with open(lbl_dir / (fname + ".txt"), "w", encoding="utf-8") as f:
        f.write(f"ANIMAL:  {animal}\nSTAGE:   BEHAVIOR\nSESSION: {session}\nDUMMY:   {dummy}\nCHECK1:  {check1}\nCHECK2:  {check2}\nSYRINGE: {syringe_id}\nLABEL:   {label_id}\nTS:      {ts}\n")
    if HAS_QR:
        qrcode.make(json.dumps({"animal":animal,"stage":"BEHAVIOR","session":session,"dummy":dummy,"check1":check1,"check2":check2,"syringe_id":syringe_id,"label_id":label_id,"ts":ts}, sort_keys=True)).save(lbl_dir / (fname + ".png"))
    photo_src = input("Optional post-overlay photo path (Enter to skip): ").strip()
    photo_path, photo_hash = copy_photo(photo_src, root / "media" / "photos" / animal / "BEHAVIOR", f"overlay_s{session}_{label_id}")
    reg = load_registry(root)
    reg["entries"].append({"ts_overlay": ts, "animal": animal, "stage": "BEHAVIOR", "session": session,
                           "dummy": dummy, "check1": check1, "check2": check2,
                           "syringe_id": syringe_id, "label_id": label_id,
                           "status": "issued", "ts_inject": "", "inject_photo_hash": ""})
    save_registry(root, reg)
    append_log(root, {"ts": ts, "event":"overlay","animal":animal,"stage":"BEHAVIOR","session":session,"dummy":dummy,"check1":check1,"check2":check2,"syringe_id":syringe_id,"label_id":label_id,"photo_path":photo_path,"photo_hash":photo_hash})
    print(f"[✓] BEHAVIOR overlay complete (session {session}).")

def cmd_overlay_aliquot(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    stage="VIRAL"; animal = input("Animal ID: ").strip()
    container_id = input("Base ALIQUOT_ID (cap/side code): ").strip()
    reg = load_registry(root)
    if any_issued_for_stage(reg, animal, stage):
        print(f"[!] VIRAL already issued for {animal}."); return
    dummy = f"VIR-{make_micro_dummy(4)}"
    check1, check2 = compute_checks(dummy, animal, stage)
    label_id = os.urandom(2).hex().upper(); ts = timestamp()
    lbl_dir = root / "labels"; fname = f"{animal}_{stage}_{label_id}"
    with open(lbl_dir / (fname + ".micro.txt"), "w", encoding="utf-8") as f:
        f.write(f"D:{dummy}\nC1:{check1} C2:{check2}\nCID:{container_id}\nL:{label_id}\n")
    if HAS_QR:
        qrcode.make(json.dumps({"animal":animal,"stage":stage,"dummy":dummy,"check1":check1,"check2":check2,"syringe_id":container_id,"label_id":label_id,"ts":ts}, sort_keys=True)).save(lbl_dir / (fname + ".png"))
    photo_src = input("Optional post-overlay photo path (Enter to skip): ").strip()
    photo_path, photo_hash = copy_photo(photo_src, root / "media" / "photos" / animal / stage, f"overlay_{label_id}")
    reg["entries"].append({"ts_overlay": ts, "animal": animal, "stage": stage, "session": None,
                           "dummy": dummy, "check1": check1, "check2": check2,
                           "syringe_id": container_id, "label_id": label_id,
                           "status": "issued", "ts_inject": "", "inject_photo_hash": ""})
    save_registry(root, reg)
    append_log(root, {"ts": ts, "event":"overlay","animal":animal,"stage":stage,"session":None,"dummy":dummy,"check1":check1,"check2":check2,"syringe_id":container_id,"label_id":label_id,"photo_path":photo_path,"photo_hash":photo_hash})
    print("[✓] VIRAL overlay complete.")

def cmd_overlay_physiology(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    p = root / "configs" / "physiology_plan.json"
    if not p.exists(): print("[!] physiology_plan.json not found. Run plan-physiology first."); return
    plan = json.load(open(p, encoding="utf-8"))
    animal = input("Animal ID: ").strip()
    if any_issued_for_stage(load_registry(root), animal, "PHYSIOLOGY"):
        print(f"[!] PHYSIOLOGY already issued for {animal}."); return
    agent_planned = plan["assignments"].get(animal)
    if not agent_planned: print(f"[!] {animal} not in physiology plan."); return
    print(f"[Blinder-only] Planned agent for {animal} → {agent_planned}")
    syringe_id = input("Base SYRINGE_ID (prepared with planned agent): ").strip()
    stage="PHYSIOLOGY"; dummy = stage[:3] + "-" + os.urandom(2).hex().upper()
    check1, check2 = compute_checks(dummy, animal, stage)
    label_id = os.urandom(3).hex().upper(); ts = timestamp()
    lbl_dir = root / "labels"; fname = f"{animal}_{stage}_{label_id}"
    with open(lbl_dir / (fname + ".txt"), "w", encoding="utf-8") as f:
        f.write(f"ANIMAL:  {animal}\nSTAGE:   {stage}\nDUMMY:   {dummy}\nCHECK1:  {check1}\nCHECK2:  {check2}\nSYRINGE: {syringe_id}\nLABEL:   {label_id}\nTS:      {ts}\n")
    if HAS_QR:
        qrcode.make(json.dumps({"animal":animal,"stage":stage,"dummy":dummy,"check1":check1,"check2":check2,"syringe_id":syringe_id,"label_id":label_id,"ts":ts}, sort_keys=True)).save(lbl_dir / (fname + ".png"))
    photo_src = input("Optional post-overlay photo path (Enter to skip): ").strip()
    photo_path, photo_hash = copy_photo(photo_src, root / "media" / "photos" / animal / stage, f"overlay_{label_id}")
    reg = load_registry(root)
    reg["entries"].append({"ts_overlay": ts, "animal": animal, "stage": stage, "session": None,
                           "dummy": dummy, "check1": check1, "check2": check2,
                           "syringe_id": syringe_id, "label_id": label_id,
                           "status": "issued", "ts_inject": "", "inject_photo_hash": ""})
    save_registry(root, reg)
    append_log(root, {"ts": ts, "event":"overlay","animal":animal,"stage":stage,"session":None,"dummy":dummy,"check1":check1,"check2":check2,"syringe_id":syringe_id,"label_id":label_id,"photo_path":photo_path,"photo_hash":photo_hash})
    print("[✓] PHYSIOLOGY overlay complete.")

def cmd_inject_scan(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    animal = a.animal_id; stage = a.stage.upper()
    if stage not in ("VIRAL","BEHAVIOR","PHYSIOLOGY","ANATOMY"):
        print("[!] Invalid stage."); return
    session = None
    if stage == "BEHAVIOR":
        if a.session is None: print("[!] --session required for BEHAVIOR."); return
        try: session = int(a.session)
        except Exception: print("[!] --session must be 1..4."); return
        if session not in (1,2,3,4): print("[!] --session must be 1..4."); return

    # Parse payload or manual
    if a.qr_payload:
        try:
            rec = json.loads(a.qr_payload)
            dummy = rec["dummy"]; check1 = rec["check1"]; check2 = rec["check2"]
            container = rec["syringe_id"]; label_id = rec["label_id"]
            if rec.get("animal") and rec["animal"] != animal: print("[!] Animal mismatch."); return
            if rec.get("stage") and rec["stage"].upper() != stage: print("[!] Stage mismatch."); return
            if stage=="BEHAVIOR" and rec.get("session") and int(rec["session"]) != session:
                print("[!] Session mismatch."); return
        except Exception as e:
            print("[!] Invalid QR payload:", e); return
    else:
        dummy = input("Dummy (exact): ").strip()
        c1len, c2len = check_lengths(stage)
        check1 = input(f"Check1 ({c1len} chars): ").strip().upper()
        check2 = input(f"Check2 ({c2len} chars): ").strip().upper()
        container = input("Syringe/Container ID: ").strip()
        label_id = input("Label ID: ").strip()

    exp_c1, exp_c2 = compute_checks(dummy, animal, stage)
    if check1 != exp_c1 or check2 != exp_c2:
        print("[!] CHECK mismatch — injection blocked."); return

    reg = load_registry(root)
    entry = first_issued_unused_match(reg, animal, stage, dummy, label_id, session=session)
    if not entry:
        issued = [e for e in any_issued_for_stage(reg, animal, stage, session=session) if e["status"]=="issued"]
        print("[!] No matching ISSUED label found. Injection blocked.")
        if issued:
            print("    Issued & unused for this context:")
            for e in issued:
                s = f" s{e.get('session')}" if e.get("session") else ""
                print(f"     - LABEL_ID={e['label_id']} DUMMY={e['dummy']} CID={e['syringe_id']}{s}")
        return

    inj_photo_path, inj_photo_hash = "", ""
    if a.photo:
        inj_photo_path, inj_photo_hash = copy_photo(a.photo, root / "media" / "photos" / animal / stage, f"inject_{label_id}")

    entry["status"] = "used"; entry["ts_inject"] = timestamp(); entry["inject_photo_hash"] = inj_photo_hash
    save_registry(root, reg)
    append_log(root, {"ts": entry["ts_inject"], "event":"inject","animal":animal,"stage":stage,"session":session,
                      "dummy":dummy,"check1":check1,"check2":check2,"syringe_id":container,"label_id":label_id,
                      "photo_path":inj_photo_path,"photo_hash":inj_photo_hash})
    print("[✓] Injection verified, matched to ISSUED label, and marked USED.")

def cmd_unblind(a):
    root = pathlib.Path(a.study_root); ensure_dirs(root)
    mapping = json.load(open(a.mapping_path, encoding="utf-8"))
    out_json = root / "unblinded_mapping.json"; out_csv = root / "unblinded_mapping.csv"
    with open(out_json, "w", encoding="utf-8") as f: json.dump(mapping, f, indent=2)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["animal","group"]); [w.writerow([k, v]) for k, v in mapping.items()]
    print("[+] Unblinded mapping saved.\n    JSON →", out_json, "\n    CSV  →", out_csv)

def cmd_verify_all(a):
    root = pathlib.Path(a.study_root)
    jsonl = root / "logs" / "events.jsonl"
    if not jsonl.exists(): print("[!] No events log to verify."); return
    prev = None
    with open(jsonl, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            serial = json.dumps({k: rec[k] for k in rec if k != "event_hash"}, sort_keys=True)
            if sha256_str(serial) != rec["event_hash"]: print("[!] Tamper detected."); return
            if rec["prev_hash"] != prev: print("[!] Chain break."); return
            prev = rec["event_hash"]
    print("[✓] Logs verified, chain intact.")
    # terminal-stage sanity
    reg = load_registry(root); errs=[]
    for st in ("VIRAL","PHYSIOLOGY","ANATOMY"):
        by = {}
        for e in reg["entries"]:
            if e["stage"]!=st: continue
            by.setdefault(e["animal"], 0); by[e["animal"]] += 1
        for a_id, n in by.items():
            if n>1: errs.append(f"{st} multiple labels for {a_id} (n={n})")
    if errs: print("[!] Registry rule violations:\n - " + "\n - ".join(errs))
    else:   print("    Registry rules OK for terminal stages.")

# -------- Anatomy crossref & provenance --------
def cmd_blind_anatomy(a):
    if not HAS_PIL:
        print("[!] Pillow is required. Install with: pip install pillow")
        sys.exit(1)
    study_root = pathlib.Path(a.study_root).resolve()
    input_root = pathlib.Path(a.input_root).resolve()
    output_root = pathlib.Path(a.output_root).resolve()
    if not input_root.exists() or not input_root.is_dir():
        raise SystemExit("[!] --input-root must be a directory with subfolders (one per animal).")
    if output_root.exists():
        if any(output_root.iterdir()) and not a.force:
            raise SystemExit("[!] --output-root exists and is not empty. Use --force to proceed.")
    else:
        os.makedirs(output_root, exist_ok=True)

    ensure_dirs(study_root)
    animal_dirs = collect_animals(input_root)
    if not animal_dirs:
        raise SystemExit("[!] No animal subfolders found in --input-root.")

    today = datetime.datetime.utcnow().strftime("%Y%m%d")
    used_ids=set(); blinded_map={}; counts={}
    for adir in animal_dirs:
        salt="ANATOMY_V1"
        h = sha256_str(f"{adir.name}|{today}|{salt}")
        suf=6; bid="ANA-"+h[:suf].upper()
        while bid in used_ids:
            suf+=1; bid="ANA-"+h[:suf].upper()
        used_ids.add(bid); blinded_map[adir.name]=bid

    total = 0
    all_rows = []  # crossref rows
    for adir in animal_dirs:
        bid = blinded_map[adir.name]
        n, rows = blind_animal_folder(adir, output_root, bid, strict_index=not a.allow_missing_index)
        counts[adir.name]=n; total += n
        for r in rows:
            all_rows.append({
                "animal": adir.name,
                "blinded_id": bid,
                "M": r["M"], "N": r["N"],
                "original_relpath": r["original_relpath"],
                "original_sha256": r["original_sha256"],
                "original_dhash": r["original_dhash"],
                "blinded_relpath": r["blinded_relpath"],
                "blinded_sha256": r["blinded_sha256"],
                "blinded_dhash": r["blinded_dhash"],
            })
        append_log(study_root, {
            "ts": timestamp(),
            "event": "anatomy_blind_copy",
            "animal": adir.name,
            "stage": "ANATOMY",
            "session": None,
            "dummy": "",
            "check1": "",
            "check2": "",
            "syringe_id": "",
            "label_id": "",
            "photo_path": "",
            "photo_hash": "",
        })

    cfg = study_root / "configs"; os.makedirs(cfg, exist_ok=True)

    with open(cfg / "anatomy_blind_map.json", "w", encoding="utf-8") as f:
        json.dump({"created": timestamp(), "input_root": str(input_root), "output_root": str(output_root),
                   "mapping": blinded_map, "counts": counts}, f, indent=2)
    with open(cfg / "anatomy_blind_map.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["animal","blinded_id","images"])
        for a_id in sorted(blinded_map.keys()):
            w.writerow([a_id, blinded_map[a_id], counts.get(a_id,0)])

    cross_csv = cfg / "anatomy_crossref.csv"
    cross_json = cfg / "anatomy_crossref.json"
    with open(cross_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["animal","blinded_id","M","N","original_relpath","original_sha256","original_dhash","blinded_relpath","blinded_sha256","blinded_dhash"]
        )
        w.writeheader()
        for row in all_rows: w.writerow(row)
    with open(cross_json, "w", encoding="utf-8") as f:
        json.dump({"created": timestamp(),"input_root": str(input_root),"output_root": str(output_root),"files": all_rows}, f, indent=2)

    with open(cross_csv.with_suffix(".csv.sha256"), "w", encoding="utf-8") as f:
        f.write(sha256_file(cross_csv))

    if a.seal:
        zip_path = _seal_zip(output_root, study_root, [cfg / "anatomy_blind_map.json", cross_csv, cross_json, cross_csv.with_suffix(".csv.sha256")])
        print("[✓] Sealed archive:", zip_path, " (sha256 in .sha256)")

    if a.working_root:
        wr = pathlib.Path(a.working_root).resolve()
        if wr.exists() and any(wr.iterdir()) and not a.force:
            raise SystemExit("[!] --working-root exists and is not empty. Use --force to proceed.")
        _copytree(output_root, wr)
        print("[✓] Working copy created at:", wr)

    print(f"[✓] Anatomy blinding complete. Animals: {len(animal_dirs)}, images: {total}")
    print("    Blinded output →", output_root)
    print("    Mapping →", cfg / "anatomy_blind_map.json", "and", cfg / "anatomy_blind_map.csv")
    print("    Crossref →", cross_csv, "and", cross_json)
    if a.allow_missing_index:
        print("[i] --allow-missing-index used; non‑indexed files sequenced after indexed.")

def cmd_verify_anatomy_crossref(a):
    study_root = pathlib.Path(a.study_root).resolve()
    cfg = study_root / "configs" / "anatomy_crossref.json"
    if not cfg.exists():
        raise SystemExit("[!] configs/anatomy_crossref.json not found. Run blind-anatomy first.")
    manifest = json.load(open(cfg, encoding="utf-8"))

    input_root  = pathlib.Path(a.input_root).resolve()  if a.input_root  else pathlib.Path(manifest["input_root"]).resolve()
    output_root = pathlib.Path(a.output_root).resolve() if a.output_root else pathlib.Path(manifest["output_root"]).resolve()

    csv_path = study_root / "configs" / "anatomy_crossref.csv"
    sig_path = study_root / "configs" / "anatomy_crossref.csv.sha256"
    if csv_path.exists() and sig_path.exists():
        if sha256_file(csv_path) != open(sig_path, encoding="utf-8").read().strip():
            print("[!] Crossref CSV hash mismatch (file changed).")

    in_idx = out_idx = None
    if a.relax_paths:
        print("[i] Building relaxed SHA-256 indices (may take time)...")
        in_idx  = _index_hashes(input_root)
        out_idx = _index_hashes(output_root)

    errors = 0; warnings = 0
    thr = int(a.dhash_threshold)

    for rec in manifest["files"]:
        # Source
        src_path = input_root / rec["animal"] / pathlib.Path(rec["original_relpath"]).name
        if not src_path.exists():
            literal = pathlib.Path(rec["original_relpath"])
            if literal.exists():
                src_path = literal
            elif a.relax_paths and in_idx:
                hits = in_idx.get(rec["original_sha256"], [])
                if hits: src_path = hits[0]
        if src_path.exists():
            h_src = sha256_file(src_path)
            if h_src != rec["original_sha256"]:
                try:
                    d_now = dhash_image(src_path)
                    d0 = rec.get("original_dhash","")
                    if d0 and dhash_hamming(d_now, d0) <= thr:
                        warnings += 1
                        print(f"[~] Source SHA mismatch but dHash within {thr}b: {src_path}")
                    else:
                        errors += 1; print(f"[!] Source hash mismatch: {src_path}")
                except Exception:
                    errors += 1; print(f"[!] Source changed & cannot dHash: {src_path}")
        else:
            errors += 1; print(f"[!] Source missing: {src_path}")

        # Blinded
        dst_path = output_root / rec["blinded_relpath"]
        if not dst_path.exists() and a.relax_paths and out_idx:
            hits = out_idx.get(rec["blinded_sha256"], [])
            if hits: dst_path = hits[0]
        if dst_path.exists():
            h_dst = sha256_file(dst_path)
            if h_dst != rec["blinded_sha256"]:
                try:
                    d_now = dhash_image(dst_path)
                    d0 = rec.get("blinded_dhash","")
                    if d0 and dhash_hamming(d_now, d0) <= thr:
                        warnings += 1
                        print(f"[~] Blinded SHA mismatch but dHash within {thr}b: {dst_path}")
                    else:
                        errors += 1; print(f"[!] Blinded hash mismatch: {dst_path}")
                except Exception:
                    errors += 1; print(f"[!] Blinded changed & cannot dHash: {dst_path}")
        else:
            errors += 1; print(f"[!] Blinded file missing: {dst_path}")

    if errors == 0:
        ok_msg = "[✓] Crossref verification passed"
        if warnings: ok_msg += f" with {warnings} warning(s) (visual match but SHA changed)."
        print(ok_msg)
    else:
        print(f"[!] Crossref verification found {errors} error(s) and {warnings} warning(s).")

def _append_derivative_rows(study_root: pathlib.Path, rows: list):
    cfg = study_root / "configs"; os.makedirs(cfg, exist_ok=True)
    csv_path = cfg / "anatomy_derivatives.csv"
    jsonl   = cfg / "anatomy_derivatives.jsonl"
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "ts","parent_path","parent_sha256","parent_dhash",
            "child_path","child_sha256","child_dhash","note","dhash_distance"
        ])
        if write_header: w.writeheader()
        for r in rows: w.writerow(r)
    with open(jsonl, "a", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r) + "\n")

def cmd_record_derivative(a):
    study_root = pathlib.Path(a.study_root).resolve()
    parent = pathlib.Path(a.parent).resolve()
    child  = pathlib.Path(a.child).resolve()
    if not parent.exists() or not child.exists():
        raise SystemExit("[!] Parent and child paths must exist.")
    p_sha, p_dh = sha256_file(parent), dhash_image(parent)
    c_sha, c_dh = sha256_file(child),  dhash_image(child)
    dist = dhash_hamming(p_dh, c_dh)
    row = {
        "ts": timestamp(),
        "parent_path": str(parent),
        "parent_sha256": p_sha,
        "parent_dhash": p_dh,
        "child_path": str(child),
        "child_sha256": c_sha,
        "child_dhash": c_dh,
        "note": a.note or "",
        "dhash_distance": dist,
    }
    _append_derivative_rows(study_root, [row])
    append_log(study_root, {
        "ts": timestamp(),
        "event": "anatomy_derivative",
        "animal": "", "stage": "ANATOMY",
        "session": None, "dummy": "", "check1": "", "check2": "",
        "syringe_id": "", "label_id": "",
        "photo_path": "", "photo_hash": ""
    })
    verdict = "VISUALLY MATCHING" if dist <= a.dhash_threshold else "VISUALLY DIFFERENT"
    print(f"[✓] Recorded derivative. dHash distance={dist} → {verdict}")

# ---------------- CLI ----------------
def main():
    ap = argparse.ArgumentParser(prog="blindkit_v1_6", description="Blinding & randomization toolkit with auditable anatomy (v1.6)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("init-study");            p.add_argument("--study-root", required=True); p.add_argument("--study-id", required=True); p.set_defaults(func=cmd_init_study)
    p = sp.add_parser("register-animal");       p.add_argument("--study-root", required=True); p.add_argument("--animal-id", required=True); p.add_argument("--sex", required=True); p.add_argument("--weight", required=True); p.set_defaults(func=cmd_register_animal)

    p = sp.add_parser("randomize");             p.add_argument("--study-root", required=True); p.add_argument("--groups", nargs="+", required=True); p.add_argument("--mapping-out", required=True); p.set_defaults(func=cmd_randomize)
    p = sp.add_parser("randomize-viral");       p.add_argument("--study-root", required=True); p.add_argument("--choices", nargs=2, required=True); p.add_argument("--mapping-out", required=True); p.set_defaults(func=cmd_randomize_viral)

    p = sp.add_parser("plan-behavior");         p.add_argument("--study-root", required=True); p.add_argument("--date-seed", required=True); p.add_argument("--agents", nargs=2, required=True); p.set_defaults(func=cmd_plan_behavior)
    p = sp.add_parser("verify-behavior-plan");  p.add_argument("--study-root", required=True); p.set_defaults(func=cmd_verify_behavior)

    p = sp.add_parser("plan-physiology");       p.add_argument("--study-root", required=True); p.add_argument("--date-seed", required=True); p.add_argument("--agents", nargs=2, required=True); p.add_argument("--allow-imbalance", action="store_true"); p.set_defaults(func=cmd_plan_physiology)
    p = sp.add_parser("verify-physiology-plan");p.add_argument("--study-root", required=True); p.set_defaults(func=cmd_verify_physiology)

    p = sp.add_parser("overlay-behavior");      p.add_argument("--study-root", required=True); p.set_defaults(func=cmd_overlay_behavior)
    p = sp.add_parser("overlay-aliquot");       p.add_argument("--study-root", required=True); p.set_defaults(func=cmd_overlay_aliquot)
    p = sp.add_parser("overlay-physiology");    p.add_argument("--study-root", required=True); p.set_defaults(func=cmd_overlay_physiology)

    p = sp.add_parser("inject-scan");           p.add_argument("--study-root", required=True); p.add_argument("--animal-id", required=True); p.add_argument("--stage", required=True); p.add_argument("--session"); p.add_argument("--qr-payload"); p.add_argument("--photo"); p.set_defaults(func=cmd_inject_scan)

    p = sp.add_parser("blind-anatomy");         p.add_argument("--study-root", required=True); p.add_argument("--input-root", required=True); p.add_argument("--output-root", required=True); p.add_argument("--force", action="store_true"); p.add_argument("--allow-missing-index", action="store_true"); p.add_argument("--seal", action="store_true"); p.add_argument("--working-root"); p.set_defaults(func=cmd_blind_anatomy)
    p = sp.add_parser("verify-anatomy-crossref", help="Compare current trees to saved crossref manifest.")
    p.add_argument("--study-root", required=True)
    p.add_argument("--input-root", required=False, help="Override input root if moved")
    p.add_argument("--output-root", required=False, help="Override output root if moved")
    p.add_argument("--relax-paths", action="store_true", help="If paths differ, search roots by SHA-256 to relocate")
    p.add_argument("--dhash-threshold", type=int, default=6, help="dHash Hamming-distance threshold for visual match")
    p.set_defaults(func=cmd_verify_anatomy_crossref)

    p = sp.add_parser("record-derivative", help="Record an edited/derived file against its blinded parent.")
    p.add_argument("--study-root", required=True)
    p.add_argument("--parent", required=True, help="Path to original blinded image")
    p.add_argument("--child", required=True, help="Path to derived/edited image")
    p.add_argument("--note", required=False, help="Short description of the edit")
    p.add_argument("--dhash-threshold", type=int, default=6)
    p.set_defaults(func=cmd_record_derivative)

    p = sp.add_parser("unblind");               p.add_argument("--study-root", required=True); p.add_argument("--mapping-path", required=True); p.set_defaults(func=cmd_unblind)
    p = sp.add_parser("verify-all");            p.add_argument("--study-root", required=True); p.set_defaults(func=cmd_verify_all)

    args = ap.parse_args(); args.func(args)

if __name__ == "__main__":
    main()
