import asyncio
import json
import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from openai import AsyncOpenAI

from data import HISTORY, INCIDENTS, RUNBOOKS

client = AsyncOpenAI(
    base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_KEY"],
)
MODEL = os.environ["AZURE_OPENAI_DEPLOYMENT"]

app = FastAPI(title="OpsMind")
RUNS = {}  # in-memory store, fine for a POC

# ---------- LLM helper ----------
async def ask(system: str, user: str, as_json: bool = False):
    extra = {"response_format": {"type": "json_object"}} if as_json else {}
    r = await client.chat.completions.create(
        model=MODEL,
        reasoning_effort="low",
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        **extra,
    )
    text = r.choices[0].message.content
    return json.loads(text) if as_json else text


def log(run, actor, event):
    run["audit"].append({"t": time.strftime("%H:%M:%S"), "actor": actor, "event": event})

# ---------- Agents ----------
ROLES = {
    "application": "Application agent. You analyse application logs and recent deployments.",
    "infrastructure": "Infrastructure agent. You analyse servers, VMs, containers, CPU, memory and disk.",
    "network": "Network agent. You analyse latency, packet loss, DNS and firewall data.",
    "database": "Database agent. You analyse connections, queries, locks and storage.",
    "security": "Security agent. You look for attacks, suspicious access and policy violations.",
}
SPEC_RULES = (" Reply in at most 3 short sentences: what you see, whether it is likely"
              " the cause or a red herring, and your confidence (low/medium/high).")


async def run_specialist(name, role, inc):
    evidence = "\n".join(inc["signals"][name])
    reply = await ask("You are the " + role + SPEC_RULES,
                      f"Incident: {inc['title']}\n{inc['description']}\n\nSignals:\n{evidence}")
    return name, reply


async def run_knowledge(inc):
    hist = "\n".join(f"{h['id']}: {h['summary']} | cause: {h['cause']} | fix: {h['fix']}" for h in HISTORY)
    books = "\n".join(f"{k}: {v}" for k, v in RUNBOOKS.items())
    reply = await ask(
        "You are the Knowledge agent. Match the incident to past incidents and runbooks."
        " Reply in at most 3 short sentences, citing the IDs or runbook names that match.",
        f"Incident: {inc['title']}\n{inc['description']}\n\nPast incidents:\n{hist}\n\nRunbooks:\n{books}")
    return "knowledge", reply


async def supervisor(inc, findings):
    text = "\n".join(f"[{k}] {v}" for k, v in findings.items())
    return await ask(
        "You are the Supervisor agent. Weigh the specialist findings, ignore red herrings and"
        " pick the single most probable root cause. Return JSON only:"
        ' {"root_cause": str, "confidence": "low|medium|high", "evidence": [str, str, str]}',
        f"Incident: {inc['title']}\n{inc['description']}\n\nFindings:\n{text}", True)


ACTION_POLICY = {  # governance: risk level per action type
    "clear_logs": "low",
    "scale_out": "low",
    "restart_service": "medium",
    "rollback_deployment": "high",
    "failover_db": "high",
}


async def planner(inc, rca):
    return await ask(
        "You are the Planning agent. Create a minimal remediation plan of 1 to 3 actions, in order."
        " Allowed action types: " + ", ".join(ACTION_POLICY) +
        '. Return JSON only: {"actions": [{"type": str, "target": str, "reason": str}]}',
        f"Incident: {inc['title']}\nRoot cause: {json.dumps(rca)}", True)


def govern(actions):
    """Policy check: low risk runs automatically, everything else needs a human."""
    out = []
    for i, a in enumerate(actions):
        risk = ACTION_POLICY.get(a.get("type"))
        if risk is None:
            status, risk = "blocked", "high"  # unknown action type
        elif risk == "low":
            status = "auto_approved"
        else:
            status = "pending_approval"
        out.append({"id": i + 1, "type": a.get("type"), "target": a.get("target", ""),
                    "reason": a.get("reason", ""), "risk": risk, "status": status})
    return out


def execute(run, action, approved_by):
    # Simulated for the POC. Replace with Azure Functions / Automation runbooks later.
    action["status"] = "executed"
    action["approved_by"] = approved_by
    log(run, "action", f"Executed {action['type']} on {action['target']} ({approved_by})")


async def settle(run):
    """After actions are decided, validate recovery if everything runnable has run."""
    acts = run["actions"]
    if any(a["status"] == "pending_approval" for a in acts):
        run["status"] = "awaiting_approval"
        return
    if not any(a["status"] == "executed" for a in acts):
        run["status"] = "escalated"
        log(run, "governance", "No action executed, escalated to a human engineer")
        return
    inc = INCIDENTS[run["incident_id"]]
    after = "\n".join(inc["post_fix"])
    run["validation"] = await ask(
        "You are the Validation agent. Based on post-remediation signals, decide whether service"
        ' recovered. Return JSON only: {"recovered": bool, "summary": str}',
        f"Incident: {inc['title']}\n\nPost-fix signals:\n{after}", True)
    run["status"] = "resolved" if run["validation"].get("recovered") else "escalated"
    log(run, "validation", run["validation"].get("summary", ""))
    log(run, "learning", "Incident, actions and outcome saved to the knowledge base (simulated)")

# ---------- API ----------
@app.get("/api/incidents")
def list_incidents():
    return [{"id": k, "title": v["title"], "severity": v["severity"]} for k, v in INCIDENTS.items()]


@app.post("/api/investigate/{incident_id}")
async def investigate(incident_id: str):
    inc = INCIDENTS.get(incident_id)
    if not inc:
        raise HTTPException(404, "Unknown incident")
    run = {"id": uuid.uuid4().hex[:8], "incident_id": incident_id,
           "incident": {"title": inc["title"], "severity": inc["severity"],
                        "description": inc["description"]},
           "audit": [], "status": "investigating"}
    RUNS[run["id"]] = run
    n = sum(len(v) for v in inc["signals"].values())
    log(run, "perception", f"Ingested {n} signals from {len(inc['signals'])} sources")

    results = await asyncio.gather(
        *[run_specialist(n, r, inc) for n, r in ROLES.items()], run_knowledge(inc))
    run["findings"] = dict(results)
    log(run, "reasoning", "Specialist and knowledge agents finished in parallel")

    run["rca"] = await supervisor(inc, run["findings"])
    log(run, "supervisor", "Root cause: " + run["rca"].get("root_cause", ""))

    plan = await planner(inc, run["rca"])
    run["actions"] = govern(plan.get("actions", []))
    log(run, "planning", f"Created plan with {len(run['actions'])} action(s)")

    for a in run["actions"]:
        if a["status"] == "auto_approved":
            execute(run, a, "policy: auto-approved")
        elif a["status"] == "pending_approval":
            log(run, "governance", f"{a['type']} is {a['risk']} risk, waiting for human approval")
        else:
            log(run, "governance", f"Blocked unknown action type: {a['type']}")
    await settle(run)
    return run


@app.post("/api/runs/{run_id}/approve")
async def approve(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run")
    for a in run["actions"]:
        if a["status"] == "pending_approval":
            execute(run, a, "human approval")
    await settle(run)
    return run


@app.post("/api/runs/{run_id}/reject")
async def reject(run_id: str):
    run = RUNS.get(run_id)
    if not run:
        raise HTTPException(404, "Unknown run")
    for a in run["actions"]:
        if a["status"] == "pending_approval":
            a["status"] = "rejected"
    log(run, "human", "Rejected pending actions, escalated to engineer")
    run["status"] = "escalated"
    return run


@app.get("/")
def home():
    return FileResponse("index.html")
