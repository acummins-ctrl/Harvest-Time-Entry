"""
Harvest Bulk Time Entry Wizard
-------------------------------
A step-by-step web UI for creating many Harvest time entries at once.
Each user enters their own Personal Access Token and Account ID, which
are held only in their browser session - never written to disk or logged.

Run locally for testing:
    pip install streamlit requests pandas
    streamlit run app.py

Deploy for others to use: push this file + requirements.txt to a GitHub
repo, then deploy on Streamlit Community Cloud (share.streamlit.io).
"""

import time
from datetime import date, datetime

import pandas as pd
import requests
import streamlit as st

BASE_URL = "https://api.harvestapp.com/v2"

st.set_page_config(page_title="Harvest Bulk Time Entry", page_icon="🕒", layout="wide")
st.title("🕒 Harvest Bulk Time Entry Wizard")
st.caption(
    "Enter your own Harvest credentials below. They're only kept in this browser "
    "session - not saved, logged, or visible to anyone else."
)

# ---------------------------------------------------------------------------
# Session state setup
# ---------------------------------------------------------------------------
if "verified" not in st.session_state:
    st.session_state.verified = False
if "id_lookup_df" not in st.session_state:
    st.session_state.id_lookup_df = None
if "entries_df" not in st.session_state:
    st.session_state.entries_df = None
if "results" not in st.session_state:
    st.session_state.results = None


def get_headers():
    return {
        "Authorization": f"Bearer {st.session_state.access_token}",
        "Harvest-Account-Id": str(st.session_state.account_id),
        "User-Agent": "Harvest Bulk Time Entry Wizard (internal tool)",
        "Content-Type": "application/json",
    }


def get_all_pages(endpoint, params=None):
    items = []
    url = f"{BASE_URL}/{endpoint}"
    params = params or {}
    while url:
        resp = requests.get(url, headers=get_headers(), params=params)
        resp.raise_for_status()
        data = resp.json()
        key = [k for k in data.keys() if isinstance(data[k], list)][0]
        items.extend(data[key])
        url = data.get("links", {}).get("next")
        params = {}
    return items


def normalize_date(raw_date):
    raw_date = str(raw_date).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw_date, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Step 1: Credentials
# ---------------------------------------------------------------------------
st.header("Step 1: Connect to Harvest")
st.markdown(
    "Get your token and account ID from "
    "[id.getharvest.com/developers](https://id.getharvest.com/developers) "
    "(no admin access needed - any user can generate their own)."
)

col1, col2 = st.columns(2)
with col1:
    access_token = st.text_input("Personal Access Token", type="password", key="access_token")
with col2:
    account_id = st.text_input("Account ID", key="account_id")

if st.button("Connect", type="primary", disabled=not (access_token and account_id)):
    try:
        resp = requests.get(f"{BASE_URL}/users/me", headers=get_headers())
        if resp.status_code == 200:
            me = resp.json()
            st.session_state.verified = True
            st.session_state.user_name = f"{me['first_name']} {me['last_name']}"
            st.success(f"Connected as {st.session_state.user_name}")
        else:
            st.session_state.verified = False
            st.error(f"Couldn't connect ({resp.status_code}): {resp.text}")
    except requests.RequestException as e:
        st.session_state.verified = False
        st.error(f"Connection error: {e}")

if not st.session_state.verified:
    st.stop()

# ---------------------------------------------------------------------------
# Step 2: Discover projects/tasks
# ---------------------------------------------------------------------------
st.header("Step 2: Load your projects and tasks")

if st.button("Fetch my projects & tasks"):
    with st.spinner("Fetching your active project assignments..."):
        try:
            assignments = get_all_pages("users/me/project_assignments", {"is_active": "true"})
            rows = []
            for a in assignments:
                project = a["project"]
                client = a["client"]
                for ta in a.get("task_assignments", []):
                    task = ta["task"]
                    rows.append({
                        "client_name": client["name"],
                        "project_name": project["name"],
                        "project_id": project["id"],
                        "task_name": task["name"],
                        "task_id": task["id"],
                    })
            st.session_state.id_lookup_df = pd.DataFrame(rows)

            # Build the editable entries grid, pre-populated, hours/date blank-ish
            entries = st.session_state.id_lookup_df.copy()
            entries["spent_date"] = date.today().isoformat()
            entries["hours"] = pd.Series([float("nan")] * len(entries), dtype="float64")
            entries["notes"] = ""
            st.session_state.entries_df = entries[
                ["client_name", "project_name", "task_name", "spent_date", "hours", "notes",
                 "project_id", "task_id"]
            ]
            st.success(f"Loaded {len(rows)} project/task combinations.")
        except requests.RequestException as e:
            st.error(f"Error fetching projects: {e}")

