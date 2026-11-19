"""
Converter: schedulingbenchmarks.org .txt instance  ->  data_bench/*.csv

Usage:
    python bench_convert.py Instance1.txt

Reads the plain-text benchmark format (SECTION_HORIZON, SECTION_SHIFTS,
SECTION_STAFF, SECTION_DAYS_OFF, SECTION_SHIFT_ON_REQUESTS,
SECTION_SHIFT_OFF_REQUESTS, SECTION_COVER) and writes one CSV per
section into ./data_bench/, augmented with UNIFORM PLACEHOLDER columns
for the TBL fields the benchmark does not carry (wage, commute distance,
emission factor). Placeholders are uniform on purpose -- this phase is
about validating the constraint encoding against the published optima,
not about realistic TBL data. Swap them for sampled/real values later
by editing PLACEHOLDERS below (every synthetic field is set in exactly
one place so the augmentation stays documented and reproducible).
"""

import csv
import os
import sys

PLACEHOLDERS = {
    "wage_per_shift": 100.0,     # uniform economic placeholder
    "commute_km": 10.0,          # uniform one-way commute distance
    "emission_factor": 0.2,      # uniform kg CO2e per km
}


def parse_instance(path):
    sections = {}
    current = None
    with open(path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("SECTION_"):
                current = line
                sections[current] = []
            else:
                sections[current].append(line)
    return sections


def convert(path, out_dir="data_bench"):
    sections = parse_instance(path)
    os.makedirs(out_dir, exist_ok=True)

    # --- horizon.csv ---
    n_days = int(sections["SECTION_HORIZON"][0])
    with open(os.path.join(out_dir, "horizon.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n_days"])
        w.writerow([n_days])

    # --- shifts.csv : shift_id, length_min, cannot_follow (| separated) ---
    with open(os.path.join(out_dir, "shifts.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["shift_id", "length_min", "cannot_follow"])
        for line in sections["SECTION_SHIFTS"]:
            parts = line.split(",")
            shift_id, length = parts[0], parts[1]
            cannot_follow = parts[2] if len(parts) > 2 else ""
            w.writerow([shift_id, length, cannot_follow])

    # --- staff.csv : benchmark fields + uniform TBL placeholders ---
    with open(os.path.join(out_dir, "staff.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["staff_id", "max_shifts", "max_total_min", "min_total_min",
                    "max_consec_shifts", "min_consec_shifts",
                    "min_consec_days_off", "max_weekends",
                    "wage_per_shift", "commute_km", "emission_factor"])
        for line in sections["SECTION_STAFF"]:
            p = line.split(",")
            w.writerow([p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7],
                        PLACEHOLDERS["wage_per_shift"],
                        PLACEHOLDERS["commute_km"],
                        PLACEHOLDERS["emission_factor"]])

    # --- days_off.csv : staff_id, day (one row per staff-day) ---
    with open(os.path.join(out_dir, "days_off.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["staff_id", "day"])
        for line in sections["SECTION_DAYS_OFF"]:
            p = line.split(",")
            for day in p[1:]:
                w.writerow([p[0], day])

    # --- shift_on_requests.csv / shift_off_requests.csv ---
    for section, fname in [("SECTION_SHIFT_ON_REQUESTS", "shift_on_requests.csv"),
                           ("SECTION_SHIFT_OFF_REQUESTS", "shift_off_requests.csv")]:
        with open(os.path.join(out_dir, fname), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["staff_id", "day", "shift_id", "weight"])
            for line in sections.get(section, []):
                w.writerow(line.split(","))

    # --- cover.csv : day, shift_id, requirement, under_weight, over_weight ---
    with open(os.path.join(out_dir, "cover.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "shift_id", "requirement", "under_weight", "over_weight"])
        for line in sections["SECTION_COVER"]:
            w.writerow(line.split(","))

    print(f"Converted {path} -> {out_dir}/ "
          f"({n_days} days, {len(sections['SECTION_STAFF'])} staff, "
          f"{len(sections['SECTION_SHIFTS'])} shift type(s))")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "Instance1.txt"
    convert(src)
