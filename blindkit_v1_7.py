#!/usr/bin/env python3
"""
BlindKit v1.7 — Two‑root trustless blinding toolkit
===================================================

This version splits state into TWO distinct repositories/folders:

- BLINDER ROOT (private): true keys, plans, full crossrefs, registry, photos, sealed archives
- EXPERIMENTER ROOT (public to the blinded analyst): blinded outputs, receipts, analysis provenance

Typical usage:
  • Blinder runs planning/overlays and anatomy blinding → writes secrets to BLINDER, blinded assets to EXPERIMENTER
  • Experimenter performs injections & analysis blinded → writes usage receipts and provenance to EXPERIMENTER
  • Blinder periodically runs reconcile to import receipts and update the registry in BLINDER

Python 3.8+
Optional libs: pillow (anatomy), qrcode (QR labels)
"""

import argparse, csv, datetime, hashlib, json, os, pathlib, random, re, shutil, sys, zipfile
from typing import List, Tuple, Optional

# Optional QR
try:
    import qrcode
    HAS_QR = True
except Exception:
    HAS_QR = False

# Optional PIL
try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except Exception:
    HAS_PIL = False

# ---------- Basics ----------
def sha256_str(s: str) -> str:
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def sha256_file(p: pathlib.Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()

def ts():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat()

def ensure_dirs(root: pathlib.Path, subs):
    for s in subs: os.makedirs(root / s, exist_ok=True)

# ---------- Two‑root initialization ----------
def cmd_init_dual(a):
    br = pathlib.Path(a.blinder_root).resolve()
    er = pathlib.Path(a.experimenter_root).resolve()
    ensure_dirs(br, ["configs","labels","logs","media/photos","archives"])
    ensure_dirs(er, ["receipts","logs","media/photos","anatomy_blinded","anatomy_working","provenance"])
    # minimal meta
    (br / "study_meta.json").write_text(json.dumps({"study_id": a.study_id, "role":"BLINDER","created": ts()}, indent=2))
    (er / "study_meta.json").write_text(json.dumps({"study_id": a.study_id, "role":"EXPERIMENTER","created": ts()}, indent=2))
    # registry at BLINDER
    (br / "labels" / "registry.json").write_text(json.dumps({"entries":[]}, indent=2))
    print("[+] Initialized two roots")
    print("    BLINDER     →", br)
    print("    EXPERIMENTER→", er)

# ---------- Animals ----------
def animals_path(br: pathlib.Path): return br / "configs" / "animals.jsonl"
def animals_list(br: pathlib.Path):
    ans=[]; p=animals_path(br)
    if p.exists():
        for line in p.read_text().splitlines():
            try: ans.append(json.loads(line)["animal"])
            except: pass
    return ans

def cmd_register_animal(a):
    br = pathlib.Path(a.blinder_root).resolve()
    ensure_dirs(br, ["configs"])
    with open(animals_path(br), "a", encoding="utf-8") as f:
        f.write(json.dumps({"animal": a.animal_id, "sex": a.sex, "weight": a.weight, "ts": ts()})+"\n")
    print("[+] Registered animal", a.animal_id, "in BLINDER configs")

# ---------- Behavior planning (balanced per animal: 2xA/2xB) ----------
def seeded_rng(date_seed: str, animal: str):
    base = int(date_seed)
    ah = int(sha256_str(animal)[:8], 16)
    return random.Random(base ^ ah)

def cmd_plan_behavior(a):
    br = pathlib.Path(a.blinder_root).resolve()
    ensure_dirs(br, ["configs"])
    ans = animals_list(br)
    if not ans: raise SystemExit("[!] No animals registered (BLINDER).")
    A,B = a.agents
    plan = {"date_seed": a.date_seed, "agents":[A,B], "sessions":4, "assignments":{}}
    for an in sorted(ans):
        seq=[A,A,B,B]; seeded_rng(a.date_seed, an).shuffle(seq)
        plan["assignments"][an] = [{"session": i+1, "agent": seq[i]} for i in range(4)]
    out_json = br / "configs" / "behavior_plan.json"
    out_csv  = br / "configs" / "behavior_plan.csv"
    out_json.write_text(json.dumps(plan, indent=2))
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["animal","session","agent"])
        for an in sorted(plan["assignments"]):
            for r in plan["assignments"][an]:
                w.writerow([an, r["session"], r["agent"]])
    print("[+] Behavior plan saved at BLINDER configs")

