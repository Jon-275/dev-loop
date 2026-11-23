- **`devloop feedback`.** Collects what maintainers did with review findings into the
  `review.feedback` store: a resolved finding thread with a member's labelled reply
  (`labels.prefixes`, such as `accepted:` or `rejected:`) becomes a record, and a finding
  rejected three times is suppressed (never a P1). Nothing is written when any read fails,
  and an unreadable store is never overwritten. Ported from gtm_toolkit.
