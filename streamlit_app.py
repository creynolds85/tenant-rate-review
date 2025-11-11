
import streamlit as st
import pandas as pd
import numpy as np
import re
import requests
from bs4 import BeautifulSoup

st.set_page_config(page_title="Tenant Rate Review – Enhanced", layout="wide")
st.title("Tenant Rate Review & Competitor Check — Enhanced")
st.caption("Upload your tenant CSV, map its columns, add competitors (manual or scraped), and export suggestions.")

# Sidebar policy
st.sidebar.header("Policy Settings")
default_increase_pct = st.sidebar.number_input("Default increase %", 0.0, 50.0, 17.0, 0.5) / 100.0
cadence_months = st.sidebar.number_input("Cadence (months)", 1, 36, 10, 1)
min_inc = st.sidebar.number_input("Minimum % increase", 0.0, 50.0, 8.0, 0.5) / 100.0
max_inc = st.sidebar.number_input("Maximum % increase", 0.0, 100.0, 18.0, 0.5) / 100.0
band_vs_comp = st.sidebar.number_input("Band vs Competitors (±%)", 0.0, 50.0, 3.0, 0.5) / 100.0
band_vs_standard = st.sidebar.number_input("Band vs Standard (±%)", 0.0, 50.0, 5.0, 0.5) / 100.0
rounding = st.sidebar.selectbox("Rounding", ["$1", "$5"], index=1)
round_step = 1 if rounding == "$1" else 5

# Competitors
st.sidebar.header("Competitors (Optional)")
comp_manual_csv = st.sidebar.file_uploader("Upload competitors manual CSV (unit_type,comp_rate)", type=["csv"])
comp_urls_text = st.sidebar.text_area("Or paste competitor URLs (one per line)", value="", height=120)

def round_to_step(val, step=5):
    return int(round(val / step) * step)

@st.cache_data(ttl=600)
def scrape_prices(url: str):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        text = BeautifulSoup(r.text, "lxml").get_text(" ", strip=True)
        nums = re.findall(r"\$\s*([0-9]+(?:\.[0-9]{1,2})?)", text)
        return [float(x) for x in nums]
    except Exception:
        return []

st.subheader("1) Upload Tenant CSV")
tenant_file = st.file_uploader("Upload tenant CSV", type=["csv"])

if not tenant_file:
    st.info("Upload your tenant CSV to continue.")
    st.stop()

raw = pd.read_csv(tenant_file)
st.write("**Preview of uploaded data:**")
st.dataframe(raw.head(20), use_container_width=True)

st.subheader("2) Map Columns")
cols = list(raw.columns)

def guess(sub):
    sub = sub.lower()
    for c in cols:
        if sub in c.lower():
            return c
    return None

col_unit = st.selectbox("Unit Type / Size column", ["<none>"] + cols, index=(cols.index(guess("size")) + 1) if guess("size") in cols else 0)
col_standard = st.selectbox("Monthly Standard Rate column", ["<none>"] + cols, index=(cols.index(guess("standard")) + 1) if guess("standard") in cols else 0)
col_tenant = st.selectbox("Current Tenant Rate column", ["<none>"] + cols, index=(cols.index(guess("tenant rate")) + 1) if guess("tenant rate") in cols else 0)
col_pending = st.selectbox("Pending / Proposed Rate column (if any)", ["<none>"] + cols, index=(cols.index(guess("pending")) + 1) if guess("pending") in cols else 0)
col_months = st.selectbox("Months Since Last Increase column (optional)", ["<none>"] + cols, index=(cols.index(guess("months since")) + 1) if guess("months since") in cols else 0)

if col_unit == "<none>" or col_standard == "<none>" or col_tenant == "<none>":
    st.warning("Please map Unit Type/Size, Monthly Standard Rate, and Current Tenant Rate.")
    st.stop()

def to_num(s):
    return pd.to_numeric(s, errors="coerce")