# ---------- Physiology planning (50/50) ----------
def cmd_plan_physiology(a):
    br = pathlib.Path(a.blinder_root).resolve()
    ensure_dirs(br, ["configs"])
    ans = animals_list(br)
    if not ans: raise SystemExit("[!] No animals registered (BLINDER).")
    rng = random.Random(int(a.date_seed))
    order = sorted(ans); rng.shuffle(order)
    A,B = a.agents
    half = len(order)//2
    assign = {an: (A if i<half else B) for i, an in enumerate(order)}
    plan = {"date_seed": a.date_seed, "agents":[A,B], "assignments": assign}
    (br/"configs"/"physiology_plan.json").write_text(json.dumps(plan, indent=2))
    with open(br/"configs"/"physiology_plan.csv","w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(["animal","agent","rank"])
        for i, an in enumerate(order): w.writerow([an, assign[an], i+1])
    print("[+] Physiology plan saved at BLINDER configs")

# ---------- Overlay helpers ----------
_MICRO_ALPH="23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
def micro_code(k=4):
    import random
    return "".join(random.choice(_MICRO_ALPH) for _ in range(k))

def compute_checks(dummy: str, animal: str, stage: str):
    base=f"{dummy}{animal}{stage}"
    c1=hashlib.sha256(base.encode()).hexdigest().upper()
    c2=hashlib.sha1(base.encode()).hexdigest().upper()
    # short lengths per stage
    if stage=="VIRAL": return c1[:2], c2[:2]
    if stage=="BEHAVIOR": return c1[:4], c2[:4]
    return c1[:2], c2[:2]

def append_blinder_registry(br: pathlib.Path, rec: dict):
    reg_path = br / "labels" / "registry.json"
    try:
        reg = json.loads(reg_path.read_text())
    except Exception:
        reg={"entries":[]}
    reg["entries"].append(rec)
    reg_path.write_text(json.dumps(reg, indent=2))

# ---------- Overlays (BLINDER) ----------
def overlay_common(animal: str, stage: str, base_id: str):
    if stage=="VIRAL":
        dummy=f"VIR-{micro_code(4)}"
    elif stage=="BEHAVIOR":
        dummy=f"BEH-{os.urandom(2).hex().upper()}"
    else:
        dummy=f"PHY-{os.urandom(2).hex().upper()}"
    c1,c2 = compute_checks(dummy, animal, stage)
    label_id = os.urandom(3).hex().upper()
    return dummy,c1,c2,label_id

def cmd_overlay_behavior(a):
    br = pathlib.Path(a.blinder_root).resolve()
    ensure_dirs(br, ["labels","media/photos","logs"])
    animal = input("Animal ID: ").strip()
    session = int(input("Behavior SESSION (1-4): ").strip())
    syringe_id = input("Base SYRINGE_ID (preparer sticker): ").strip()
    dummy,c1,c2,label = overlay_common(animal,"BEHAVIOR",syringe_id)
    ts0 = ts()
    # Label artifact
    lbl = br/"labels"/f"{animal}_BEH{session}_{label}.txt"
    lbl.write_text(f"ANIMAL:{animal}\nSTAGE:BEHAVIOR\nSESSION:{session}\nDUMMY:{dummy}\nCHECK1:{c1}\nCHECK2:{c2}\nSYRINGE:{syringe_id}\nLABEL:{label}\nTS:{ts0}\n")
    if HAS_QR:
        payload=json.dumps({"animal":animal,"stage":"BEHAVIOR","session":session,"dummy":dummy,"check1":c1,"check2":c2,"syringe_id":syringe_id,"label_id":label,"ts":ts0}, sort_keys=True)
        qrcode.make(payload).save(br/"labels"/f"{animal}_BEH{session}_{label}.png")
    # Registry (BLINDER only)
    append_blinder_registry(br, {"ts_overlay": ts0,"animal":animal,"stage":"BEHAVIOR","session":session,
                                 "dummy":dummy,"check1":c1,"check2":c2,"syringe_id":syringe_id,
                                 "label_id":label,"status":"issued"})
    print("[✓] BEHAVIOR overlay issued at BLINDER root.")

def cmd_overlay_physiology(a):
    br = pathlib.Path(a.blinder_root).resolve()
    ensure_dirs(br, ["labels","media/photos","logs","configs"])
    animal = input("Animal ID: ").strip()
    syringe_id = input("Base SYRINGE_ID (preparer sticker): ").strip()
    plan_path = br/"configs"/"physiology_plan.json"
    if plan_path.exists():
        agent = json.loads(plan_path.read_text())["assignments"].get(animal,"?")
        print(f"[Blinder‑only] Planned agent for {animal}: {agent}")
    dummy,c1,c2,label = overlay_common(animal,"PHYSIOLOGY",syringe_id)
    ts0=ts()
    lbl = br/"labels"/f"{animal}_PHYS_{label}.txt"
    lbl.write_text(f"ANIMAL:{animal}\nSTAGE:PHYSIOLOGY\nDUMMY:{dummy}\nCHECK1:{c1}\nCHECK2:{c2}\nSYRINGE:{syringe_id}\nLABEL:{label}\nTS:{ts0}\n")
    if HAS_QR:
        payload=json.dumps({"animal":animal,"stage":"PHYSIOLOGY","dummy":dummy,"check1":c1,"check2":c2,"syringe_id":syringe_id,"label_id":label,"ts":ts0}, sort_keys=True)
        qrcode.make(payload).save(br/"labels"/f"{animal}_PHYS_{label}.png")
    append_blinder_registry(br, {"ts_overlay": ts0,"animal":animal,"stage":"PHYSIOLOGY","session":None,
                                 "dummy":dummy,"check1":c1,"check2":c2,"syringe_id":syringe_id,
                                 "label_id":label,"status":"issued"})
    print("[✓] PHYSIOLOGY overlay issued at BLINDER root.")

def cmd_overlay_aliquot(a):
    br = pathlib.Path(a.blinder_root).resolve()
    ensure_dirs(br, ["labels","media/photos","logs"])
    animal = input("Animal ID: ").strip()
    aliquot_id = input("Base ALIQUOT_ID (cap/side code): ").strip()
    dummy,c1,c2,label = overlay_common(animal,"VIRAL",aliquot_id)
    ts0=ts()
    lbl = br/"labels"/f"{animal}_VIRAL_{label}.micro.txt"
    lbl.write_text(f"D:{dummy}\nC1:{c1}\nC2:{c2}\nCID:{aliquot_id}\nL:{label}\nTS:{ts0}\n")
    if HAS_QR:
        payload=json.dumps({"animal":animal,"stage":"VIRAL","dummy":dummy,"check1":c1,"check2":c2,"syringe_id":aliquot_id,"label_id":label,"ts":ts0}, sort_keys=True)
        qrcode.make(payload).save(br/"labels"/f"{animal}_VIRAL_{label}.png")
    append_blinder_registry(br, {"ts_overlay": ts0,"animal":animal,"stage":"VIRAL","session":None,
                                 "dummy":dummy,"check1":c1,"check2":c2,"syringe_id":aliquot_id,
                                 "label_id":label,"status":"issued"})
    print("[✓] VIRAL micro‑label issued at BLINDER root.")

# ---------- Experimenter: inject‑scan → usage receipt ----------
def cmd_inject_scan(a):
    er = pathlib.Path(a.experimenter_root).resolve()
    ensure_dirs(er, ["receipts","media/photos","logs"])
    animal=a.animal_id; stage=a.stage.upper()
    session = int(a.session) if (stage=="BEHAVIOR" and a.session) else None

    # accept QR JSON payload or prompt
    if a.qr_payload:
        try:
            rec=json.loads(a.qr_payload)
            dummy, c1, c2 = rec["dummy"], rec["check1"], rec["check2"]
            label_id = rec["label_id"]; container = rec.get("syringe_id","")
        except Exception as e:
            raise SystemExit(f"[!] Invalid --qr-payload JSON: {e}")
    else:
        dummy = input("Dummy: ").strip()
        c1 = input("Check1: ").strip().upper()
        c2 = input("Check2: ").strip().upper()
        label_id = input("Label ID: ").strip().upper()
        container = input("Syringe/Container ID: ").strip()

    # We cannot check ground truth at EXPERIMENTER side (no keys). Instead create receipt for blinder reconciliation.
    photo_path, photo_hash = "", ""
    if a.photo:
        psrc=pathlib.Path(a.photo)
        if psrc.exists():
            dst_dir = er / "media" / "photos" / animal / stage
            os.makedirs(dst_dir, exist_ok=True)
            dst = dst_dir / (f"inject_{label_id}{psrc.suffix.lower()}")
            shutil.copy2(psrc, dst)
            photo_path=str(dst.resolve()); photo_hash=sha256_file(dst)

    receipt = {
        "ts_inject": ts(),
        "animal": animal,
        "stage": stage,
        "session": session,
        "dummy": dummy,
        "check1": c1,
        "check2": c2,
        "label_id": label_id,
        "syringe_id": container,
        "photo_hash": photo_hash
    }
    rid = f"{animal}_{stage}{'' if session is None else '_S'+str(session)}_{label_id}_{int(datetime.datetime.utcnow().timestamp())}.json"
    (er / "receipts" / rid).write_text(json.dumps(receipt, indent=2))
    print("[✓] Injection receipt written at EXPERIMENTER root:", er / "receipts" / rid)
    print("    Blinder will reconcile this receipt to mark the overlay as USED.")

# ---------- Blinder: reconcile receipts ----------
def cmd_reconcile_usage(a):
    br = pathlib.Path(a.blinder_root).resolve()
    er = pathlib.Path(a.experimenter_root).resolve()
    reg_path = br / "labels" / "registry.json"
    try:
        reg=json.loads(reg_path.read_text())
    except Exception:
        reg={"entries":[]}
    updated=0; errors=0
    for rfile in sorted((er / "receipts").glob("*.json")):
        rec=json.loads(rfile.read_text())
        # find matching issued entry by animal, stage, (session), dummy, label_id
        match=None
        for e in reg["entries"]:
            if e.get("status")=="issued" and e.get("animal")==rec["animal"] and e.get("stage")==rec["stage"] \
               and (e.get("session")==rec["session"]) and e.get("dummy")==rec["dummy"] and e.get("label_id")==rec["label_id"]:
                match=e; break
        if not match:
            print(f"[!] No matching ISSUED overlay for receipt {rfile.name}")
            errors+=1; continue
        # verify checks
        exp_c1, exp_c2 = compute_checks(rec["dummy"], rec["animal"], rec["stage"])
        if rec["check1"]!=exp_c1 or rec["check2"]!=exp_c2:
            print(f"[!] Check mismatch for {rfile.name} (possible transcription error).")
            errors+=1; continue
        match["status"]="used"; match["ts_inject"]=rec["ts_inject"]; match["inject_photo_hash"]=rec.get("photo_hash","")
        updated+=1
    reg_path.write_text(json.dumps(reg, indent=2))
    print(f"[✓] Reconcile complete. Updated {updated} entries; {errors} issues.")

# ---------- Anatomy: blinded copy (writes to EXPERIMENTER; secrets to BLINDER) ----------
IMAGE_EXTS={".jpg",".jpeg",".png",".tif",".tiff"}
def is_img(p: pathlib.Path): return p.is_file() and p.suffix.lower() in IMAGE_EXTS
_PATTERNS=[re.compile(r"(?i)index[\s_]*([0-9]+)[\s_-]*[-–][\s_-]*([0-9]+)"),
           re.compile(r"(?i)idx[\s_]*([0-9]+)[\s_-]*[-–][\s_-]*([0-9]+)")]

def parse_index(name: str)->Optional[Tuple[int,int]]:
    for pat in _PATTERNS:
        m=pat.search(name)
        if m:
            try: return int(m.group(1)), int(m.group(2))
            except: pass
    return None

def dhash_image(path: pathlib.Path, size: int=8) -> str:
    if not HAS_PIL: raise RuntimeError("Pillow required for anatomy")
    with Image.open(path) as im:
        im = im.convert("L").resize((size+1,size))
        bits=[]
        for y in range(size):
            for x in range(size):
                a=im.getpixel((x,y)); b=im.getpixel((x+1,y))
                bits.append(1 if b>a else 0)
        val=0
        for b in bits: val=(val<<1)|b
        return f"{val:016x}"

def strip_meta_copy(src: pathlib.Path, dst: pathlib.Path):
    if not HAS_PIL: raise RuntimeError("Pillow required")
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode in ("P","PA"): im = im.convert("RGBA" if im.mode=="PA" else "RGB")
        fmt = (dst.suffix.lower().strip(".") or im.format or "PNG").upper()
        params={}
        if fmt=="JPEG": params={"quality":95,"optimize":True}
        elif fmt in ("TIFF","TIF"): params={"compression":"tiff_deflate"}
        im.save(dst, format=fmt, **params)

def cmd_blind_anatomy(a):
    br = pathlib.Path(a.blinder_root).resolve()
    er = pathlib.Path(a.experimenter_root).resolve()
    in_root = pathlib.Path(a.input_root).resolve()
    out_root = er / "anatomy_blinded"
    if not in_root.is_dir(): raise SystemExit("[!] --input-root must be a directory with subfolders (one per animal).")
    ensure_dirs(br, ["configs"])
    ensure_dirs(er, ["anatomy_blinded"])
    # map animal folder → blinded ID
    today = datetime.datetime.utcnow().strftime("%Y%m%d")
    mapping={}
    for adir in sorted([p for p in in_root.iterdir() if p.is_dir()]):
        h=sha256_str(f"{adir.name}|{today}|ANAT_V1")
        bid="ANA-"+h[:6].upper()
        i=6
        while bid in mapping.values():
            i+=1; bid="ANA-"+h[:i].upper()
        mapping[adir.name]=bid
    # copy & hash
    cross=[]
    total=0
    for orig, bid in mapping.items():
        src_dir=in_root/orig
        out_dir=out_root/bid
        os.makedirs(out_dir, exist_ok=True)
        imgs=[p for p in sorted(src_dir.rglob("*")) if is_img(p)]
        parsed=[]
        miss=[]
        for p in imgs:
            t=parse_index(p.name)
            (parsed if t else miss).append((t,p))
        if miss and not a.allow_missing_index:
            raise SystemExit(f"[!] {orig}: {len(miss)} files missing 'INDEX M-N' notation.")
        if miss and a.allow_missing_index:
            baseM=9999
            for i,(_,p) in enumerate(miss,1): parsed.append(((baseM,i),p))
        parsed.sort(key=lambda x:(x[0][0],x[0][1],x[1].name))
        for (M,N), src in parsed:
            dst = out_dir / f"IDX_{M:03d}-{N:03d}{src.suffix.lower()}"
            # hashes
            o_sha = sha256_file(src)
            o_dh  = dhash_image(src)
            strip_meta_copy(src, dst)
            b_sha = sha256_file(dst)
            b_dh  = dhash_image(dst)
            cross.append({
                "animal": orig,
                "blinded_id": bid,
                "M": M, "N": N,
                "original_relpath": str(src),
                "original_sha256": o_sha,
                "original_dhash": o_dh,
                "blinded_relpath": str(dst.relative_to(er)),
                "blinded_sha256": b_sha,
                "blinded_dhash": b_dh
            })
            total+=1
    # Write manifests:
    #   BLINDER: full crossref (contains original paths)
    #   EXPERIMENTER: blinded-only manifest (no original info)
    b_cfg = br / "configs"
    e_cfg = er / "configs"
    os.makedirs(b_cfg, exist_ok=True)
    os.makedirs(e_cfg, exist_ok=True)
    (b_cfg/"anatomy_blind_map.json").write_text(json.dumps({"created":ts(),"mapping":mapping}, indent=2))
    (b_cfg/"anatomy_crossref.json").write_text(json.dumps({"created":ts(),"files":cross}, indent=2))
    blinded_only=[{"blinded_relpath":r["blinded_relpath"],"blinded_sha256":r["blinded_sha256"],"blinded_dhash":r["blinded_dhash"]} for r in cross]
    (e_cfg/"anatomy_blinded_manifest.json").write_text(json.dumps({"created":ts(),"files":blinded_only}, indent=2))
    # sealed zip at BLINDER
    if a.seal:
        stamp=datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        zip_path = br/"archives"/f"anatomy_blinded_{stamp}.zip"
        with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as zf:
            # add blinded tree
            for p in out_root.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=str(p.relative_to(er)))
            zf.writestr("manifests/anatomy_blinded_manifest.json",(e_cfg/"anatomy_blinded_manifest.json").read_text())
        (br/"archives"/(zip_path.name+".sha256")).write_text(sha256_file(zip_path))
    print(f"[✓] Anatomy blinding complete. Files: {total}")
    print("    Blinded output →", out_root)
    print("    EXPERIMENTER manifest →", e_cfg/"anatomy_blinded_manifest.json")
    print("    BLINDER crossref →", b_cfg/"anatomy_crossref.json")

