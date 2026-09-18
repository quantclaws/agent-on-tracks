# v0.9 stale-revision fixture note
#
# Scenario: review page shows revision R1 as pending; an edit produces R2.
# Approvals bound to R1 must be rejected as stale_revision (AC-FR0294-03,
# AC-FR0308-02). Seed docs carry revision ids `rev-r1` and `rev-r2`.
seed_revisions:
  - rev-r1
  - rev-r2