df = pd.DataFrame({
    "unit_type": raw[col_unit].astype(str) if col_unit != "<none>" else "",
    "current_standard_rate": to_num(raw[col_standard]) if col_standard != "<none>" else np.nan,
    "current_tenant_rate": to_num(raw[col_tenant]) if col_tenant != "<none>" else np.nan,
    "pending_tenant_rate": to_num(raw[col_pending]) if col_pending != "<none>" else np.nan,
    "months_since_last_increase": to_num(raw[col_months]) if col_months != "<none>" else np.nan,
})
df["unit_type"] = df["unit_type"].str.replace("\s*x\s*", "x", regex=True).str.strip().str.lower()

st.subheader("3) Competitors")
comp_by_unit = {}

# Manual competitor CSV
if comp_manual_csv is not None:
    try:
        cdf = pd.read_csv(comp_manual_csv)
        st.write("Manual competitor table:")
        st.dataframe(cdf, use_container_width=True)
        cdf["unit_type"] = cdf["unit_type"].astype(str).str.replace("\s*x\s*", "x", regex=True).str.strip().str.lower()
        comp_by_unit = cdf.groupby("unit_type")["comp_rate"].mean().to_dict()
    except Exception as e:
        st.error(f"Could not read competitor CSV: {e}")

# Scrape fallback
urls = [u.strip() for u in comp_urls_text.splitlines() if u.strip()]
global_avg = np.nan
if urls:
    all_prices = []
    with st.spinner("Scraping competitor URLs..."):
        for u in urls:
            prices = scrape_prices(u)
            all_prices.extend(prices)
    st.write(f"Scraped {len(all_prices)} price points from {len(urls)} URLs.")
    if all_prices:
        global_avg = float(np.mean(all_prices))

for ut in df["unit_type"].unique():
    if ut not in comp_by_unit:
        comp_by_unit[ut] = global_avg

st.subheader("4) Generate Suggestions")

def suggest(row):
    cur = row["current_tenant_rate"]
    std = row["current_standard_rate"]
    pend = row["pending_tenant_rate"]
    months = row["months_since_last_increase"]
    unit = row["unit_type"]

    # Base target per cadence
    base_inc = default_increase_pct
    if not np.isnan(months) and months < cadence_months:
        base_inc = max(min_inc, default_increase_pct / 2)

    target = cur * (1 + base_inc)
    target = max(cur * (1 + min_inc), min(cur * (1 + max_inc), target))

    rationale = []
    comp_avg = comp_by_unit.get(unit, np.nan)
    if not np.isnan(comp_avg):
        rationale.append(f"Comp avg for '{unit}' ≈ ${comp_avg:.0f}.")
        comp_max = comp_avg * (1 + band_vs_comp)
        comp_min = comp_avg * (1 - band_vs_comp)
        if target > comp_max:
            rationale.append(f"Above comp ceiling ${comp_max:.0f}; capping.")
            target = comp_max
        if target < comp_min:
            rationale.append(f"Below comp floor ${comp_min:.0f}; raising.")
            target = max(target, comp_min)
    else:
        rationale.append("No competitor data; using policy caps/standard.")

    if not np.isnan(std):
        std_cap = std * (1 + band_vs_standard)
        if target > std_cap:
            rationale.append(f"Over standard cap ${std_cap:.0f}; capping.")
            target = std_cap

    target = round_to_step(target, round_step)

    if not np.isnan(pend):
        if target > pend * 1.01:
            action = "increase"
        elif target < pend * 0.99:
            action = "decrease"
        else:
            action = "keep"
        rationale.append(f"Pending ${pend:.0f} → Suggested ${target:.0f} ⇒ {action}.")
    else:
        action = "set"
        rationale.append(f"No pending; set ${target:.0f}.")

    return pd.Series({"suggested_rate": target, "action": action, "rationale": " ".join(rationale)})

out = pd.concat([df, df.apply(suggest, axis=1)], axis=1)
st.dataframe(out, use_container_width=True)

csv = out.to_csv(index=False).encode("utf-8")
st.download_button("Download Suggestions CSV", data=csv, file_name="rate_suggestions.csv", mime="text/csv")

st.caption("Tip: For accuracy, provide a manual competitor CSV (unit_type,comp_rate). Scraping is basic and may miss JS-rendered prices.")
