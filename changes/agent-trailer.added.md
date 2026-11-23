- **Metrics per authoring tool.** `devloop agent --install` adds a `commit-msg` hook that
  appends an `Agent: <tool>/<model>` trailer when `DEVLOOP_AGENT` is set (it never blocks a
  commit), and `devloop metrics` reports each tool's pull requests, time to merge, threads,
  review rounds, rejected share and size. The shared `metrics.yml` runs it weekly; dev-loop
  runs it on itself.
