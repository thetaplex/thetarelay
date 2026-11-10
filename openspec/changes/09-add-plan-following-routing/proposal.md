# Proposal: add plan-following routing

Status: deferred. Write the design only after `04-add-matched-baselines` shows the judge beats
matched random routing. If it does not, a stronger signal than the judge is needed, and this is
one candidate.

## Why

The judge asks "is the next step easy?" without knowing what the next step is. When the
frontier's last turn states a plan ("read X, update Y's tests, run them"), the steps that carry
out that plan are more predictable than a judge score suggests.

## What Changes

- New route mode `plan`: after a frontier turn that states a plan, the following steps go to the
  lite tier until the plan's steps are done, a step fails, or a lite step is off-plan. Then the
  next request goes to the frontier tier.
- Opt-in; not part of `hybrid`.

## Capabilities

### New Capabilities

- `plan-routing`: routing on the frontier's stated plan.

### Modified Capabilities

None.

## Impact

- `src/thetarelay/router.py`; needs a way to read a plan from the frontier's text and to tell
  whether a lite step follows it. Both are open problems; the second may need a model.
- Depends on `01-add-step-classification`, `02-fix-failure-detection`.

## Open for review

- How is "off-plan" decided without a model? Candidate: each plan step names a file or command;
  a lite step is on-plan if its target is in the remaining plan steps.
