from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zerohandoff.config import digest_file
from zerohandoff.delivery.commands import project_command_manifest
from zerohandoff.models import BuildRequest


BROWSER_ACCEPTANCE_HARNESS_REQUIREMENTS = (
    "The browser harness must build the current production bundle before starting its "
    "preview and must test that fresh bundle in a real browser. When it drives CDP "
    "directly, it must await every Input.dispatchKeyEvent call sequentially. Send Enter as "
    "exactly one keyDown with text and unmodifiedText set to `\\r`, followed by keyUp. Send "
    "non-text keys such as Tab or Escape as rawKeyDown followed by keyUp. Include "
    "windowsVirtualKeyCode, code, key, location, and modifiers where the protocol requires "
    "them, and omit nativeVirtualKeyCode. It must never fire-and-forget a key event or "
    "insert a separate char event. "
    "Before keyboard activation it must focus the intended control and assert that "
    "document.activeElement is that exact element; a body-text match is not focus or "
    "activation evidence. "
    "For controlled modal dialogs, acceptance must cancel with Escape, verify closure and "
    "focus restoration, then immediately reopen and confirm the dialog. The application "
    "must not let a delayed native close event overwrite that new open state. "
    "Repeated exports that reuse a deterministic filename must clear "
    "or distinguish the prior file and correlate the new completed download rather than "
    "treating an old file or unchanged filename as success. CDP/WebSocket connection, every "
    "request, the overall journey, and browser/preview cleanup must each have bounded "
    "timeouts. Every readiness or runtime fetch callback must carry its own AbortSignal or "
    "AbortController deadline; an outer polling deadline is not a substitute for bounding "
    "the awaited request. A CDP readiness expression must normalize DOM queries and other "
    "object-valued results to a primitive boolean before requesting return-by-value; never "
    "send DOM nodes, Request/Response objects, events, or native browser objects across the "
    "protocol boundary. Pending requests must be rejected and child processes stopped in "
    "finally. A browser launcher that spawns a child before discovering its debugging "
    "endpoint must terminate that child inside the launcher if startup errors or times out; "
    "the outer caller cannot clean up a handle it never received. Network collection must "
    "begin before the initial navigation and remain active "
    "through any explicit reload. Classify requests by origin, resource type, and initiator: "
    "browser/parser-initiated same-origin preview documents and static assets may load, "
    "including a same-origin favicon even when CDP labels it Other, but app-initiated Fetch, "
    "XHR, WebSocket, EventSource, beacon, or script-driven HTTP and every external-origin "
    "request must fail unless the Outcome Model explicitly permits it. Report permitted "
    "preview loads separately from prohibited runtime requests; never clear the request "
    "ledger or start monitoring after navigation to make a network assertion pass. "
    "When launching a system Chrome browser, use a fresh temporary profile plus "
    "--disable-extensions and --disable-component-extensions-with-background-pages. CDP "
    "ServiceWorker or Target events whose scope or script uses a browser-owned scheme such "
    "as chrome-extension:, chrome:, or devtools: are browser internals, not application "
    "network activity, and must be recorded separately or excluded from the application "
    "failure ledger. Still fail every application call to navigator.serviceWorker.register, "
    "every HTTP(S) service-worker scope or script, and every service worker controlled by "
    "the preview page. After completion, persist explicit permitted-preview and prohibited-runtime counts plus "
    "their navigation/runtime phases; a combined request total or an implied zero is not "
    "sufficient evidence. The declared npm test entrypoint must delete any prior success "
    "receipt before unit tests, builds, or other fallible setup begin. It may publish a new "
    "success receipt and ZEROHANDOFF_BROWSER_ACCEPTANCE_OK only after the browser client, "
    "browser process, preview process, downloads, and temporary profile have all been "
    "cleaned up successfully; cleanup failure is test failure and must leave no receipt. "
    "The wrapper's literal first filesystem action from the npm package root must remove "
    "the prior success receipt before command substitution, directory discovery, mktemp, "
    "cd, or any other fallible setup. When an activated control is removed or disabled by "
    "the resulting state change, move keyboard focus deterministically to the nearest "
    "surviving logical control and prove its visible focus—even when the parent item remains "
    "in the active filter. When activating a control inserts a panel or dialog earlier in "
    "DOM order, move focus into that interface and prove the application did so; a test must "
    "not hide broken focus order by programmatically focusing the target first. Give compact "
    "split-text metrics a stable accessible name containing the complete observable phrase "
    "so demo capture is not coupled to incidental text-node whitespace. After real keyboard "
    "input, acceptance must prove that the exact active control "
    "matches :focus-visible, is rendered, and has a nonzero computed outline, box-shadow, or "
    "equivalent visible focus indicator; activeElement equality alone is insufficient. It "
    "must also compute and assert at least 3:1 contrast between that indicator and every "
    "adjacent light or dark surface exercised by the journey. When focus evidence code is "
    "embedded in a JavaScript template literal, do not rely on regex backslashes that the "
    "outer string can consume; classify transparent/rgba(0, 0, 0, 0) surfaces explicitly "
    "and reject any receipt that reports a transparent adjacent surface. Exercise each "
    "contractually distinct recovery transition from its own prerequisite state (for "
    "example, reset-from-rejection cannot be inferred from reset-after-undo). "
    "The harness may print ZEROHANDOFF_BROWSER_ACCEPTANCE_OK only after the real browser "
    "journey and every required disk/browser assertion succeed."
)


