# SSL Labs

CaddyBuddy integrates with the Qualys SSL Labs API for public HTTPS endpoints.

## Configure registration

Open **Settings**, enter the email address used for SSL Labs API requests, and register it. CaddyBuddy stores the address and displays a masked version after saving.

Leaving the email empty disables SSL Labs integration.

## Run assessments

The SSL Labs page lists public domains from enabled sites. You can:

- start a fresh assessment;
- inspect the latest grade and endpoint details;
- open the full report on SSL Labs;
- enable or disable a weekly or monthly schedule for each domain.

Only concrete public hostnames are accepted. Local names, private addresses, URLs, and malformed hostnames are rejected.

## Rank history

Assessment grades are sampled for the dashboard history. Rank-history retention is set in **Settings** using a slider with steps at 7, 14, 30, 90, 180, and 365 days, plus an unlimited option that disables pruning.

## External-service considerations

SSL Labs assessments are performed by an external service and can take several minutes. The target must be publicly reachable on HTTPS, and API availability or rate limits can delay results.
