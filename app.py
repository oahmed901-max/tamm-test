"""TEMPORARY connectivity test: can THIS server query the DOH sick-leave validation service?

One click = one request. Shows exactly what came back. Nothing is stored or logged.
"""
import copy
import re
import time

import requests
import streamlit as st

URL = ("https://www.tamm.abudhabi/services/community/doh/pub/proxy/ms-call/gateway/"
       "TammJourneyDoh-backend/1.0/doh/tools/rslc/getSickLeaveDetails")
MIN_GAP_SECONDS = 5
TIMEOUT_SECONDS = 20
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "EAI-HR-sickleave-check/0.1 (internal HR tool)",  # honest identity, no browser imitation
}


# ── pure helpers (no Streamlit, easy to test) ────────────────
def clean_eid(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def clean_ref(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).upper()


def valid_inputs(ref: str, eid: str):
    problems = []
    if not re.fullmatch(r"RSLC-\d{4}-\d{6,10}", ref):
        problems.append("Reference should look like RSLC-2026-01234567")
    if not re.fullmatch(r"784\d{12}", eid):
        problems.append("Emirates ID should be 15 digits starting with 784 (dashes are fine)")
    return problems


def portal_name(d: dict) -> str:
    parts = [d.get("firstName"), d.get("middleName"), d.get("lastName")]
    return re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip()


def classify(status_code: int, body):
    """-> (code, human message). body is parsed JSON, or None if it was not JSON."""
    if status_code in (401, 403):
        return "BLOCKED", f"The service refused this server (HTTP {status_code})."
    if status_code == 429:
        return "RATE_LIMITED", "The service says too many requests (HTTP 429). Stop and wait."
    if status_code >= 500:
        return "SERVER_ERROR", f"The service had an error (HTTP {status_code})."
    if body is None:
        return "NOT_JSON", f"HTTP {status_code} but the reply is not JSON (could be a block page)."
    details = ((body.get("data") or {}).get("sickLeaveDetails")) if isinstance(body, dict) else None
    if details and details.get("sickLeaveId"):
        return "FOUND", "The certificate was found."
    return "NO_DETAILS", "The service answered normally but returned no certificate details."


def redact(body):
    """Hide the personal download link before showing the reply."""
    out = copy.deepcopy(body)
    try:
        d = out["data"]["sickLeaveDetails"]
        if d and "downloadLink" in d:
            d["downloadLink"] = "[hidden]"
    except (KeyError, TypeError):
        pass
    return out


def call_portal(ref: str, eid: str):
    t0 = time.time()
    r = requests.post(URL, json={"emiratesId": eid, "sickLeaveId": ref, "language": "en"},
                      headers=HEADERS, timeout=TIMEOUT_SECONDS)
    return r, round(time.time() - t0, 2)


# ── page ─────────────────────────────────────────────────────
st.set_page_config(page_title="TAMM connectivity test", page_icon="🧪")
st.title("🧪 TAMM sick leave check — connectivity test")
st.caption("Temporary page. One click sends ONE request from this server to the DOH validation "
           "service and shows exactly what came back. Nothing is saved.")

# Password gate: refuses to run until a password is set in the app's Secrets.
try:
    app_password = st.secrets["APP_PASSWORD"]
except Exception:
    app_password = None
if not app_password:
    st.error("Set APP_PASSWORD in this app's Secrets (Settings → Secrets) before using it. "
             "This page can query real certificates, so it must not be open to everyone.")
    st.stop()
if st.text_input("Password", type="password") != app_password:
    st.stop()

ref_in = st.text_input("Reference Number", placeholder="RSLC-2026-01234567")
eid_in = st.text_input("Emirates ID", placeholder="784-1234-1234567-1")
go = st.button("Send ONE test request", type="primary")

if go:
    last = st.session_state.get("last_call", 0)
    wait = MIN_GAP_SECONDS - (time.time() - last)
    ref, eid = clean_ref(ref_in), clean_eid(eid_in)
    problems = valid_inputs(ref, eid)
    if wait > 0:
        st.warning(f"Slow down: wait {int(wait) + 1} more second(s) between requests.")
    elif problems:
        for p in problems:
            st.warning(p)
    else:
        st.session_state["last_call"] = time.time()
        try:
            resp, secs = call_portal(ref, eid)
        except requests.exceptions.Timeout:
            st.error(f"No answer within {TIMEOUT_SECONDS}s (timeout).")
        except requests.exceptions.RequestException as e:
            st.error(f"Could not reach the service: {type(e).__name__}")
        else:
            try:
                body = resp.json()
            except ValueError:
                body = None
            code, msg = classify(resp.status_code, body)
            c1, c2, c3 = st.columns(3)
            c1.metric("HTTP status", resp.status_code)
            c2.metric("Seconds", secs)
            c3.metric("Verdict", code)
            (st.success if code == "FOUND" else st.info if code == "NO_DETAILS" else st.error)(msg)

            if code == "FOUND":
                d = body["data"]["sickLeaveDetails"]
                st.write({
                    "Reference": d.get("sickLeaveId"), "Emirates ID": d.get("emiratesId"),
                    "Name": portal_name(d), "From": d.get("sickLeaveFromDate"),
                    "To": d.get("sickLeaveToDate"), "Days": d.get("sickLeavePeriod"),
                    "Status": d.get("sickLeaveStatusId"), "Facility": d.get("facilityName"),
                    "Physician": d.get("physicianName"),
                })

            hints = {k: v for k, v in resp.headers.items()
                     if k.lower() in ("content-type", "server", "via", "x-cache", "cf-ray", "retry-after")}
            with st.expander("Reply details (send me a screenshot of this)", expanded=True):
                st.write("Response headers of interest:", hints or "none")
                if body is not None:
                    st.json(redact(body))
                else:
                    st.code(resp.text[:1500])