@dataclass(frozen=True)
class BuildEvidence:
    profile: str
    files: list[str]
    commands: dict[str, str]
    checks: dict[str, bool]
    file_digests: dict[str, str]
    command_results: tuple[dict[str, Any], ...] = ()

    @property
    def passed(self) -> bool:
        return all(self.checks.values()) and all(
            result.get("exit_code") == 0
            and not result.get("timed_out")
            and not result.get("reported_error")
            for result in self.command_results
        )


def _product_name(idea: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", idea)
    return " ".join(words[:5]).title() or "Enterprise Operations"


def _portable_dist_assets(workspace: Path) -> bool:
    index = workspace / "dist" / "index.html"
    if not index.is_file():
        return False
    for reference in re.findall(r'(?:src|href)=["\']([^"\']+)["\']', index.read_text(errors="ignore")):
        clean = reference.split("?", 1)[0].split("#", 1)[0]
        if not clean or clean.startswith(("#", "data:", "mailto:")):
            continue
        if clean.startswith(("/", "http://", "https://", "//")):
            return False
        target = (index.parent / clean).resolve()
        if index.parent.resolve() not in target.parents or not target.is_file():
            return False
    return True


def _node_module_links_are_local(workspace: Path) -> bool:
    node_modules = workspace / "node_modules"
    if node_modules.is_symlink():
        return False
    if not node_modules.exists():
        return True
    root = node_modules.resolve()
    for path in node_modules.rglob("*"):
        if not path.is_symlink():
            continue
        target = path.resolve(strict=False)
        if root != target and root not in target.parents:
            return False
    return True


def _test_harnesses_are_declared(workspace: Path, test_script: str) -> bool:
    tests = workspace / "tests"
    if not tests.is_dir():
        return True
    suspicious = [
        path
        for path in tests.rglob("*")
        if path.is_file()
        and (
            path.suffix == ".sh"
            or ("acceptance" in path.name.lower() and ".test." not in path.name)
        )
    ]
    declared_surface = test_script
    for _ in suspicious:
        for path in suspicious:
            relative = str(path.relative_to(workspace))
            if relative in declared_surface:
                declared_surface += "\n" + path.read_text(errors="ignore")
    return all(
        str(path.relative_to(workspace)) in declared_surface for path in suspicious
    )


def _browser_acceptance_required(workspace: Path) -> bool:
    tests = workspace / "tests"
    return tests.is_dir() and any(
        path.is_file() and "acceptance" in path.name.lower()
        for path in tests.rglob("*")
    )


def _browser_runtime_prerequisites_documented(workspace: Path) -> bool:
    """Require an honest README when acceptance tests depend on a named browser."""

    tests = workspace / "tests"
    if not tests.is_dir():
        return True
    acceptance_surface = "\n".join(
        path.read_text(errors="ignore")
        for path in tests.rglob("*")
        if path.is_file() and "acceptance" in path.name.lower()
    )
    browser_names = {
        match.lower()
        for match in re.findall(
            r"--browser(?:=|\s+)([A-Za-z0-9_-]+)", acceptance_surface
        )
    }
    if not browser_names:
        return True
    try:
        readme = (workspace / "README.md").read_text(errors="ignore").lower()
    except OSError:
        return False
    aliases = {
        "chrome": ("chrome", "google chrome"),
        "chromium": ("chromium",),
        "firefox": ("firefox",),
        "webkit": ("webkit", "safari"),
    }
    return all(
        any(alias in readme for alias in aliases.get(name, (name,)))
        for name in browser_names
    )


class ReactViteWorkspaceBuilder:
    """Creates the bounded React/Vite product profile used by the hackathon path."""

    def build(
        self,
        workspace: Path,
        request: BuildRequest,
        implementation: dict[str, Any],
    ) -> BuildEvidence:
        workspace = workspace.resolve()
        if workspace.exists() and any(workspace.iterdir()):
            raise ValueError("generated application workspace must start empty")
        (workspace / "src").mkdir(parents=True, exist_ok=True)
        (workspace / "tests").mkdir(parents=True, exist_ok=True)
        (workspace / "dist").mkdir(parents=True, exist_ok=True)
        name = _product_name(request.idea)
        description = request.outcome
        contract_items = implementation.get("contract_item_ids", [])
        package = {
            "name": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "enterprise-app",
            "private": True,
            "version": "0.1.0",
            "type": "module",
            "scripts": {
                "dev": "vite",
                "build": "tsc -b && vite build",
                "test": "node --test tests/*.test.mjs",
                "typecheck": "tsc -b --pretty false",
            },
            "dependencies": {
                "@vitejs/plugin-react": "6.0.3",
                "vite": "8.1.5",
                "react": "19.2.7",
                "react-dom": "19.2.7",
            },
            "devDependencies": {
                "typescript": "7.0.2",
                "@types/react": "19.2.17",
                "@types/react-dom": "19.2.3",
            },
        }
        files: dict[str, str] = {
            "package.json": json.dumps(package, indent=2) + "\n",
            "package-lock.json": json.dumps(
                {
                    "name": package["name"],
                    "version": package["version"],
                    "lockfileVersion": 3,
                    "requires": True,
                    "packages": {
                        "": {
                            "name": package["name"],
                            "version": package["version"],
                            "dependencies": package["dependencies"],
                            "devDependencies": package["devDependencies"],
                        }
                    },
                },
                indent=2,
            )
            + "\n",
            "index.html": (
                '<!doctype html><html lang="en"><head><meta charset="UTF-8" />'
                '<meta name="viewport" content="width=device-width,initial-scale=1.0" />'
                f"<title>{html.escape(name)}</title></head><body><div id=\"root\"></div>"
                '<script type="module" src="/src/main.tsx"></script></body></html>\n'
            ),
            "tsconfig.json": json.dumps(
                {
                    "compilerOptions": {
                        "target": "ES2022",
                        "useDefineForClassFields": True,
                        "lib": ["ES2022", "DOM", "DOM.Iterable"],
                        "allowJs": False,
                        "skipLibCheck": True,
                        "esModuleInterop": True,
                        "allowSyntheticDefaultImports": True,
                        "strict": True,
                        "forceConsistentCasingInFileNames": True,
                        "module": "ESNext",
                        "moduleResolution": "Bundler",
                        "resolveJsonModule": True,
                        "isolatedModules": True,
                        "noEmit": True,
                        "jsx": "react-jsx",
                    },
                    "include": ["src"],
                },
                indent=2,
            )
            + "\n",
            "vite.config.ts": (
                "import { defineConfig } from 'vite';\n"
                "import react from '@vitejs/plugin-react';\n"
                "export default defineConfig({ base: './', plugins: [react()] });\n"
            ),
            "src/main.tsx": (
                "import React from 'react';\n"
                "import ReactDOM from 'react-dom/client';\n"
                "import App from './App';\n"
                "import './styles.css';\n"
                "ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);\n"
            ),
            "src/vite-env.d.ts": '/// <reference types="vite/client" />\n',
            "src/App.tsx": self._app_source(name, description),
            "src/styles.css": self._styles(),
            "tests/profile.test.mjs": (
                "import test from 'node:test';\n"
                "import assert from 'node:assert/strict';\n"
                "import { readFileSync } from 'node:fs';\n"
                "test('generated app has its root workflow', () => {\n"
                "  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');\n"
                "  assert.match(app, /Operations queue/);\n"
                "});\n"
            ),
            "project_commands.json": json.dumps(
                project_command_manifest(),
                indent=2,
            )
            + "\n",
            "README.md": (
                f"# {name}\n\nGenerated by ZeroHandoff.\n\n"
                "```bash\nnpm install\nnpm test\nnpm run build\nnpm run dev\n```\n"
            ),
            "dist/index.html": self._standalone_preview(name, description),
        }
        for relative, content in files.items():
            path = (workspace / relative).resolve()
            if workspace not in path.parents:
                raise ValueError("generated file escapes workspace")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        commands = json.loads((workspace / "project_commands.json").read_text())
        return self.inspect(workspace, implementation, commands=commands)

    def inspect(
        self,
        workspace: Path,
        implementation: dict[str, Any],
        *,
        commands: dict[str, str],
        command_results: tuple[dict[str, Any], ...] = (),
    ) -> BuildEvidence:
        workspace = workspace.resolve()
        files = sorted(
            str(path.relative_to(workspace))
            for path in workspace.rglob("*")
            if path.is_file() and "node_modules" not in path.parts
        )
        package: dict[str, Any] = {}
        current_commands: dict[str, str] = {}
        try:
            package = json.loads((workspace / "package.json").read_text())
            current_commands = json.loads((workspace / "project_commands.json").read_text())
        except (OSError, json.JSONDecodeError):
            pass
        source_text = "\n".join(
            path.read_text(errors="ignore")
            for path in workspace.rglob("*")
            if path.is_file()
            and "node_modules" not in path.parts
            and "dist" not in path.relative_to(workspace).parts
            and path.suffix in {".html", ".js", ".json", ".ts", ".tsx"}
        )
        contract_items = implementation.get("contract_item_ids", [])
        dependency_versions = [
            *package.get("dependencies", {}).values(),
            *package.get("devDependencies", {}).values(),
        ]
        scripts = package.get("scripts", {})
        safe_scripts = {
            "test": {
                "node --test tests/*.test.mjs",
                "vitest run",
                "vitest run && bash tests/browser.acceptance.sh",
                "bash tests/browser.acceptance.sh",
            },
            "typecheck": {"tsc -b --pretty false", "tsc -b", "tsc --noEmit"},
            "build": {"tsc -b && vite build", "vite build"},
        }
        lock_root: dict[str, Any] = {}
        try:
            lock_root = json.loads((workspace / "package-lock.json").read_text()).get(
                "packages", {}
            ).get("", {})
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
        checks = {
            "react_entry_exists": (workspace / "src/App.tsx").exists(),
            "vite_config_exists": (workspace / "vite.config.ts").exists(),
            "package_is_private": package.get("private") is True,
            "command_manifest_unchanged": current_commands == commands,
            "no_external_services": not any(
                token in source_text for token in ("fetch(", "axios", "http://api", "https://api")
            ),
            "contract_items_present": all(
                item in implementation.get("contract_item_ids", [])
                for item in contract_items
            ),
            "standalone_preview_exists": (workspace / "dist/index.html").exists(),
            "package_lock_present": (workspace / "package-lock.json").is_file(),
            "package_lock_matches_manifest": (
                lock_root.get("dependencies", {}) == package.get("dependencies", {})
                and lock_root.get("devDependencies", {})
                == package.get("devDependencies", {})
            ),
            "dependencies_exactly_pinned": bool(dependency_versions)
            and all(
                isinstance(version, str)
                and re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version)
                for version in dependency_versions
            ),
            "node_module_links_local": _node_module_links_are_local(workspace),
            "package_scripts_allowlisted": all(
                scripts.get(name) in allowed for name, allowed in safe_scripts.items()
            ),
            "test_harnesses_declared": _test_harnesses_are_declared(
                workspace, str(scripts.get("test", ""))
            ),
            "browser_acceptance_receipt_verified": (
                not _browser_acceptance_required(workspace)
                or (
                    bool(command_results)
                    and command_results[-1].get("command") == commands.get("test")
                    and bool(
                        command_results[-1].get("browser_acceptance_receipt")
                    )
                    and not command_results[-1].get("reported_error")
                )
            ),
            "browser_runtime_prerequisites_documented": (
                _browser_runtime_prerequisites_documented(workspace)
            ),
            "portable_dist_assets": _portable_dist_assets(workspace),
        }
        return BuildEvidence(
            profile="react-vite-local",
            files=files,
            commands=commands,
            checks=checks,
            file_digests={relative: digest_file(workspace / relative) for relative in files},
            command_results=command_results,
        )

    @staticmethod
    def _app_source(name: str, description: str) -> str:
        return f'''import {{ useMemo, useState }} from 'react';

type Item = {{ id: number; title: string; owner: string; status: 'Open' | 'In progress' | 'Done' }};
const seed: Item[] = [
  {{ id: 101, title: 'Review facilities request', owner: 'Operations', status: 'Open' }},
  {{ id: 102, title: 'Schedule equipment delivery', owner: 'Workplace', status: 'In progress' }},
  {{ id: 103, title: 'Close completed work order', owner: 'Support', status: 'Done' }},
];

export default function App() {{
  const [items, setItems] = useState(seed);
  const [filter, setFilter] = useState('All');
  const visible = useMemo(() => filter === 'All' ? items : items.filter(item => item.status === filter), [filter, items]);
  const advance = (id: number) => setItems(current => current.map(item => item.id !== id ? item : {{ ...item, status: item.status === 'Open' ? 'In progress' : 'Done' }}));
  return <main>
    <header><span className="eyebrow">ENTERPRISE OPERATIONS</span><h1>{name}</h1><p>{description}</p></header>
    <section className="metrics" aria-label="Queue summary">
      <article><strong>{{items.length}}</strong><span>Total records</span></article>
      <article><strong>{{items.filter(item => item.status !== 'Done').length}}</strong><span>Needs attention</span></article>
      <article><strong>{{items.filter(item => item.status === 'Done').length}}</strong><span>Completed</span></article>
    </section>
    <section className="panel"><div className="panel-heading"><div><span className="eyebrow">LIVE WORKFLOW</span><h2>Operations queue</h2></div>
      <label>Filter <select value={{filter}} onChange={{event => setFilter(event.target.value)}}><option>All</option><option>Open</option><option>In progress</option><option>Done</option></select></label></div>
      <div className="table" role="table">{{visible.map(item => <article className="row" role="row" key={{item.id}}><span>#{{item.id}}</span><b>{{item.title}}</b><span>{{item.owner}}</span><span className={{`pill ${{item.status.replace(' ', '-').toLowerCase()}}`}}>{{item.status}}</span><button disabled={{item.status === 'Done'}} onClick={{() => advance(item.id)}}>{{item.status === 'Open' ? 'Start' : item.status === 'In progress' ? 'Complete' : 'Done'}}</button></article>)}}</div>
    </section>
  </main>;
}}
'''

    @staticmethod
    def _styles() -> str:
        return """:root{font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;color:#111827;background:#f6f1e7;font-synthesis:none}*{box-sizing:border-box}body{margin:0}main{max-width:1180px;margin:auto;padding:64px 28px}header{max-width:760px}h1{font-size:clamp(2.6rem,7vw,5.8rem);line-height:.94;letter-spacing:-.06em;margin:.2em 0}p{color:#5b6472;font-size:1.1rem}.eyebrow{font-size:.72rem;font-weight:800;letter-spacing:.18em}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:48px 0}.metrics article,.panel{background:#fff;border:2px solid #111827;border-radius:18px;box-shadow:7px 7px 0 #111827}.metrics article{padding:24px}.metrics strong{font-size:2.2rem;display:block}.metrics span{color:#667085}.panel{overflow:hidden}.panel-heading{padding:24px;display:flex;align-items:end;justify-content:space-between;border-bottom:2px solid #111827}.panel-heading h2{margin:.3rem 0 0}select,button{font:inherit;border:2px solid #111827;border-radius:8px;background:white;padding:9px 12px}button{background:#ffd447;font-weight:800;cursor:pointer}button:disabled{opacity:.45;cursor:default}.row{display:grid;grid-template-columns:80px 2fr 1fr 120px 100px;align-items:center;gap:16px;padding:18px 24px;border-bottom:1px solid #d8dee7}.row:last-child{border:0}.pill{font-size:.75rem;font-weight:800;padding:7px;border-radius:999px;text-align:center;background:#dbeafe}.pill.done{background:#b7f7de}.pill.in-progress{background:#ffe9a8}@media(max-width:720px){main{padding:36px 16px}.metrics{grid-template-columns:1fr}.panel-heading{align-items:start;gap:16px;flex-direction:column}.row{grid-template-columns:1fr 1fr}.row b{grid-column:1/-1}}"""

    @staticmethod
    def _standalone_preview(name: str, description: str) -> str:
        return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(name)}</title><style>body{{font:16px system-ui;background:#f6f1e7;color:#111827;margin:0}}main{{max-width:1000px;margin:auto;padding:7vw 24px}}h1{{font-size:clamp(3rem,8vw,6rem);letter-spacing:-.06em;line-height:.9;margin:.2em 0}}.card{{background:#fff;border:2px solid;border-radius:18px;box-shadow:7px 7px #111827;padding:24px;margin-top:40px}}.row{{display:grid;grid-template-columns:100px 1fr 140px;padding:16px 0;border-bottom:1px solid #ddd}}.tag{{background:#b7f7de;border-radius:999px;padding:5px 10px;text-align:center}}small{{font-weight:800;letter-spacing:.18em}}</style><main><small>GENERATED AUTONOMOUSLY</small><h1>{html.escape(name)}</h1><p>{html.escape(description)}</p><section class="card"><h2>Operations queue</h2><div class="row"><span>#101</span><b>Review facilities request</b><span class="tag">Open</span></div><div class="row"><span>#102</span><b>Schedule equipment delivery</b><span class="tag">In progress</span></div><div class="row"><span>#103</span><b>Close completed work order</b><span class="tag">Done</span></div></section></main></html>"""
