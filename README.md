<p align="center">
  <img src="docs/assets/evidence-lane-logo.png" alt="Evidence Lane" height="88" />
  <img src="plugins/evidence-lane-plugin/assets/evidence-lane-icon.png" alt="Evidence Lane cube icon" height="88" />
</p>

<p align="center">
  <a href="docs/QUICKSTART.md">Quickstart</a> · <a href="docs/INSTALL.md">Install</a> · <a href="docs/PROJECT_SETUP.md">Project setup</a> · <a href="docs/WORKFLOW_GUIDE.md">Workflows</a> · <a href="docs/STUDIO.md">Studio</a> · <a href="docs/INTEGRATIONS.md">Integrations</a> · <a href="docs/SDK_AND_MCP.md">SDK & MCP</a> · <a href="docs/TROUBLESHOOTING.md">Troubleshooting</a> · <a href="docs/RELEASES.md">Releases</a> · <a href="docs/PRIVACY.md">Privacy</a> · <a href="docs/SECURITY.md">Security</a> · <a href="docs/CONTRIBUTORS.md">Contributors</a>
</p>


# Evidence Lane

**Keep the project connected after the conversation moves on.**

Evidence Lane is a persistent project system for Codex. It connects the work a person asks for with the sources that support it, the current Plan, the tools allowed to run, the results that were produced, and the checks that explain whether the work is ready to continue.

It is designed for long-running projects where a new task or context window should not force the user to reconstruct the project from memory.

## The problem it solves

An AI response can look complete while the project around it remains fragmented. Source files live in one place, unfinished tasks in another, decisions in chat history, tools in a local runtime, and published work in Git or a deployment. When those pieces drift apart, the user becomes the integration layer.

Evidence Lane keeps those parts connected while preserving their different purposes:

- **Sources** retain where information came from and when it was observed.
- **The Plan** shows completed work, the one current task, and what comes next.
- **Project memory** helps retrieve relevant prior context without replacing source evidence.
- **Lessons** record reusable procedures only after verified work.
- **Tools and connections** remain scoped to the selected project and operation.
- **Results and checks** stay linked to the work that produced them.
- **Studio** provides a visible, read-only Windows view of the connected project.

## What changes for the user

Instead of asking a new task to rediscover the whole project, the user can return to the selected project and ask:

- What is current?
- Which sources support this task?
- What has already been completed?
- What is still proposed or blocked?
- Which tools are ready for this operation?
- What changed after the last steer?
- Where should recovery begin if a client or process stopped?

Evidence Lane answers those questions through attributed project records. It does not treat a polished answer, a passing test, a commit, a deployment, or an installation as a human decision by itself.

## How the product is used

1. **Open or resume a project** from Codex Desktop.
2. **Choose persistent project storage** that is separate from the source folder.
3. **Bring in selected sources** such as code, documents, spreadsheets, BI files, PDFs, images, research, artifacts, or approved custom data.
4. **Choose the workflow** that matches the intention: understand, plan, execute, connect, continue, or recover.
5. **Inspect or steer the Plan** while completed history remains visible.
6. **Carry out bounded work** through the tools and providers eligible for the current operation.
7. **Inspect the result and its checks** before the project moves forward.
8. **Continue, hand off, close, or recover** at an explicit boundary.

The [Workflow Guide](docs/WORKFLOW_GUIDE.md) explains the complete set of 24 public workflows.

## The Windows Studio

Studio is the local read-only observer. It shows the selected project's Plan, jobs, evidence, workers, toolchains, connections, learning, compute, and diagnostics. Users navigate and inspect in Studio; project changes remain directed through Codex.

Studio is a Windows application installed with the shared local engine and toolchain. It is not the public Vercel website. See [Windows Studio](docs/STUDIO.md).

## Supported environment

The current product targets a persistent local Windows PC using Codex Desktop Stable or Beta. The default shared installation root is `C:\Apps\EvidenceLaneStudio`. Each project uses a separately selected durable state root.

CPU is the baseline compute route. NVIDIA CUDA and DirectML are used only when the selected operation and measured host compatibility allow them. Optional services and external connectors require their own configuration and bounded grants.

## Current release

The current published release is **v4.0.7**, bound to commit [`d006c95a07c0812f30c5ec69efda74821d711f84`](https://github.com/rathee000001/evidence_lane_plugin/commit/d006c95a07c0812f30c5ec69efda74821d711f84).

- Immutable tag: [`evidence-lane-v4.0.7-bundle-977fb5ec2702ff9d`](https://github.com/rathee000001/evidence_lane_plugin/releases/tag/evidence-lane-v4.0.7-bundle-977fb5ec2702ff9d)
- Published component archives: 12
- Bound archive bytes: 7,083,785,294
- Asset-set SHA-256: `977fb5ec2702ff9dcc0c804cc4d17881f2c90400dbbbf9597f73980d370389cd`

A published source release, a manager installation, an active local runtime, and a verified project operation establish different facts. Follow the [Installation](docs/INSTALL.md) and [Releases](docs/RELEASES.md) guides instead of inferring runtime readiness from a version label.

## Start here

- [Quickstart](docs/QUICKSTART.md) — the shortest path from installation to an inspectable project.
- [Installation](docs/INSTALL.md) — supported plugin-first installation and upgrade behavior.
- [Project Setup](docs/PROJECT_SETUP.md) — choose source and state roots and connect the first sources.
- [Workflow Guide](docs/WORKFLOW_GUIDE.md) — find the workflow that matches the request.
- [Windows Studio](docs/STUDIO.md) — understand the read-only observer.
- [Integrations](docs/INTEGRATIONS.md) — distinguish connections, providers, tools, and transports.
- [Troubleshooting](docs/TROUBLESHOOTING.md) — identify which state failed before retrying.
- [Contributors and Provenance](docs/CONTRIBUTORS.md) — project authorship, AI assistance, documentation sources, and open-source acknowledgements.

## Honest boundaries

- Evidence Lane does not grant access to a source, service, repository, or account.
- A listed capability is not proof that its dependency, credential, model, license, or device is ready.
- Studio navigation cannot change a project.
- A source snapshot does not prove that the source is still unchanged today.
- An outgoing task packet is not proof that the receiving task accepted it.
- Recovery preserves uncertainty; it does not relabel failed or unknown work as successful.
- The public website explains the product but is not connected to a user's local engine or project.

## License

Evidence Lane's original source and documentation are available under the [MIT License](LICENSE.md). Third-party software, models, fonts, assets, services, and marks retain their own licenses and terms. See [Third-party Notices](docs/THIRD_PARTY_NOTICES.md).
