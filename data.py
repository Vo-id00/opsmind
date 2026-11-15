# Simulated enterprise data for the POC (replace with real ITSM / monitoring later)

INCIDENTS = {
    "INC001": {
        "title": "Payments portal returning HTTP 503",
        "severity": "P1",
        "description": "Customers cannot complete checkout. Started 10:42 UTC. Error rate 78%.",
        "signals": {
            "application": [
                "10:41 deployment of payments-api v2.8.1 to production",
                "10:44 ERROR timeout acquiring DB connection from pool (pool 50/50 in use)",
                "10:45 HTTP 503 rate 78%",
                "Thread dump: 212 threads waiting on the connection pool",
            ],
            "infrastructure": [
                "payments-api pods: CPU 35%, memory 61% (normal)",
                "Kubernetes nodes healthy; pods only failing readiness checks",
            ],
            "network": [
                "App-to-DB latency 2 ms (normal)",
                "Brief 120 ms CDN latency spike at 10:30, already resolved",
                "No packet loss, no firewall changes",
            ],
            "database": [
                "Active connections 498/500",
                "Most sessions are 'idle in transaction' from payments-api",
                "DB CPU 22%, no slow queries, no lock contention",
            ],
            "security": [
                "No suspicious logins",
                "WAF traffic normal, no attack signatures",
            ],
        },
        "post_fix": [
            "payments-api rolled back to v2.8.0 and restarted",
            "HTTP 503 rate 0.2%, checkout success 99.8%",
            "DB connections 61/500, connection pool 8/50",
        ],
    },
    "INC002": {
        "title": "Orders API very slow (p95 latency 14 s)",
        "severity": "P2",
        "description": "Order submission is timing out for many users since 02:10 UTC.",
        "signals": {
            "application": [
                "p95 latency 14 s, normal is 0.4 s",
                "ERROR database write timeout in orders-service",
                "No deployments in the last 5 days",
            ],
            "infrastructure": [
                "orders-db-vm: /var/log is 98% full",
                "logrotate job has been failing for 3 days",
                "VM CPU and memory normal",
            ],
            "network": ["Latency and throughput normal", "No packet loss"],
            "database": [
                "WARNING could not write temp/WAL file: no space left on device",
                "Query load normal, no locks",
            ],
            "security": ["No anomalies", "No unusual access patterns"],
        },
        "post_fix": [
            "Old logs cleared on orders-db-vm, /var/log now 41%",
            "p95 latency 0.4 s, no write timeouts",
        ],
    },
}

HISTORY = [
    {"id": "HIST-101", "summary": "Checkout 503 right after release v2.5.0",
     "cause": "Connection leak introduced in the new release exhausted the DB pool",
     "fix": "Rolled back the release and restarted pods"},
    {"id": "HIST-087", "summary": "API slow, database write timeouts",
     "cause": "Log partition full because logrotate was failing",
     "fix": "Cleared old logs and repaired the logrotate config"},
    {"id": "HIST-064", "summary": "Intermittent timeouts to CDN",
     "cause": "Provider network blip",
     "fix": "No action, resolved itself"},
    {"id": "HIST-032", "summary": "Spike in login failures",
     "cause": "Credential stuffing attack",
     "fix": "Blocked offending IPs and enforced MFA"},
]

RUNBOOKS = {
    "DB connection pool exhausted": "Check for a recent deployment. If a release is the trigger, roll it back first, then restart the service to release leaked connections.",
    "Disk full on database VM": "Clear rotated and old logs, verify logrotate config, confirm free space above 30%.",
    "High 503 rate": "Check app errors, then database, then network. Roll back the latest change if it correlates with the start time.",
}
