- **A red `Review labels run` job now always means the check could not run.** The verdict
  is the `Review labels` commit status. The job used to fail as well whenever a thread had
  no labelled reply, and each new reply cancelled the run before it, so a commit collected
  failed and cancelled jobs even after its status turned green. Now `devloop labels` exits
  0 whenever it posted a verdict, runs for one pull request overlap instead of cancelling
  each other, and each run reads the threads again after posting, so the last one to
  finish leaves the status that matches them. A bot's comment events no longer start a run
  each: its review event starts one.
