import csv, io

def csv_totals(content: bytes):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8", "replace")))
    out = []
    for r in reader:
        cols = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
        camp = cols.get("campaign") or cols.get("campaign name") or cols.get("name") or "row"
        def f(*names):
            for n in names:
                if n in cols and cols[n]:
                    return float(str(cols[n]).replace(",", "").replace("%", "") or 0)
            return 0.0
        spend, imp, clicks = f("spend","amount spent","cost"), f("impressions"), f("clicks","link clicks","clicks (all)")
        row = next((o for o in out if o["campaign"] == camp), None)
        if row is None:
            row = {"campaign": camp, "spend": 0.0, "impressions": 0.0, "clicks": 0.0}
            out.append(row)
        row["spend"] += spend; row["impressions"] += imp; row["clicks"] += clicks
    for o in out:
        o["ctr"] = round(o["clicks"] / o["impressions"] * 100, 2) if o["impressions"] else 0
        o["cpc"] = round(o["spend"] / o["clicks"], 2) if o["clicks"] else 0
        o["spend"] = round(o["spend"], 2)
    return out