if st.session_state.id_lookup_df is None:
    st.stop()

# ---------------------------------------------------------------------------
# Step 3: Fill in hours
# ---------------------------------------------------------------------------
st.header("Step 3: Enter time")
st.markdown(
    "Edit the **spent_date**, **hours**, and **notes** columns below directly. "
    "Leave **hours** blank for any task you don't want to log time for right now - "
    "blank rows are skipped automatically."
)

edited_df = st.data_editor(
    st.session_state.entries_df,
    column_config={
        "project_id": None,  # hide internal columns
        "task_id": None,
        "client_name": st.column_config.TextColumn(disabled=True),
        "project_name": st.column_config.TextColumn(disabled=True),
        "task_name": st.column_config.TextColumn(disabled=True),
        "spent_date": st.column_config.TextColumn(help="Format: YYYY-MM-DD"),
        "hours": st.column_config.NumberColumn(min_value=0.0, max_value=24.0, step=0.01, format="%.2f"),
    },
    num_rows="fixed",
    use_container_width=True,
    height=400,
    key="editor",
)
st.session_state.entries_df = edited_df

# ---------------------------------------------------------------------------
# Step 4: Preview (dry run)
# ---------------------------------------------------------------------------
st.header("Step 4: Preview")

to_create = []
skip_notes = []
for i, row in edited_df.iterrows():
    if pd.isna(row["hours"]) or str(row["hours"]).strip() == "":
        continue
    try:
        hours_value = float(row["hours"])
        if hours_value <= 0:
            skip_notes.append(f"Row {i+1}: hours must be greater than 0")
            continue
    except (ValueError, TypeError):
        skip_notes.append(f"Row {i+1}: '{row['hours']}' isn't a valid number")
        continue

    norm_date = normalize_date(row["spent_date"])
    if not norm_date:
        skip_notes.append(f"Row {i+1}: couldn't parse date '{row['spent_date']}'")
        continue

    payload = {
        "project_id": int(row["project_id"]),
        "task_id": int(row["task_id"]),
        "spent_date": norm_date,
        "hours": hours_value,
    }
    if str(row.get("notes", "")).strip():
        payload["notes"] = str(row["notes"]).strip()
    to_create.append((row["project_name"], row["task_name"], payload))

st.info(f"**{len(to_create)}** entries ready to create.")
if to_create:
    preview_df = pd.DataFrame([
        {"project": p, "task": t, **payload} for p, t, payload in to_create
    ])
    st.dataframe(preview_df, use_container_width=True)

if skip_notes:
    with st.expander(f"{len(skip_notes)} rows with issues (won't be created)"):
        for note in skip_notes:
            st.write(f"- {note}")

# ---------------------------------------------------------------------------
# Step 5: Create
# ---------------------------------------------------------------------------
st.header("Step 5: Create entries in Harvest")
st.warning("This step writes real time entries to Harvest. Double-check Step 4 above first.")

if st.button("Create these entries", type="primary", disabled=not to_create):
    progress = st.progress(0)
    status_area = st.empty()
    created, failed = 0, 0
    results = []

    for i, (project_name, task_name, payload) in enumerate(to_create, start=1):
        resp = requests.post(f"{BASE_URL}/time_entries", headers=get_headers(), json=payload)
        if resp.status_code == 201:
            created += 1
            results.append({"project": project_name, "task": task_name, "status": "Created",
                             "detail": f"{payload['hours']}h on {payload['spent_date']}"})
        else:
            failed += 1
            results.append({"project": project_name, "task": task_name, "status": "FAILED",
                             "detail": resp.text})
        progress.progress(i / len(to_create))
        status_area.text(f"{i}/{len(to_create)} processed...")
        time.sleep(0.3)

    st.session_state.results = pd.DataFrame(results)
    if failed == 0:
        st.success(f"Done. Created {created} entries, 0 failures.")
    else:
        st.error(f"Done. Created {created} entries, {failed} failed - see details below.")

if st.session_state.results is not None:
    st.dataframe(st.session_state.results, use_container_width=True)
