# Evidence Lane Studio

The persistent engine serves this React application on its protected local Studio origin. The frontend uses the admitted source design system's glass shells, pills, icon orbs, cube, logo, layout measurement and theme directly, with v4 project/Plan/job bindings. `src/design-system` contains the admitted components; `src/App.tsx`, `src/views.tsx` and `src/forms.tsx` connect them to the engine.

Build from the repository root with `python scripts/build_studio.py`. Install frontend development dependencies with `npm ci --ignore-scripts` in this directory first. React, React DOM, TypeScript and Vite are pinned to the admitted design-system source lock. The build writes to the verified `.work/studio-dist` directory and copies a hash-declared static asset set into the Python package. End users consume the built assets; they do not run a frontend development server.

The browser exchanges a single-use launch ticket for a separate owner cookie and CSRF token. MCP clients use their own engine connection. The UI never receives a native MCP bearer credential. Reads are bounded registry actions; commands retain the engine's existing project writer and grant checks. The public localhost page contains no project data until authenticated.

The four inner containers are the project rail, persistent context header, changing content panel, and workflow bar. Narrow windows retain the rail and use horizontal scrolling for workflow pills. Keyboard focus and reduced-motion preferences are supported. All counters and states come from the current engine snapshot. Unavailable project state is shown as unavailable.

This remains an uninstalled development build. Complete profile operations and the shared end-user runtime are governed by the remaining v4 implementation steps.
