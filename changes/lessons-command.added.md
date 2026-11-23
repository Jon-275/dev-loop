- **`devloop lessons`.** Ranks the bug classes review found (seeded in `lessons.seed`,
  counted from accepted findings in `review.feedback` and council notes) and writes the
  lessons page (`lessons.doc`), the top 10 rules into `lessons.agents` between the
  `lessons:start` and `lessons:end` markers, and the patterns every reviewer checks first
  (`review.lessons`). An input that exists but cannot be read writes nothing. Ported from
  gtm_toolkit.
