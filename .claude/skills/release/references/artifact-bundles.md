# Release a model or report bundle

Use this procedure when a release distributes generated files across channels, such as a model, a report, preview images, and a download site. The project's release procedure defines the channels, checks, and approval boundary.

1. Keep the current release active while staging the candidate. Tie the manifest to a reviewed source revision that contains the approved bytes, or to immutable artifacts produced from that revision. Use a separate activation PR when a download site needs to follow the public release. Give each published version a new tag.
2. Include the artifact names and SHA-256 digests, source revision, guide revision, and the evidence for each platform and capability claimed. Give unsupported or untested platforms their actual status. Check that wrong revisions, mixed versions, and tampered bytes fail verification.
3. Verify the artifacts the user will open, including the report and visual previews. Reuse evidence when its inputs, revision, and scope still apply. For a native application, record the tested build and distinguish a completed run from cancellation, reopen, or print coverage. Keep private files and raw captures out of the release and the PR.
4. Publish the reviewed bundle using the project's authorized process. Fetch the versioned public assets and compare them with the staged bytes before activating the download page. Check the live page, guide links, and downloaded files again after deployment.
5. Rehearse restoring the previous manifest, links, and assets. Keep the previous release reachable, and document the result and any open limitations in the PR and release notes.
