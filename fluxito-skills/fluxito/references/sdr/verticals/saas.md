# Vertical accelerator: SaaS / subscription

Use for subscription or freemium/PLG products. Adapt to the actual activation path.

**Primary conversions:** `subscribe` (paid plan) and often `trial_start`; `sign_up` is top-of-funnel.
**Core events:** `sign_up → login → trial_start → feature_used (activation) → subscribe`,
plus `cancel_subscription` (churn).

**Proof params:**
- `subscribe`: `plan_name`, `value` (first-cycle), `currency`, `billing_cycle`.
- `trial_start`: `trial_type`, `plan_name`.
- `feature_used`: `feature_name` (the activation signal that predicts retention).

**KPIs:** trial→paid conversion, activation rate, feature-adoption depth, MRR, churn.

**The big watch-out:** recurring revenue lives server-side. `subscribe` captures
*activation only* — renewals/expansion/churn are warehouse events, not browser
events. Reconcile LTV/ROAS in the warehouse; never infer renewal revenue from
client events. Flag any client-side "renewal" tracking as unreliable.

**Cross-platform:** `sign_up`/`trial_start`/`subscribe` → Google Ads conversions;
Meta `CompleteRegistration`/`Subscribe` where used.
