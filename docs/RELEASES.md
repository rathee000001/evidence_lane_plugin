<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Releases and qualification

A release is an exact relationship among source, tag, package assets, installation records, and runtime observations. Those facts should not be collapsed into one “version installed” statement.

## v4.0.9 — corrected experimental closeout

- Tag: [`evidence-lane-v4.0.9-bundle-977fb5ec2702ff9d`](https://github.com/rathee000001/evidence_lane_plugin/releases/tag/evidence-lane-v4.0.9-bundle-977fb5ec2702ff9d)
- Component archives: 12
- Total archive bytes: 7,083,785,294
- Asset-set SHA-256: `977fb5ec2702ff9dcc0c804cc4d17881f2c90400dbbbf9597f73980d370389cd`
- Source manifest SHA-256: `b3e4acd168553fd3909dfb219ba19ddbb32e1fdc67883e8c9b9d2656ab5706bd`

This release preserves the project as an incomplete experimental snapshot while the maintainer focuses on securing employment. It publishes the current website and Windows Studio interface, pairs that interface into the plugin, and prevents ordinary MCP reconnection from repeatedly opening or foregrounding Studio. The larger implementation Plan is preserved for a possible later resume.

The release is not production-ready and should not be relied on for critical or unattended work. Its exact tag, package assets, local installation, and observed runtime remain separate facts.

## v4.0.8 — superseded experimental prerelease

v4.0.8 published the intended source and assets, but its installation self-test still required the earlier nine-file Studio bundle. The v4.0.8 installer rejected the new 25-file hash-bound Studio bundle and restored v4.0.7 without changing project data. Use v4.0.9, which corrects that verifier contract. Do not install v4.0.8.

## v4.0.7

- Commit: [`d006c95a07c0812f30c5ec69efda74821d711f84`](https://github.com/rathee000001/evidence_lane_plugin/commit/d006c95a07c0812f30c5ec69efda74821d711f84)
- Tag: [`evidence-lane-v4.0.7-bundle-977fb5ec2702ff9d`](https://github.com/rathee000001/evidence_lane_plugin/releases/tag/evidence-lane-v4.0.7-bundle-977fb5ec2702ff9d)
- Component archives: 12
- Total archive bytes: 7,083,785,294
- Asset-set SHA-256: `977fb5ec2702ff9dcc0c804cc4d17881f2c90400dbbbf9597f73980d370389cd`
- Source manifest SHA-256: `e3d38d2fb0eb6667f120b56bbf42d4093677e34b2d94b12d74f5a2bca12bda0c`

## What changed

v4.0.7 completes the current published source release across the plugin, persistent engine, Windows Studio, project lanes, user workflows, Hook pipeline, exact release binding, and public website baseline. It restores 24 separate business workflows, keeps project records separately owned, makes source-specific records appear only when used, and replaces unreadable row-dump diagrams with logical traversal maps.

It also hardens the 48 visible Hook rows with bounded output, redaction, deliver-once handling, event-specific timeouts, and trusted-disabled installation.

## Qualification evidence

For the exact release commit:

- governed branch and main CI passed;
- CodeQL passed;
- final Codex Security scan reported no findings;
- all current skills passed Skill Creator validation;
- Plugin Creator and Review Agent validation passed;
- bounded Hook, graph/materialization, package/release, Mermaid, and Graphviz checks passed;
- production website dependency audit reported no known vulnerabilities;
- all 51 deployed website routes returned HTTP 200;
- Vercel reported no runtime errors in the post-deployment check;
- every published release asset matched its bound name, size, and GitHub SHA-256 digest.

## Qualification boundaries

Source and package checks do not prove a user's local installation. Manager installation does not prove first detection completed. A valid release receipt does not prove a project operation ran. Native prompts, project registration, Hook UI state, provider execution, Studio binding, and recovery need their own readback.

## Later work

The coordinated Studio Cosmic Observatory redesign, project-card cover contract, final installed-native qualification, and later documentation-driven website refinements are separate downstream work until their own commit, release, installation, and verification are complete.
