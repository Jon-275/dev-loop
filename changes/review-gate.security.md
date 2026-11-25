- **`review.yml` can hold a model review until a maintainer approves it.** With
  `gate: members` a pull request from the owner, a member, a collaborator or a trusted
  bot is reviewed as before; anyone else's waits, its status pending, until a maintainer
  adds the `ai-review-approved` label, which reviews that head once. A stranger can no
  longer spend the project's key or have their text sent to a reviewer by opening a pull
  request. The default (no gate) is unchanged.
