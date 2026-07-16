from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zerohandoff.config import digest_file
from zerohandoff.models import BuildRequest


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
            result.get("exit_code") == 0 and not result.get("timed_out")
            for result in self.command_results
        )


def _product_name(idea: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", idea)
    return " ".join(words[:5]).title() or "Enterprise Operations"


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
            "dependencies": {"@vitejs/plugin-react": "latest", "vite": "latest", "react": "latest", "react-dom": "latest"},
            "devDependencies": {"typescript": "latest", "@types/react": "latest", "@types/react-dom": "latest"},
        }
        files: dict[str, str] = {
            "package.json": json.dumps(package, indent=2) + "\n",
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
                "export default defineConfig({ plugins: [react()] });\n"
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
                {
                    "install": "npm install",
                    "test": "npm test",
                    "typecheck": "npm run typecheck",
                    "build": "npm run build",
                    "start": "npm run dev -- --host 127.0.0.1",
                    "healthcheck": "http://127.0.0.1:5173/",
                },
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
