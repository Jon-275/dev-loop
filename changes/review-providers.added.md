- **Review: providers.** Gemini and any OpenAI-compatible API (Reflection is a built-in
  preset) review with the standard library only; a project overrides a reviewer's model,
  endpoint, key variable and dimensions in `[review.reviewers.NAME]` and writes its lens in
  `review.lenses_dir`. Markers and status names match gtm_toolkit's, so its threads keep
  counting.
