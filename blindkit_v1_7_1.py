#!/usr/bin/env python3
"""
BlindKit v1.7.1 — Two‑root model + Post‑hoc full‑chain verification & packaging

Adds:
  - package-unblinding (blinder side): create a single reviewer bundle ZIP with keys, plans, registry,
    anatomy crossrefs, experimenter receipts, plus reconciliation CSV and a summary report.
  - verify-posthoc (anyone): verify the bundle's internal file hashes and present a concise status.

Requires Python 3.8+. Optional: pillow, qrcode (unchanged from v1.7).
"""

import argparse, csv, datetime, hashlib, json, os, pathlib, random, re, shutil, sys, zipfile
from typing import List, Tuple, Optional

# ---------- helpers ----------
def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1<<20), b""):
            h.update(chunk)
    return h.hexdigest()

def now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat()

# ---------- MINIMAL v1.7 API SURFACE (imports not required here) ----------
# We only implement new commands; v1.7 core lives in separate file. This module can be used standalone for packaging.

def _load_json(p: pathlib.Path, default=None):
    try:
        return json.loads(p.read_text())
    except Exception:
        return {} if default is None else default

def _collect_files(base: pathlib.Path, globs):
    out = []
    for g in globs:
        out += list(base.glob(g))
    return sorted(set(out))

def _safe_rel(base: pathlib.Path, p: pathlib.Path):
    try: return str(p.relative_to(base))
    except Exception: return str(p)

def cmd_package_unblinding(a):
    br = pathlib.Path(a.blinder_root).resolve()
    er = pathlib.Path(a.experimenter_root).resolve()
    out = pathlib.Path(a.out).resolve()

    # Gather inputs
    behavior = br / "configs" / "behavior_plan.json"
    physiology = br / "configs" / "physiology_plan.json"
    registry = br / "labels" / "registry.json"
    a_cross = br / "configs" / "anatomy_crossref.json"
    a_map   = br / "configs" / "anatomy_blind_map.json"

    receipts = sorted((er / "receipts").glob("*.json"))
    e_anat_manifest = er / "configs" / "anatomy_blinded_manifest.json"
    provenance_dir = er / "provenance"

    # Basic checks
    missing = [p for p in [behavior, physiology, registry, a_cross, a_map, e_anat_manifest] if not p.exists()]
    if missing:
        print("[!] Missing required files:")
        for m in missing: print("    -", m)
        print("    You can still build a bundle, but verification will be partial.")
    os.makedirs(out.parent, exist_ok=True)

    # Reconcile registry + receipts into a table
    reg = _load_json(registry, {"entries":[]})
    used_rows = []
    issues = []
    # index by (animal,stage,session,dummy,label_id)
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
            issues.append(f"Receipt {rfile.name}: no matching registry overlay")

    # Build the ZIP
    stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    zip_path = out if out.suffix.lower()==".zip" else out.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        manifest = {"created": now(), "files": []}

        def add_file(path: pathlib.Path, arcname: str):
            if not path.exists(): return
            zf.write(path, arcname=arcname)
            manifest["files"].append({"arcname": arcname, "sha256": sha256_file(path)})

        # Keys & plans (BLINDER)
        add_file(behavior,   "blinder/configs/behavior_plan.json")
        add_file(physiology, "blinder/configs/physiology_plan.json")
        add_file(registry,   "blinder/labels/registry.json")
        add_file(a_map,      "blinder/configs/anatomy_blind_map.json")
        add_file(a_cross,    "blinder/configs/anatomy_crossref.json")

        # Experimenter side artifacts
        add_file(e_anat_manifest, "experimenter/configs/anatomy_blinded_manifest.json")
        for rfile in receipts:
            add_file(rfile, f"experimenter/receipts/{rfile.name}")
        # Provenance JSONs if present
        if provenance_dir.is_dir():
            for pj in sorted(provenance_dir.glob("*.json")):
                add_file(pj, f"experimenter/provenance/{pj.name}")

        # Reconciliation CSV
        import io
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
        summary.write("# BlindKit v1.7.1 — Unblinding Bundle Summary\n")
        summary.write(f"- Created: {now()}\n")
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

        # Final MANIFEST.json with file hashes
        zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2).encode("utf-8"))

    print("[✓] Unblinding bundle created:", zip_path)

def cmd_verify_posthoc(a):
    z = pathlib.Path(a.bundle).resolve()
    if not z.exists():
        raise SystemExit("[!] Bundle not found.")
    with zipfile.ZipFile(z, "r") as zf:
        # Load manifest
        try:
            man = json.loads(zf.read("MANIFEST.json"))
        except Exception:
            raise SystemExit("[!] MANIFEST.json missing or invalid.")
        errors = 0
        for entry in man.get("files", []):
            arc = entry["arcname"]; expected = entry["sha256"]
            data = zf.read(arc)
            h = hashlib.sha256(data).hexdigest()
            if h != expected:
                print(f"[!] Hash mismatch: {arc}")
                errors += 1
        # Read reconciliation if present
        rec_summary = ""
        try:
            rec_csv = zf.read("reports/reconciliation.csv").decode("utf-8", errors="ignore").splitlines()
            # Quick stats
            import csv as _csv, io
            rows = list(_csv.DictReader(io.StringIO("\n".join(rec_csv))))
            matched = sum(1 for r in rows if r.get("matched_in_registry")=="MATCHED")
            rec_summary = f"Receipts: {len(rows)}, matched: {matched}, unmatched: {len(rows)-matched}"
        except Exception:
            rec_summary = "No reconciliation.csv found."
        if errors==0:
            print("[✓] Bundle integrity OK (all internal file hashes match).")
        else:
            print(f"[!] Bundle integrity FAILED with {errors} mismatched file(s).")
        print("Reconciliation summary:", rec_summary)

# ---------- CLI ----------
def main():
    ap = argparse.ArgumentParser(prog="blindkit_v1_7_1", description="Two‑root + post‑hoc verifier (v1.7.1)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("package-unblinding", help="Blinder side: create reviewer bundle ZIP")
    p.add_argument("--blinder-root", required=True)
    p.add_argument("--experimenter-root", required=True)
    p.add_argument("--out", required=True, help="Output .zip path")
    p.set_defaults(func=cmd_package_unblinding)

    p = sp.add_parser("verify-posthoc", help="Anyone: verify unblinding bundle by internal hashes")
    p.add_argument("--bundle", required=True)
    p.set_defaults(func=cmd_verify_posthoc)

    args = ap.parse_args(); args.func(args)

if __name__ == "__main__":
    main()
