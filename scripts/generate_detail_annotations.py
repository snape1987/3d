#!/usr/bin/env python3
"""Turn a filled detailInventory into a per-detail annotation report.

Consumes the JSON produced by `build_detail_inventory.py` (after the agent has
classified each zone stub) plus the authored `object-sculpt-spec.json`, and
writes one entry per detail: what it is (kind + description) and how it is
currently reproduced in the spec (the matched component.localFeatures or
material.localOverrides entry), or a flagged UNMAPPED if `mapsTo.ref` does not
resolve. Reuses the spec's own gate logic (`validate_sculpt_spec._detail_link_keys`)
to decide what counts as mapped, so this report never disagrees with the
strict-quality gate about which details are linked.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_sculpt_spec import _detail_link_keys  # noqa: E402


def load_details(payload: dict[str, Any]) -> list[dict[str, Any]]:
    inventory = payload.get("detailInventory", payload)
    details = inventory.get("details", [])
    if not isinstance(details, list):
        raise ValueError("detailInventory.details must be a list")
    return details


def resolve_reproduction_text(spec: dict[str, Any], ref: str) -> str | None:
    for comp in spec.get("componentTree", []):
        if not isinstance(comp, dict):
            continue
        cid = comp.get("id")
        if isinstance(cid, str) and ref == cid:
            return f"component '{cid}' ({comp.get('role', '')})"
        for feat in comp.get("localFeatures", []) or []:
            if isinstance(feat, str):
                if ref in (feat, f"{cid}/{feat}"):
                    return f"component '{cid}'.localFeatures: \"{feat}\""
            elif isinstance(feat, dict) and isinstance(feat.get("id"), str):
                fid = feat["id"]
                if ref in (fid, f"{cid}/{fid}"):
                    return f"component '{cid}'.localFeatures[{fid}]: {json.dumps(feat, ensure_ascii=False)}"
    for mat in spec.get("materials", []):
        if not isinstance(mat, dict):
            continue
        mid = mat.get("id")
        if isinstance(mid, str) and ref == mid:
            return f"material '{mid}'"
        for over in mat.get("localOverrides", []) or []:
            if isinstance(over, dict) and isinstance(over.get("id"), str):
                oid = over["id"]
                if ref in (oid, f"{mid}/{oid}"):
                    rest = {k: v for k, v in over.items() if k != "id"}
                    return f"material '{mid}'.localOverrides[{oid}]: {json.dumps(rest, ensure_ascii=False)}"
    return None


def annotate(details: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    link_keys = _detail_link_keys(spec)
    rows = []
    for detail in details:
        maps_to = detail.get("mapsTo") or {}
        ref = maps_to.get("ref") or ""
        mapped = bool(ref) and ref in link_keys
        reproduced_as = resolve_reproduction_text(spec, ref) if mapped else None
        rows.append(
            {
                "id": detail.get("id", ""),
                "kind": detail.get("kind", ""),
                "description": detail.get("description", ""),
                "region": detail.get("region"),
                "confidence": detail.get("confidence"),
                "mapsTo": maps_to,
                "evidenceRef": detail.get("evidenceRef", ""),
                "status": "mapped" if mapped else "unmapped",
                "reproducedAs": reproduced_as,
            }
        )
    return rows


def render_markdown(rows: list[dict[str, Any]], source_label: str, spec_label: str) -> str:
    lines = ["# Detail Inventory Annotations", "", f"Source: {source_label}", f"Spec: {spec_label}", ""]
    for row in rows:
        lines.append(f"## {row['id']} — {row['kind'] or '(unclassified)'}")
        lines.append(f"- **What it is:** {row['description'] or '(no description)'}")
        if row["region"]:
            lines.append(f"- **Region:** {json.dumps(row['region'])}")
        lines.append(f"- **Confidence:** {row['confidence']}")
        maps_to = row["mapsTo"]
        lines.append(f"- **Maps to:** `{maps_to.get('type', '')}:{maps_to.get('ref', '')}`")
        if row["status"] == "mapped":
            lines.append(f"- **How it's reproduced:** {row['reproducedAs']}")
        else:
            lines.append(
                f"- **How it's reproduced:** ⚠ UNMAPPED — "
                f"'{maps_to.get('ref', '')}' does not match any component/material field"
            )
        if row["evidenceRef"]:
            lines.append(f"- **Evidence:** [{Path(row['evidenceRef']).name}]({row['evidenceRef']})")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("detail_inventory", type=Path, help="Filled detail-inventory JSON (build_detail_inventory.py output)")
    parser.add_argument("--spec", type=Path, required=True, help="object-sculpt-spec.json to resolve mapsTo.ref against")
    parser.add_argument("--out", type=Path, required=True, help="Output report path")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON instead of Markdown")
    parser.add_argument("--force", action="store_true", help="Overwrite existing --out")
    args = parser.parse_args(argv)

    if args.out.exists() and not args.force:
        parser.error(f"{args.out} already exists; use --force to overwrite")

    try:
        payload = json.loads(args.detail_inventory.read_text(encoding="utf-8"))
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        details = load_details(payload)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rows = annotate(details, spec)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.json:
        args.out.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        source_label = payload.get("sourceImage", str(args.detail_inventory))
        markdown = render_markdown(rows, source_label, str(args.spec))
        args.out.write_text(markdown, encoding="utf-8")

    unmapped = [row["id"] for row in rows if row["status"] == "unmapped"]
    if unmapped:
        print(f"warning: {len(unmapped)} unmapped detail(s): {', '.join(unmapped)}", file=sys.stderr)
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
