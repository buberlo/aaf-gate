# 171 Call limits (V1-2)

Status: implemented and locally tested. Not tested on device. Not released for production.

## Assumption E48-1

Date: 22.11.2026 22:16 Europe/Vienna. The estimate is an **Assumption** without measurement: 20 mails per hour, with calls per mail read 4, draft 1, internal_write 2, and factor 2 for the sliding window of 3600 seconds.

## Pilot 1 start values

| Class | Window (s) | max_calls |
|--------|-------------|-----------|
| read | 3600 | 160 |
| draft | 3600 | 40 |
| internal_write | 3600 | 80 |

After a **pilot week**, values are **reset** from `gate_calls`.

## Limits

E125-1: The call limit does not replace the unattended block (`internal_write` stays capped at `level_L2` without approval).

F429: A policy with `limits` on an older core without limit support yields `invalid_policy`.