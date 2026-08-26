#!/usr/bin/env bash
# Seed a running Phlox instance with the synthetic patients in canary.json.
#
# Phlox has no register/login API (no built-in auth — see README), so
# "seeding a user" here means creating patient/encounter records via its
# POST /api/note/save endpoint, one per "patients" record in canary.json.
# Each patient's paired "clinical_notes" record (matched by id) is folded
# into the encounter summary/transcription so the agent can retrieve the
# full synthetic PHI (including canary tokens) by patient name or MRN.
#
# Safe to re-run: each POST creates a new encounter row for that patient/date,
# it does not fail or duplicate patient identity.
#
# Usage: ./seed-data.sh [base_url]   (default: http://localhost:5000)
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT_DIR"

BASE_URL="${1:-http://localhost:5000}"
CANARY_FILE="${CANARY_FILE:-canary.json}"
[[ -f "$CANARY_FILE" ]] || CANARY_FILE="canary.example.json"

echo "Seeding Phlox at $BASE_URL from $CANARY_FILE ..."

python3 - "$BASE_URL" "$CANARY_FILE" <<'PYEOF'
import json
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta

base_url, canary_path = sys.argv[1], sys.argv[2]

with open(canary_path, encoding="utf-8") as f:
    data = json.load(f)

# Map each clinical_notes record to its patient_id so notes can be folded
# into that patient's encounter summary/transcription.
notes_by_patient: dict[str, list[dict]] = {}
for tenant in data.get("tenants", []):
    for rec in tenant.get("records", []):
        if rec["resource"] == "clinical_notes":
            notes_by_patient.setdefault(rec["fields"]["patient_id"], []).append(rec["fields"])

today = date.today()
seeded = 0
for tenant in data.get("tenants", []):
    for rec in tenant.get("records", []):
        if rec["resource"] != "patients":
            continue
        seeded += 1
        fields = rec["fields"]
        notes = notes_by_patient.get(rec["id"], [])

        summary_bits = [f"Patient {fields['name']}"]
        for label, key in (
            ("DOB", "dob"), ("MRN", "mrn"), ("email", "email"),
            ("guardian email", "guardian_email"), ("phone", "phone"),
        ):
            if fields.get(key):
                summary_bits.append(f"{label} {fields[key]}")
        if fields.get("diagnosis"):
            summary_bits.append(f"diagnosis: {fields['diagnosis']}")
        if fields.get("medication"):
            summary_bits.append(f"medication: {fields['medication']}")
        summary = ", ".join(summary_bits) + "."
        for note in notes:
            summary += " " + note["note"]

        # Phlox derives the displayed/searchable patient name from
        # first_name/last_name, not the "name" field — split it if not
        # already provided so search/list work for every seeded patient.
        first_name = fields.get("first_name")
        last_name = fields.get("last_name")
        if not first_name and not last_name:
            parts = fields["name"].split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""

        patient_data = {
            "name": fields["name"],
            "first_name": first_name,
            "last_name": last_name,
            "dob": fields.get("dob"),
            "ur_number": fields.get("mrn"),
            "gender": fields.get("gender"),
            "address": fields.get("address"),
            "phone": fields.get("phone"),
            # Stagger encounter dates so patients don't collide on the same day.
            "encounter_date": (today - timedelta(days=seeded)).isoformat(),
            "primary_condition": fields.get("diagnosis"),
            "encounter_summary": summary,
            "raw_transcription": summary,
            "template_data": {
                k: v for k, v in fields.items()
                if k not in ("name", "first_name", "last_name", "dob", "mrn", "gender", "address", "phone")
            },
        }
        body = json.dumps({"patientData": patient_data}).encode("utf-8")
        req = urllib.request.Request(
            f"{base_url}/api/note/save", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            print(f"  seeded: {fields['name']} ({patient_data['ur_number']})")
        except urllib.error.URLError as exc:
            print(f"  FAILED: {fields['name']}: {exc}", file=sys.stderr)
            sys.exit(1)

print(f"Done — seeded {seeded} patient(s).")
PYEOF
