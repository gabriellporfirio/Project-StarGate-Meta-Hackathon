# M4c calls (development log)

Attempt limit per provider: 5. One row per send. States: planned (written before a send is allowed), sent (a reply or HTTP status came back), uncertain (a timeout, a dropped connection, or anything where the request may have reached the provider). Planned and uncertain both count as used until the owner reconciles them, and an uncertain row is never given back.

| id | time | provider | model | where | status | prompt | completion | state |
|---|---|---|---|---|---|---|---|---|
