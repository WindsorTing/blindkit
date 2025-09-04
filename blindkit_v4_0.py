#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BlindKit v4.0 — Integrated two-root blinding toolkit with comprehensive audit logging,
legacy-aware physiology planning, and `init-dual --only` mode.

Features
- Two-root model: BLINDER (keys, maps) and EXPERIMENTER (blinded artifacts).
- Append-only audit logs in each root (machine JSONL + human-readable text).
- Behavior planning (A/B per animal with 4 sessions → 2xA/2xB randomized per animal).
- Physiology planning (50/50 cohort; legacy-aware via --legacy-csv/--legacy-json).
- Label overlays for behavior/physiology/aliquot; optional QR payloads.
- Injection receipts with optional photo hashing; reconcile back to overlays.
- Anatomy blinding (metadata stripped, INDEX M-N order preserved, SHA-256 + dHash), sealed ZIP + manifests.
- Provenance recording for legitimate image edits.
- Post-hoc unblinding bundle with manifest & reconciliation report; verify command.
- Audit viewer: filter audit JSONL by action/animal/stage/time/grep/tail.
"""

import argparse, csv, datetime, hashlib, json, os, pathlib, random, re, shutil, sys, zipfile, glob
import pandas as pd
from pathlib import Path
from collections import Counter
from PIL import Image, ImageDraw, ImageFont
import math

# ---------- Optional deps ----------
try:
    import qrcode
    HAS_QR = True
except Exception:
    HAS_QR = False

try:
    from PIL import Image, ImageOps
    HAS_PIL = True
except Exception:
    HAS_PIL = False

# ---------- Utils ----------
def iso_now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def sha256_str(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()

def ensure_dirs(root: pathlib.Path, subs):
    for s in subs: os.makedirs(root / s, exist_ok=True)

def safe_rel(base: pathlib.Path, p: pathlib.Path) -> str:
    try: return str(p.relative_to(base))
    except Exception: return str(p)

# ---------- Audit logging ----------
def _audit_paths(root: pathlib.Path):
    os.makedirs(root / "audit", exist_ok=True)
    return root / "audit" / "actions.jsonl", root / "audit" / "actions.log"

def _audit_write(root: pathlib.Path, action: str, **fields):
    jsonl, logtxt = _audit_paths(root)
    rec = {"ts": iso_now(), "action": action}
    rec.update({k:v for k,v in fields.items()})
    with open(jsonl, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    line = f"{rec['ts']} | {action.upper()} | " + " | ".join(f"{k}={v}" for k,v in fields.items())
    with open(logtxt, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# ---------- Init (two roots) ----------
def _init_blinder(br: pathlib.Path, study_id: str):
    ensure_dirs(br, ["configs","labels","logs","media/photos","archives","audit"])
    (br / "study_meta.json").write_text(json.dumps({"study_id": study_id, "role":"BLINDER","created": iso_now()}, indent=2))
    (br / "labels" / "registry.json").write_text(json.dumps({"entries":[]}, indent=2))

def _init_experimenter(er: pathlib.Path, study_id: str):
    ensure_dirs(er, ["receipts","logs","media/photos","anatomy_blinded","anatomy_working","provenance","configs","audit"])
    (er / "study_meta.json").write_text(json.dumps({"study_id": study_id, "role":"EXPERIMENTER","created": iso_now()}, indent=2))

def cmd_init_dual(a):
    study_id = a.study_id
    only = (a.only or "").lower().strip() or None

    if only == "blinder":
        if not a.blinder_root:
            raise SystemExit("[!] --blinder-root is required when --only blinder")
        br = pathlib.Path(a.blinder_root).resolve()
        _init_blinder(br, study_id)
        _audit_write(br, "init-dual", study_id=study_id, role="BLINDER", mode="only")
        print("[+] Initialized BLINDER root only →", br)
        return

    if only == "experimenter":
        if not a.experimenter_root:
            raise SystemExit("[!] --experimenter-root is required when --only experimenter")
        er = pathlib.Path(a.experimenter_root).resolve()
        _init_experimenter(er, study_id)
        _audit_write(er, "init-dual", study_id=study_id, role="EXPERIMENTER", mode="only")
        print("[+] Initialized EXPERIMENTER root only →", er)
        return

    # Default: create both
    if not a.blinder_root or not a.experimenter_root:
        raise SystemExit("[!] When --only is not used, both --blinder-root and --experimenter-root are required.")
    br = pathlib.Path(a.blinder_root).resolve()
    er = pathlib.Path(a.experimenter_root).resolve()
    _init_blinder(br, study_id)
    _init_experimenter(er, study_id)
    _audit_write(br, "init-dual", study_id=study_id, peer_experimenter_root=str(er), role="BLINDER", mode="both")
    _audit_write(er, "init-dual", study_id=study_id, peer_blinder_root=str(br), role="EXPERIMENTER", mode="both")
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
    ensure_dirs(br, ["configs","audit"])
    with open(animals_path(br), "a", encoding="utf-8") as f:
        f.write(json.dumps({"animal": a.animal_id, "sex": a.sex, "weight": a.weight, "ts": iso_now()})+"\n")
    _audit_write(br, "register-animal", animal_id=a.animal_id, sex=a.sex, weight=a.weight)
    print("[+] Registered animal", a.animal_id, "in BLINDER configs")

# ---------- Planning ----------
def seeded_rng(date_seed: str, animal: str):
    base = int(date_seed)
    ah = int(sha256_str(animal)[:8], 16)
    import random
    return random.Random(base ^ ah)

# def cmd_plan_behavior(a):
#     br = pathlib.Path(a.blinder_root).resolve()
#     ensure_dirs(br, ["configs","audit"])
#     ans = animals_list(br)
#     if not ans: raise SystemExit("[!] No animals registered (BLINDER).")
#     A,B = a.agents
#     plan = {"date_seed": a.date_seed, "agents":[A,B], "sessions":4, "assignments":{}}
#     for an in sorted(ans):
#         seq=[A,A,B,B]; seeded_rng(a.date_seed, an).shuffle(seq)
#         plan["assignments"][an] = [{"session": i+1, "agent": seq[i]} for i in range(4)]
#     out_json = br / "configs" / "behavior_plan.json"
#     out_csv  = br / "configs" / "behavior_plan.csv"
#     out_json.write_text(json.dumps(plan, indent=2))
#     with open(out_csv, "w", newline="", encoding="utf-8") as f:
#         w=csv.writer(f); w.writerow(["animal","session","agent"])
#         for an in sorted(plan["assignments"]):
#             for r in plan["assignments"][an]:
#                 w.writerow([an, r["session"], r["agent"]])
#     _audit_write(br, "plan-behavior", date_seed=a.date_seed, agents=",".join(a.agents), animals=len(ans))
#     print("[+] Behavior plan saved at BLINDER configs")

def cmd_plan_behavior(a): # needs stress testing
    blinder_dir = Path(a.blinder)
    planning_dir = blinder_dir / "configs"
    planning_dir.mkdir(parents=True, exist_ok=True)

    # Load registered animals
    registered_df = pd.read_json(a.animals, lines=True)
    registered_animals = set(registered_df["animal"])

    # Load agent list
    with open(a.agents) as f:
        agent_list = [line.strip() for line in f if line.strip()]
        unique_agents = sorted(set(agent_list))
        seed = a.seed

    if len(unique_agents) != 2:
        print("Error: You must provide exactly two agents for 2x2 design.")
        return

    # Compose versioned output path based on seed
    versioned_json = planning_dir / f"behavior_plan_{seed}.json"

    # Load previous assignments
    existing_animals = set()
    for plan_file in planning_dir.glob("behavior_plan_*.json"):
        with open(plan_file) as f:
            plan = json.load(f)
    existing_animals.update(plan.get("assignments", {}).keys())

    # Filter only unassigned animals
    unassigned_animals = sorted(registered_animals - existing_animals)
    if not unassigned_animals:
        print("No unassigned animals found. All have been previously planned.")
        return

    print(f"Planning {len(unassigned_animals)} new animals for seed {seed}: {unassigned_animals}")

    # Hash-based seed
    hash_input = "".join(sorted(unassigned_animals)) + "".join(unique_agents) + str(seed)
    hashed_seed = int(hashlib.sha256(hash_input.encode()).hexdigest(), 16) % (10 ** 8)
    random.seed(hashed_seed)

    # 4 sessions per animal, 2 of each agent in random order
    assignments = {}
    for animal in unassigned_animals:
        sessions = [unique_agents[0]] * 2 + [unique_agents[1]] * 2
        random.shuffle(sessions)
        assignments[animal] = {
        f"session_{i+1}": agent for i, agent in enumerate(sessions)
    }

    # Save
    output = {
    "seed": seed,
    "assignments": assignments
    }
    versioned_json.write_text(json.dumps(output, indent=2))
    print(f"Saved to {versioned_json}")
    all_agents = sum([list(s.values()) for s in assignments.values()], [])
    print(f"Final session distribution: {Counter(all_agents)}")


def _load_legacy_assignments(path: str, allowed_agents):
    p = pathlib.Path(path)
    if not p.exists():
        raise SystemExit(f"[!] Legacy file not found: {p}")
    legacy = {}
    if p.suffix.lower() == ".json":
        try:
            data = json.loads(p.read_text())
            items = data.get("assignments", data)
            for k,v in items.items():
                ag = str(v).strip()
                if ag not in allowed_agents:
                    raise SystemExit(f"[!] Legacy agent for {k} must be one of {allowed_agents}, got {ag}")
                legacy[str(k).strip()] = ag
        except Exception as e:
            raise SystemExit(f"[!] Could not parse legacy JSON: {e}")
    else:
        with open(p, "r", encoding="utf-8") as f:
            r = csv.reader(f)
            rows = list(r)
        start = 1 if rows and rows[0] and rows[0][0].lower().startswith("animal") else 0
        for row in rows[start:]:
            if not row: 
                continue
            an = row[0].strip()
            if not an:
                continue
            ag = row[1].strip() if len(row)>1 else ""
            if ag not in allowed_agents:
                raise SystemExit(f"[!] Legacy agent for {an} must be one of {allowed_agents}, got {ag}")
            legacy[an] = ag
    return legacy

def cmd_plan_physiology(a): # needs to integrate versioned json
    seed = a.date_seed
    blinder_dir = Path(a.blinder_root)
    plan_path = blinder_dir / "configs"
    # plan_path = blinder_dir / "configs"
    planning_dir = plan_path.parent
    versioned_json = blinder_dir / "configs" / f"physiology_plan_{seed}.json"

    planning_dir.mkdir(parents=True, exist_ok=True)

    # Load registered animal list from JSONL file
    registered_df = pd.read_json(a.reganimals_list, lines=True)
    registered_animals = set(registered_df["animal"])

    # Load agent list from text file (one agent per line)
    agent_list = a.agents
    seed = a.date_seed

    unique_agents = sorted(set(agent_list))
    if len(unique_agents) <2: 
        print("Error: you must provide at least two unique agents.")
        return

    # Load assigned animal list from versioned jsons if available
    if os.path.exists(planning_dir):
        existing_animals = set()
        for plan_file in plan_path.glob("physiology_plan_*.json"):
            with open(plan_file) as f:
                plan = json.load(f)
                existing_animals.update(plan.get("assignments", {}).keys())
        # existing_df = pd.read_csv(plan_path)
        # assigned_animals = set(existing_df["animal"])
        print(f"Loaded existing plan with {len(existing_animals)} assigned animals.")
    else:
        existing_df = pd.DataFrame()
        existing_animals = set()
        print("No existing plan found. Starting fresh.")

    # Determine unassigned animals
    # unassigned_animals = sorted(registered_animals - assigned_animals)
    unassigned_animals = sorted(registered_animals - existing_animals)
    if not unassigned_animals:
        print("No unassigned animals found. Plan is up to date.")
        return

    print(f"Found {len(unassigned_animals)} unassigned animals: {unassigned_animals}")

    # Hash inputs to generate deterministic seed
    hash_input = "".join(sorted(unassigned_animals)) + "".join(sorted(unique_agents)) + str(seed)
    hashed_seed = int(hashlib.sha256(hash_input.encode()).hexdigest(), 16) % (10 ** 8)
    random.seed(hashed_seed)

    # Shuffle agents and assign
    # agent_cycle = agent_list * ((len(unassigned_animals) // len(agent_list)) + 1)
    # random.shuffle(agent_cycle)
    # agent_assignments = agent_cycle[:len(unassigned_animals)]

    # Create balanced group assignment
    n = len(unassigned_animals)
    n_agents = len(unique_agents)
    base_count = n // n_agents
    remainder = n % n_agents

    # Create balanced agent list
    agent_counts = [base_count + (1 if i < remainder else 0) for i in range(n_agents)]
    balanced_agents = []
    for agent, count in zip(unique_agents, agent_counts):
        balanced_agents.extend([agent] * count)
    random.shuffle(balanced_agents)

    # Create new assignment rows
    # new_rows = []
    # for animal, agent in zip(unassigned_animals, balanced_agents):
    #     new_rows.append({
    #         "animal": animal,
    #         "agent": agent
    #     })

    assignments = {
        animal: {
            "agent": agent,
            "label": f"{''.join(random.choices('ABCDEF0123456789', k=4))}"
        }
        for animal, agent in zip(unassigned_animals, balanced_agents)
    }

    output = {
        "seed": seed,
        "assignments": assignments
    }

    # new_df = pd.DataFrame(new_rows)
    # full_plan = pd.concat([existing_df, new_df], ignore_index=True)
    # full_plan.to_csv(plan_path, index=False)

    versioned_json.write_text(json.dumps(output, indent=2))
    print(f"Saved to {versioned_json}")
    print(f"Agent distribution for this planning run: {Counter([a['agent'] for a in assignments.values()])}")

    # full_plan_for_json = dict(zip(full_plan["animal"], full_plan["agent"]))

    # plan_json = {"date_seed": a.date_seed, "agents": ["CNO, Saline"], "assignments": full_plan_for_json,
    #         "final_counts": full_plan['agent'].value_counts().to_dict()}

    # (blinder_dir/"configs"/"physiology_plan.json").write_text(json.dumps(plan_json, indent=2))

    # print(f"Appended {len(new_df)} new assignments. Total now: {len(full_plan)} animals.")
    # print(f"Appended {len(new_df)} new assignments.")
    # print(f"Final agent distribution: {dict(Counter(full_plan['agent']))}")

# ---------- Overlays (BLINDER) ----------
_MICRO_ALPH="23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
def micro_code(k=4):
    import random
    return "".join(random.choice(_MICRO_ALPH) for _ in range(k))

def compute_checks(dummy: str, animal: str, stage: str):
    base=f"{dummy}{animal}{stage}"
    c1=hashlib.sha256(base.encode()).hexdigest().upper()
    c2=hashlib.sha1(base.encode()).hexdigest().upper()
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
    ensure_dirs(br, ["labels","media/photos","logs","audit"])
    animal = input("Animal ID: ").strip()
    session = int(input("Behavior SESSION (1-4): ").strip())
    syringe_id = input("Base SYRINGE_ID (preparer sticker): ").strip()
    dummy,c1,c2,label = overlay_common(animal,"BEHAVIOR",syringe_id)
    ts0 = iso_now()
    lbl = br/"labels"/f"{animal}_BEH{session}_{label}.txt"
    lbl.write_text(f"ANIMAL:{animal}\nSTAGE:BEHAVIOR\nSESSION:{session}\nDUMMY:{dummy}\nCHECK1:{c1}\nCHECK2:{c2}\nSYRINGE:{syringe_id}\nLABEL:{label}\nTS:{ts0}\n")
    if HAS_QR:
        payload=json.dumps({"animal":animal,"stage":"BEHAVIOR","session":session,"dummy":dummy,"check1":c1,"check2":c2,"syringe_id":syringe_id,"label_id":label,"ts":ts0}, sort_keys=True)
        qrcode.make(label).save(br/"labels"/f"{animal}_BEH{session}_{label}.png")
    append_blinder_registry(br, {"ts_overlay": ts0,"animal":animal,"stage":"BEHAVIOR","session":session,
                                 "dummy":dummy,"check1":c1,"check2":c2,"syringe_id":syringe_id,
                                 "label_id":label,"status":"issued"})
    _audit_write(br, "overlay-behavior", animal_id=animal, session=session, label_id=label, syringe_id=syringe_id)
    print("[+] BEHAVIOR overlay issued at BLINDER root.")

def cm_to_px(cm, dpi):
    return int(round((cm / 2.54) * dpi))

def load_font(paths, size_px):
    for p in paths:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size_px)
            except Exception:
                pass
    return ImageFont.load_default()

def make_qr(data, version, box_size, border):
    qr = qrcode.QRCode(
        version=version,                      # will be increased by fit=True if needed
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGBA")

def text_sprite(label, font, bleed=2):
    """Tight text sprite with baseline offset (no clipping)."""
    tmp = Image.new("RGBA", (1, 1))
    d = ImageDraw.Draw(tmp)
    left, top, right, bottom = d.textbbox((0, 0), label, font=font)
    w = (right - left) + 2 * bleed
    h = (bottom - top) + 2 * bleed
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((bleed - left, bleed - top), label, font=font, fill=(0, 0, 0, 255))
    return img

def tile_horizontal(canvas, sprite, y, gap_px=12, x_start=0, x_end=None):
    """Repeat sprite left→right across [x_start, x_end). Corner-safe via bounds."""
    W, _ = canvas.size
    if x_end is None:
        x_end = W
    step = sprite.width + gap_px
    origin = 0
    first = x_start + ((-(x_start - origin)) % step)
    x = first
    while x + sprite.width <= x_end:
        canvas.alpha_composite(sprite, (x, y))
        x += step

def tile_vertical(canvas, sprite_rot, x, gap_px=12, y_start=0, y_end=None):
    """Repeat sprite top→bottom across [y_start, y_end). Corner-safe via bounds."""
    _, H = canvas.size
    if y_end is None:
        y_end = H
    step = sprite_rot.height + gap_px
    origin = 0
    first = y_start + ((-(y_start - origin)) % step)
    y = first
    while y + sprite_rot.height <= y_end:
        canvas.alpha_composite(sprite_rot, (x, y))
        y += step

def build_qr_with_border_labels(
    data, label, font,
    inner_gap, outer_gap, repeat_gap, corner_gap,
    qr_version, box_size, quiet_border
):
    # 1) QR
    qr_img = make_qr(data, qr_version, box_size, quiet_border)

    # 2) Label sprites
    sprite = text_sprite(label, font, bleed=2)
    left_sprite  = sprite.rotate(90,  expand=True)
    right_sprite = sprite.rotate(-90, expand=True)

    # 3) Margin from measured sprite (text height) + gaps
    label_margin = sprite.height + inner_gap + outer_gap

    # 4) Canvas & place QR
    W, H = qr_img.size
    canvas = Image.new("RGBA", (W + 2 * label_margin, H + 2 * label_margin), (255, 255, 255, 255))
    canvas.alpha_composite(qr_img, (label_margin, label_margin))

    # 5) Edge positions
    top_y    = outer_gap
    bottom_y = canvas.height - sprite.height - outer_gap
    left_x   = outer_gap
    right_x  = canvas.width - right_sprite.width - outer_gap

    # 6) Corner guards (avoid collisions)
    left_guard   = outer_gap + left_sprite.width  + corner_gap
    right_guard  = canvas.width - (outer_gap + right_sprite.width + corner_gap)
    top_guard    = outer_gap + sprite.height + corner_gap
    bottom_guard = canvas.height - (outer_gap + sprite.height + corner_gap)

    # 7) Tile labels
    tile_horizontal(canvas, sprite, y=top_y,    gap_px=repeat_gap, x_start=left_guard,  x_end=right_guard)
    tile_horizontal(canvas, sprite, y=bottom_y, gap_px=repeat_gap, x_start=left_guard,  x_end=right_guard)
    tile_vertical(  canvas, left_sprite,  x=left_x,  gap_px=repeat_gap, y_start=top_guard,    y_end=bottom_guard)
    tile_vertical(  canvas, right_sprite, x=right_x, gap_px=repeat_gap, y_start=top_guard,    y_end=bottom_guard)

    return canvas

def cmd_overlay_physiology(a):

        # ---------------------- PHYSICAL TARGET ----------------------
    TARGET_CM = 3.0        # final sticker width/height in centimeters
    DPI       = 900        # printer DPI (203 / 300 / 600, etc.)
    SCALE     = 4          # supersampling factor (2–4 typical; 4 is very sharp)
    # -------------------------------------------------------------

    # ---------------------- AESTHETICS ----------------------
    # Base (pre-scale) sizes; code multiplies by SCALE internally
    # Good starting points for a 3 cm sticker at 300 dpi:
    FONT_SIZE_BASE = 18      # final text height ~18 px → legible at 3 cm; adjust if needed
    INNER_GAP      = 5       # QR ↔ text (tight but safe)
    OUTER_GAP      = 4       # text ↔ outer canvas edge
    REPEAT_GAP     = 10      # spacing between repeated labels
    CORNER_GAP     = 1       # extra breathing room at corners
    # ------------------------------------------------------

    # ---------------------- FONT PICK ----------------------
    FONT_PATHS = [
        "/usr/share/fonts/truetype/jetbrains-mono/JetBrainsMono-Regular.ttf",
        "/Library/Fonts/JetBrainsMono-Regular.ttf",
        "C:/Windows/Fonts/JetBrainsMono-Regular.ttf",
        "C:/Windows/Fonts/consola.ttf",                        # Consolas (Windows)
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", # DejaVu
    ]
    # ------------------------------------------------------

    # ---------------------- QR PARAMS ----------------------
    # Keep quiet border >= 4 modules for robust scanning
    QR_VERSION   = 2       # version will auto-increase if DATA needs it (fit=True)
    BOX_SIZE     = 7       # base module pixels (before SCALE); adjust if needed
    QUIET_BORDER = 4
    # ------------------------------------------------------

    br = pathlib.Path(a.blinder_root).resolve()
    ensure_dirs(br, ["labels","media/photos","logs","configs","audit"])
    animal = input("Enter Animal ID: ").strip()
    syringe_underlay_id = input("Underlay ID (number on syringe from CNO/saline solution preparer): ").strip()
    plan_path = br/"configs"
    agent="?"

    # Load plans from pool of versioned JSONs
    # if plan_path.exists():
    #     agent = json.loads(plan_path.read_text())["assignments"].get(animal,"?")
    #     print(f"[Blinder‑only] Planned agent for {animal}: {agent}")
    
    # Load assigned animal list from versioned jsons if available
    if os.path.exists(plan_path):
        for filename in os.listdir(plan_path):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(plan_path, filename)
            try:
                with open(filepath, "r") as f:
                    data = json.load(f)
                assignments = data.get("assignments", {})
                result = assignments.get(animal, "?")

                if result != "?":  # found the animal
                    print("Rat " + animal + " was successfully found in the registered animals list.")
                    agent = result
                    found_file = filename
                    break  # stop searching

            except (json.JSONDecodeError, FileNotFoundError) as e:
                print(f"Skipping {filename}: {e}")

        # existing_animals = set()
        # for plan_file in plan_path.glob("physiology_plan_*.json"):
        #     with open(plan_file) as f:
        #         # plan = json.load(f)
        #         agent = json.loads(plan_path.read_text())["assignments"].get(animal,"?")
        # existing_df = pd.read_csv(plan_path)
        # assigned_animals = set(existing_df["animal"])
        print(f"Scanned existing set of versioned jsons.")
    else:
        existing_df = pd.DataFrame()
        existing_animals = set()
        print("No existing plan found. Starting fresh.")

    # dummy,c1,c2,label = overlay_common(animal,"PHYSIOLOGY",syringe_underlay_id)
    label = agent['label']
    
    ts0=iso_now()
    lbl = br/"labels"/f"{animal}_PHYS_{label}.txt"
    lbl.write_text(f"ANIMAL:{animal}\nSTAGE:PHYSIOLOGY\nSYRINGE_UNDERLAY:{syringe_underlay_id}\nASSIGNMENT:{agent}\nTS:{ts0}\n")
    if HAS_QR:
        payload=json.dumps({"animal":animal,"stage":"PHYSIOLOGY","syringe_blinded_label":label,"ts":ts0}, sort_keys=True)
        qr_label = "RAT["+animal+"]PHYS :: "+label

        # Compute exact target pixels and superscaled working size
        target_px = cm_to_px(TARGET_CM, DPI)       # final width=height in pixels
        work_px   = target_px * SCALE

        # Load font at superscaled size
        font = load_font(FONT_PATHS, FONT_SIZE_BASE * SCALE)

        # Build at high resolution (all distances scaled)
        img_hi = build_qr_with_border_labels(
            payload, qr_label, font,
            inner_gap = INNER_GAP   * SCALE,
            outer_gap = OUTER_GAP   * SCALE,
            repeat_gap= REPEAT_GAP  * SCALE,
            corner_gap= CORNER_GAP  * SCALE,
            qr_version= QR_VERSION,
            box_size  = BOX_SIZE    * SCALE,   # QR modules scale too
            quiet_border = QUIET_BORDER,
        )

        # If the hi-res image isn't exactly work_px, center-crop or pad to square work_px
        # (Usually close already, but we force exact so downscale hits 3 cm precisely)
        W, H = img_hi.size
        # First, resize proportionally so the smallest side == work_px,
        # then center-crop or pad to exact square work_px×work_px.
        scale_factor = work_px / min(W, H)
        newW = int(round(W * scale_factor))
        newH = int(round(H * scale_factor))
        img_hi = img_hi.resize((newW, newH), resample=Image.LANCZOS)

        # Center-crop or pad to exact square
        left   = (newW - work_px) // 2
        top    = (newH - work_px) // 2
        right  = left + work_px
        bottom = top + work_px

        if newW >= work_px and newH >= work_px:
            img_hi = img_hi.crop((left, top, right, bottom))
        else:
            # pad if smaller (unlikely with current settings)
            canvas = Image.new("RGBA", (work_px, work_px), (255, 255, 255, 255))
            canvas.alpha_composite(img_hi, ((work_px - newW)//2, (work_px - newH)//2))
            img_hi = canvas

        # Downscale to exact target (3 cm @ DPI)
        img = img_hi.resize((target_px, target_px), resample=Image.LANCZOS)

        # Save with DPI metadata
        out = br/"labels"/f"{animal}_PHYS_{label}.png"
        img.save(out, dpi=(DPI, DPI))
        print(f"Saved {out} — exact size: {TARGET_CM} cm × {TARGET_CM} cm at {DPI} dpi ({target_px}×{target_px}px)")

        # qrcode.make(payload).save(br/"labels"/f"{animal}_PHYS_{label}.png")

    append_blinder_registry(br, {"ts_overlay": ts0,"animal":animal,"stage":"PHYSIOLOGY","session":None,
                                 "syringe_underlay_id":syringe_underlay_id,
                                 "status":"issued","assignment":agent})
    _audit_write(br, "overlay-physiology", animal_id=animal, syringe_underlay_id=syringe_underlay_id, assignment=agent)
    print("[+] PHYSIOLOGY overlay issued at BLINDER root for animal ID " + animal + ".")

def cmd_overlay_aliquot(a):
    br = pathlib.Path(a.blinder_root).resolve()
    ensure_dirs(br, ["labels","media/photos","logs","audit"])
    animal = input("Animal ID: ").strip()
    aliquot_id = input("Base ALIQUOT_ID (cap/side code): ").strip()
    dummy,c1,c2,label = overlay_common(animal,"VIRAL",aliquot_id)
    ts0=iso_now()
    lbl = br/"labels"/f"{animal}_VIRAL_{label}.micro.txt"
    lbl.write_text(f"D:{dummy}\nC1:{c1}\nC2:{c2}\nCID:{aliquot_id}\nL:{label}\nTS:{ts0}\n")
    if HAS_QR:
        payload=json.dumps({"animal":animal,"stage":"VIRAL","dummy":dummy,"check1":c1,"check2":c2,"syringe_id":aliquot_id,"label_id":label,"ts":ts0}, sort_keys=True)
        qrcode.make(label).save(br/"labels"/f"{animal}_VIRAL_{label}.png")
    append_blinder_registry(br, {"ts_overlay": ts0,"animal":animal,"stage":"VIRAL","session":None,
                                 "dummy":dummy,"check1":c1,"check2":c2,"syringe_id":aliquot_id,
                                 "label_id":label,"status":"issued"})
    _audit_write(br, "overlay-aliquot", animal_id=animal, label_id=label, aliquot_id=aliquot_id)
    print("[+] VIRAL micro‑label issued at BLINDER root.")

# ---------- Experimenter: inject-scan → receipt ----------
def cmd_inject_scan(a):
    er = pathlib.Path(a.experimenter_root).resolve()
    ensure_dirs(er, ["receipts","media/photos","logs","audit"])
    animal=a.animal_id; stage=a.stage.upper()
    session = int(a.session) if (stage=="BEHAVIOR" and a.session) else None

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

    # Optional photo capture
    photo_hash = ""
    if a.photo:
        psrc=pathlib.Path(a.photo)
        if psrc.exists():
            dst_dir = er / "media" / "photos" / animal / stage
            os.makedirs(dst_dir, exist_ok=True)
            dst = dst_dir / (f"inject_{label_id}{psrc.suffix.lower()}")
            shutil.copy2(psrc, dst)
            photo_hash=sha256_file(dst)

    receipt = {
        "ts_inject": iso_now(),
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
    _audit_write(er, "inject-scan", animal_id=animal, stage=stage, session=session, label_id=label_id, photo_hash=photo_hash or "none")
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
    updated=0; errors=0; seen=0
    for rfile in sorted((er / "receipts").glob("*.json")):
        seen+=1
        rec=json.loads(rfile.read_text())
        match=None
        for e in reg["entries"]:
            if e.get("status") in ("issued","used") and e.get("animal")==rec["animal"] and e.get("stage")==rec["stage"] \
               and (e.get("session")==rec["session"]) and e.get("dummy")==rec["dummy"] and e.get("label_id")==rec["label_id"]:
                match=e; break
        if not match:
            errors+=1; continue
        c1e, c2e = compute_checks(rec["dummy"], rec["animal"], rec["stage"])
        if rec["check1"]!=c1e or rec["check2"]!=c2e:
            errors+=1; continue
        match["status"]="used"; match["ts_inject"]=rec["ts_inject"]; match["inject_photo_hash"]=rec.get("photo_hash","")
        updated+=1
    reg_path.write_text(json.dumps(reg, indent=2))
    _audit_write(br, "reconcile-usage", receipts_seen=seen, updated=updated, issues=errors, experimenter_root=str(er))
    print(f"[✓] Reconcile complete. Updated {updated} entries; {errors} issues.")

# ---------- Anatomy: blinding (BLINDER → EXPERIMENTER) ----------
IMAGE_EXTS={".jpg",".jpeg",".png",".tif",".tiff"}
def is_img(p: pathlib.Path): return p.is_file() and p.suffix.lower() in IMAGE_EXTS
_PATTERNS=[re.compile(r"(?i)index[\s_]*([0-9]+)[\s_-]*[-–][\s_-]*([0-9]+)"),
           re.compile(r"(?i)idx[\s_]*([0-9]+)[\s_-]*[-–][\s_-]*([0-9]+)")]

def parse_index(name: str):
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
    if not HAS_PIL:
        raise SystemExit("[!] Pillow is required: pip install pillow")
    br = pathlib.Path(a.blinder_root).resolve()
    er = pathlib.Path(a.experimenter_root).resolve()
    in_root = pathlib.Path(a.input_root).resolve()
    out_root = er / "anatomy_blinded"
    if not in_root.is_dir(): raise SystemExit("[!] --input-root must be a directory with subfolders (one per animal).")
    ensure_dirs(br, ["configs","audit"])
    ensure_dirs(er, ["anatomy_blinded","configs","audit"])
    # map animal folder → blinded ID
    today = datetime.datetime.utcnow().strftime("%Y%m%d")
    mapping={}
    for adir in sorted([p for p in in_root.iterdir() if p.is_dir()]):
        h=sha256_str(f"{adir.name}|{today}|ANAT_V2")
        bid="ANA-"+h[:6].upper()
        i=6
        while bid in mapping.values():
            i+=1; bid="ANA-"+h[:i].upper()
        mapping[adir.name]=bid
    # copy & hash
    cross=[]; total=0
    for orig, bid in mapping.items():
        src_dir=in_root/orig
        out_dir=out_root/bid
        os.makedirs(out_dir, exist_ok=True)
        imgs=[p for p in sorted(src_dir.rglob("*")) if is_img(p)]
        parsed=[]; miss=[]
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
    b_cfg = br / "configs"
    e_cfg = er / "configs"
    os.makedirs(b_cfg, exist_ok=True); os.makedirs(e_cfg, exist_ok=True)
    (b_cfg/"anatomy_blind_map.json").write_text(json.dumps({"created":iso_now(),"mapping":mapping}, indent=2))
    (b_cfg/"anatomy_crossref.json").write_text(json.dumps({"created":iso_now(),"files":cross}, indent=2))
    blinded_only=[{"blinded_relpath":r["blinded_relpath"],"blinded_sha256":r["blinded_sha256"],"blinded_dhash":r["blinded_dhash"]} for r in cross]
    (e_cfg/"anatomy_blinded_manifest.json").write_text(json.dumps({"created":iso_now(),"files":blinded_only}, indent=2))
    # sealed zip at BLINDER (optional)
    if a.seal:
        stamp=datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        zip_path = br/"archives"/f"anatomy_blinded_{stamp}.zip"
        with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as zf:
            for p in out_root.rglob("*"):
                if p.is_file():
                    zf.write(p, arcname=safe_rel(er, p))
            zf.writestr("manifests/anatomy_blinded_manifest.json",(e_cfg/"anatomy_blinded_manifest.json").read_text())
        (br/"archives"/(zip_path.name+".sha256")).write_text(sha256_file(zip_path))
    _audit_write(br, "blind-anatomy", input_root=str(in_root), total_files=total, experimenter_root=str(er), sealed=bool(a.seal))
    _audit_write(er, "receive-anatomy", blinded_dir=str(out_root), files=total, blinder_root=str(br))
    print(f"[✓] Anatomy blinding complete. Files: {total}")
    print("    Blinded output →", out_root)
    print("    EXPERIMENTER manifest →", e_cfg/"anatomy_blinded_manifest.json")
    print("    BLINDER crossref →", b_cfg/"anatomy_crossref.json")

# ---------- Experimenter: verify blinded anatomy ----------
def cmd_verify_anatomy_blinded(a):
    er = pathlib.Path(a.experimenter_root).resolve()
    m = er/"configs"/"anatomy_blinded_manifest.json"
    if not m.exists():
        _audit_write(er, "verify-anatomy-blinded", status="manifest_missing")
        raise SystemExit("[!] anatomy_blinded_manifest.json not found in EXPERIMENTER configs.")
    man=json.loads(m.read_text())
    errs=0; checked=0
    for rec in man["files"]:
        p = er / pathlib.Path(rec["blinded_relpath"])
        if not p.exists():
            errs+=1; continue
        h=sha256_file(p); checked+=1
        if h!=rec["blinded_sha256"]:
            errs+=1
    status = "ok" if errs==0 else f"issues:{errs}"
    _audit_write(er, "verify-anatomy-blinded", files_checked=checked, issues=errs, status=status)
    if errs==0: print("[✓] Blinded set verified OK against manifest.")
    else: print(f"[!] {errs} issue(s) found.")

# ---------- Experimenter: record provenance of edits ----------
def cmd_record_derivative(a):
    er = pathlib.Path(a.experimenter_root).resolve()
    parent = pathlib.Path(a.parent).resolve()
    child  = pathlib.Path(a.child).resolve()
    os.makedirs(er/"provenance", exist_ok=True)
    if not parent.exists() or not child.exists():
        _audit_write(er, "record-derivative", status="fail_missing_parent_or_child", parent=str(parent), child=str(child))
        raise SystemExit("[!] Parent/child must exist.")
    rec={"ts":iso_now(),"parent":str(parent),"child":str(child),"note":a.note or ""}
    if HAS_PIL:
        rec["parent_sha256"]=sha256_file(parent)
        rec["child_sha256"]=sha256_file(child)
    (er/"provenance"/f"link_{int(datetime.datetime.utcnow().timestamp())}.json").write_text(json.dumps(rec, indent=2))
    _audit_write(er, "record-derivative", parent=str(parent), child=str(child), note=(a.note or ""))
    print("[✓] Recorded derivative link at EXPERIMENTER root.")

# ---------- Post-hoc packaging & verification ----------
def _load_json(p: pathlib.Path, default=None):
    try: return json.loads(p.read_text())
    except Exception: return {} if default is None else default

def cmd_package_unblinding(a):
    br = pathlib.Path(a.blinder_root).resolve()
    er = pathlib.Path(a.experimenter_root).resolve()
    out = pathlib.Path(a.out).resolve()
    if not out.suffix.lower().endswith(".zip"):
        out = out.with_suffix(".zip")
    os.makedirs(out.parent, exist_ok=True)

    behavior = br / "configs" / "behavior_plan.json"
    physiology = br / "configs" / "physiology_plan.json"
    registry = br / "labels" / "registry.json"
    a_cross = br / "configs" / "anatomy_crossref.json"
    a_map   = br / "configs" / "anatomy_blind_map.json"

    receipts = sorted((er / "receipts").glob("*.json"))
    e_anat_manifest = er / "configs" / "anatomy_blinded_manifest.json"
    provenance_dir = er / "provenance"

    reg = _load_json(registry, {"entries":[]})
    used_rows = []
    issues = []
    idx = {}
    for e in reg.get("entries", []):
        key = (e.get("animal"), e.get("stage"), e.get("session"), e.get("dummy"), e.get("label_id"))
        idx.setdefault(key, []).append(e)

    for rfile in receipts:
        r = _load_json(rfile, {})
        key = (r.get("animal"), r.get("stage"), r.get("session"), r.get("dummy"), r.get("label_id"))
        matches = idx.get(key, [])
        status = "MATCHED" if any(m.get("status") in ("issued","used") for m in matches) else "NO_MATCH"
        used_rows.append({
            "receipt_file": str(rfile),
            "animal": r.get("animal",""),
            "stage": r.get("stage",""),
            "session": r.get("session",""),
            "dummy": r.get("dummy",""),
            "label_id": r.get("label_id",""),
            "check1": r.get("check1",""),
            "check2": r.get("check2",""),
            "syringe_id": r.get("syringe_id",""),
            "photo_hash": r.get("photo_hash",""),
            "matched_in_registry": status,
        })
        if status != "MATCHED":
            issues.append(f"Receipt {os.path.basename(rfile)}: no matching registry overlay")

    zip_path = out
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        manifest = {"created": iso_now(), "files": []}

        def add_file(path: pathlib.Path, arcname: str):
            if not path or not path.exists(): return
            zf.write(path, arcname=arcname)
            manifest["files"].append({"arcname": arcname, "sha256": sha256_file(path)})

        # Blinder configs
        add_file(behavior,   "blinder/configs/behavior_plan.json")
        add_file(physiology, "blinder/configs/physiology_plan.json")
        add_file(registry,   "blinder/labels/registry.json")
        add_file(a_map,      "blinder/configs/anatomy_blind_map.json")
        add_file(a_cross,    "blinder/configs/anatomy_crossref.json")

        # Experimenter artifacts
        add_file(e_anat_manifest, "experimenter/configs/anatomy_blinded_manifest.json")
        for rfile in receipts:
            add_file(rfile, f"experimenter/receipts/{os.path.basename(rfile)}")
        if provenance_dir.is_dir():
            for pj in sorted(provenance_dir.glob("*.json")):
                add_file(pj, f"experimenter/provenance/{pj.name}")

        # Reconciliation CSV
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(used_rows[0].keys()) if used_rows else
                           ["receipt_file","animal","stage","session","dummy","label_id","check1","check2","syringe_id","photo_hash","matched_in_registry"])
        w.writeheader()
        for row in used_rows: w.writerow(row)
        csv_bytes = buf.getvalue().encode("utf-8")
        zf.writestr("reports/reconciliation.csv", csv_bytes)
        manifest["files"].append({"arcname":"reports/reconciliation.csv","sha256":sha256_bytes(csv_bytes)})

        # Summary report
        summary = io.StringIO()
        summary.write("# BlindKit v4.0 — Unblinding Bundle Summary\n")
        summary.write(f"- Created: {iso_now()}\n")
        summary.write(f"- Blinder root: {br}\n- Experimenter root: {er}\n")
        summary.write(f"- Receipts: {len(receipts)}\n")
        summary.write(f"- Receipts matched to registry: {sum(1 for r in used_rows if r['matched_in_registry']=='MATCHED')}\n")
        if issues:
            summary.write("\n## Issues\n")
            for s in issues: summary.write(f"- {s}\n")
        else:
            summary.write("\nNo issues found during packaging.\n")
        rpt_bytes = summary.getvalue().encode("utf-8")
        zf.writestr("reports/summary.md", rpt_bytes)
        manifest["files"].append({"arcname":"reports/summary.md","sha256":sha256_bytes(rpt_bytes)})

        # Final manifest
        zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2).encode("utf-8"))

    _audit_write(br, "package-unblinding", out_zip=str(zip_path), receipts=len(receipts), issues=len(issues))
    print("[✓] Unblinding bundle created:", zip_path)

def cmd_verify_posthoc(a):
    z = pathlib.Path(a.bundle).resolve()
    if not z.exists():
        print("[!] Bundle not found:", z)
        return
    with zipfile.ZipFile(z, "r") as zf:
        # Load manifest
        try:
            man = json.loads(zf.read("MANIFEST.json"))
        except Exception:
            print("[!] MANIFEST.json missing or invalid.")
            return
        errors = 0
        for entry in man.get("files", []):
            arc = entry["arcname"]; expected = entry["sha256"]
            data = zf.read(arc)
            h = hashlib.sha256(data).hexdigest()
            if h != expected:
                print(f"[!] Hash mismatch: {arc}")
                errors += 1
        # Reconciliation quick stats
        rec_summary = ""
        try:
            rec_csv = zf.read("reports/reconciliation.csv").decode("utf-8", errors="ignore").splitlines()
            import csv as _csv, io
            rows = list(_csv.DictReader(io.StringIO("\n".join(rec_csv))))
            matched = sum(1 for r in rows if r.get("matched_in_registry")=="MATCHED")
            rec_summary = f"Receipts: {len(rows)}, matched: {matched}, unmatched: {len(rows)-matched}"
        except Exception:
            rec_summary = "No reconciliation.csv found."
        # Optional logging target(s)
        if a.blinder_root:
            br = pathlib.Path(a.blinder_root).resolve()
            _audit_write(br, "verify-posthoc", bundle=str(z), integrity=("ok" if errors==0 else "fail"), reconciliation=rec_summary)
        if a.experimenter_root:
            er = pathlib.Path(a.experimenter_root).resolve()
            _audit_write(er, "verify-posthoc", bundle=str(z), integrity=("ok" if errors==0 else "fail"), reconciliation=rec_summary)
        if errors==0:
            print("[✓] Bundle integrity OK (all internal file hashes match).")
        else:
            print(f"[!] Bundle integrity FAILED with {errors} mismatched file(s).")
        print("Reconciliation summary:", rec_summary)

# ---------- Audit viewer ----------
def cmd_audit_show(a):
    root = pathlib.Path(a.root).resolve()
    jpath = root / "audit" / "actions.jsonl"
    if not jpath.exists():
        print("[!] No audit/actions.jsonl at", jpath)
        return
    import json, datetime
    def parse_ts(s):
        try:
            return datetime.datetime.fromisoformat(s.replace("Z",""))
        except Exception:
            return None

    since = parse_ts(a.since) if a.since else None
    until = parse_ts(a.until) if a.until else None
    matched = []
    with open(jpath, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if a.action and rec.get("action") != a.action:
                continue
            if a.animal and rec.get("animal_id") != a.animal:
                continue
            if a.stage and rec.get("stage") != a.stage:
                continue
            if since or until:
                rts = parse_ts(rec.get("ts",""))
                if rts is None:
                    continue
                if since and rts < since:
                    continue
                if until and rts > until:
                    continue
            if a.grep:
                blob = json.dumps(rec, ensure_ascii=False)
                if a.grep not in blob:
                    continue
            matched.append(rec)

    if a.tail and a.tail > 0:
        matched = matched[-a.tail:]

    for rec in matched:
        print(f"{rec.get('ts','')} | {rec.get('action','').upper()} | " +
              " | ".join(f"{k}={v}" for k,v in rec.items() if k not in ('ts','action')))

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(prog="blindkit_v4_0", description="Two‑root blinding toolkit + post‑hoc packaging (v4.0)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    # Init & animals
    p=sp.add_parser("init-dual")
    p.add_argument("--blinder-root")  # optional; required unless --only experimenter
    p.add_argument("--experimenter-root")  # optional; required unless --only blinder
    p.add_argument("--study-id", required=True)
    p.add_argument("--only", choices=["blinder","experimenter"])
    p.set_defaults(func=cmd_init_dual)

    p=sp.add_parser("register-animal"); p.add_argument("--blinder-root", required=True); p.add_argument("--animal-id", required=True); p.add_argument("--sex", required=True); p.add_argument("--weight", required=True); p.set_defaults(func=cmd_register_animal)

    # Planning
    p=sp.add_parser("plan-behavior"); p.add_argument("--blinder-root", required=True); p.add_argument("--date-seed", required=True); p.add_argument("--agents", nargs=2, required=True); p.set_defaults(func=cmd_plan_behavior)
    p=sp.add_parser("plan-physiology"); p.add_argument("--blinder-root", required=True); p.add_argument("--reganimals-list", required=True); p.add_argument("--date-seed", required=True); p.add_argument("--agents", nargs=2, required=True); p.add_argument("--legacy-json"); p.add_argument("--legacy-csv"); p.add_argument("--allow-unregistered", action="store_true"); p.set_defaults(func=cmd_plan_physiology)

    # Overlays
    p=sp.add_parser("overlay-behavior"); p.add_argument("--blinder-root", required=True); p.set_defaults(func=cmd_overlay_behavior)
    p=sp.add_parser("overlay-physiology"); p.add_argument("--blinder-root", required=True); p.set_defaults(func=cmd_overlay_physiology)
    p=sp.add_parser("overlay-aliquot"); p.add_argument("--blinder-root", required=True); p.set_defaults(func=cmd_overlay_aliquot)

    # Experimenter receipts
    p=sp.add_parser("inject-scan"); p.add_argument("--experimenter-root", required=True); p.add_argument("--animal-id", required=True); p.add_argument("--stage", required=True); p.add_argument("--session"); p.add_argument("--qr-payload"); p.add_argument("--photo"); p.set_defaults(func=cmd_inject_scan)

    # Reconcile
    p=sp.add_parser("reconcile-usage"); p.add_argument("--blinder-root", required=True); p.add_argument("--experimenter-root", required=True); p.set_defaults(func=cmd_reconcile_usage)

    # Anatomy
    p=sp.add_parser("blind-anatomy"); p.add_argument("--blinder-root", required=True); p.add_argument("--experimenter-root", required=True); p.add_argument("--input-root", required=True); p.add_argument("--allow-missing-index", action="store_true"); p.add_argument("--seal", action="store_true"); p.set_defaults(func=cmd_blind_anatomy)
    p=sp.add_parser("verify-anatomy-blinded"); p.add_argument("--experimenter-root", required=True); p.set_defaults(func=cmd_verify_anatomy_blinded)

    # Provenance
    p=sp.add_parser("record-derivative"); p.add_argument("--experimenter-root", required=True); p.add_argument("--parent", required=True); p.add_argument("--child", required=True); p.add_argument("--note"); p.set_defaults(func=cmd_record_derivative)

    # Post-hoc bundle
    p=sp.add_parser("package-unblinding"); p.add_argument("--blinder-root", required=True); p.add_argument("--experimenter-root", required=True); p.add_argument("--out", required=True); p.set_defaults(func=cmd_package_unblinding)
    p=sp.add_parser("verify-posthoc"); p.add_argument("--bundle", required=True); p.add_argument("--blinder-root"); p.add_argument("--experimenter-root"); p.set_defaults(func=cmd_verify_posthoc)

    # Audit viewer
    p=sp.add_parser("audit-show"); p.add_argument("--root", required=True); p.add_argument("--action"); p.add_argument("--animal"); p.add_argument("--stage"); p.add_argument("--since"); p.add_argument("--until"); p.add_argument("--grep"); p.add_argument("--tail", type=int); p.set_defaults(func=cmd_audit_show)

    args = ap.parse_args(); args.func(args)

if __name__ == "__main__":
    main()