# ---------- Verify anatomy (Experimenter; blinded-only) ----------
def cmd_verify_anatomy_blinded(a):
    er = pathlib.Path(a.experimenter_root).resolve()
    m = er/"configs"/"anatomy_blinded_manifest.json"
    if not m.exists(): raise SystemExit("[!] anatomy_blinded_manifest.json not found in EXPERIMENTER configs.")
    man=json.loads(m.read_text())
    errs=0
    for rec in man["files"]:
        p = er / pathlib.Path(rec["blinded_relpath"])
        if not p.exists():
            print("[!] Missing:", p); errs+=1; continue
        h=sha256_file(p)
        if h!=rec["blinded_sha256"]:
            print("[!] SHA mismatch (blinded):", p); errs+=1
    if errs==0: print("[✓] Blinded set verified OK against manifest.")
    else: print(f"[!] {errs} issue(s) found.")

# ---------- Provenance (Experimenter) ----------
def cmd_record_derivative(a):
    er = pathlib.Path(a.experimenter_root).resolve()
    parent = pathlib.Path(a.parent).resolve()
    child  = pathlib.Path(a.child).resolve()
    os.makedirs(er/"provenance", exist_ok=True)
    if not parent.exists() or not child.exists():
        raise SystemExit("[!] Parent/child must exist.")
    # store simple link (perceptual hash optional but requires PIL)
    rec={"ts":ts(),"parent":str(parent),"child":str(child),"note":a.note or ""}
    (er/"provenance"/f"link_{int(datetime.datetime.utcnow().timestamp())}.json").write_text(json.dumps(rec, indent=2))
    print("[✓] Recorded derivative link at EXPERIMENTER root.")

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(prog="blindkit_v1_7", description="Two‑root blinding toolkit (v1.7)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p=sp.add_parser("init-dual"); p.add_argument("--blinder-root", required=True); p.add_argument("--experimenter-root", required=True); p.add_argument("--study-id", required=True); p.set_defaults(func=cmd_init_dual)
    p=sp.add_parser("register-animal"); p.add_argument("--blinder-root", required=True); p.add_argument("--animal-id", required=True); p.add_argument("--sex", required=True); p.add_argument("--weight", required=True); p.set_defaults(func=cmd_register_animal)

    p=sp.add_parser("plan-behavior"); p.add_argument("--blinder-root", required=True); p.add_argument("--date-seed", required=True); p.add_argument("--agents", nargs=2, required=True); p.set_defaults(func=cmd_plan_behavior)
    p=sp.add_parser("plan-physiology"); p.add_argument("--blinder-root", required=True); p.add_argument("--date-seed", required=True); p.add_argument("--agents", nargs=2, required=True); p.set_defaults(func=cmd_plan_physiology)

    p=sp.add_parser("overlay-behavior"); p.add_argument("--blinder-root", required=True); p.set_defaults(func=cmd_overlay_behavior)
    p=sp.add_parser("overlay-physiology"); p.add_argument("--blinder-root", required=True); p.set_defaults(func=cmd_overlay_physiology)
    p=sp.add_parser("overlay-aliquot"); p.add_argument("--blinder-root", required=True); p.set_defaults(func=cmd_overlay_aliquot)

    p=sp.add_parser("inject-scan"); p.add_argument("--experimenter-root", required=True); p.add_argument("--animal-id", required=True); p.add_argument("--stage", required=True); p.add_argument("--session"); p.add_argument("--qr-payload"); p.add_argument("--photo"); p.set_defaults(func=cmd_inject_scan)

    p=sp.add_parser("reconcile-usage"); p.add_argument("--blinder-root", required=True); p.add_argument("--experimenter-root", required=True); p.set_defaults(func=cmd_reconcile_usage)

    p=sp.add_parser("blind-anatomy"); p.add_argument("--blinder-root", required=True); p.add_argument("--experimenter-root", required=True); p.add_argument("--input-root", required=True); p.add_argument("--allow-missing-index", action="store_true"); p.add_argument("--seal", action="store_true"); p.set_defaults(func=cmd_blind_anatomy)
    p=sp.add_parser("verify-anatomy-blinded"); p.add_argument("--experimenter-root", required=True); p.set_defaults(func=cmd_verify_anatomy_blinded)

    p=sp.add_parser("record-derivative"); p.add_argument("--experimenter-root", required=True); p.add_argument("--parent", required=True); p.add_argument("--child", required=True); p.add_argument("--note"); p.set_defaults(func=cmd_record_derivative)

    args = ap.parse_args(); args.func(args)

if __name__=="__main__":
    main()
