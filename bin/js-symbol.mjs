#!/usr/bin/env node
/* Find a JS symbol's exact line span with a real parser (acorn). Usage: js-symbol.mjs <file> <name>
   A dotted name (Outer.inner) finds `inner` nested inside `Outer`. Prints {"start","end","kind"} or exits 1. */
import { readFileSync } from "node:fs";
import * as acorn from "acorn";

const isOutline = process.argv.slice(2).includes("--outline");
const rawArgs = process.argv.slice(2).filter(a => a !== "--outline");
const file = rawArgs[0];
const name = rawArgs[1];
if (!file) process.exit(1);
const src = readFileSync(file, "utf8");
const opts = { ecmaVersion: "latest", locations: true, allowHashBang: true };
let ast;
try { ast = acorn.parse(src, { ...opts, sourceType: "module" }); }
catch { try { ast = acorn.parse(src, { ...opts, sourceType: "script" }); } catch (e) { process.stderr.write(String(e.message)); process.exit(2); } }

if (isOutline) {
  const lines = [];
  function processNested(body) {
    if (!body || !Array.isArray(body)) return;
    for (const inner of body) {
      if (inner.type === "FunctionDeclaration" && inner.id) {
        lines.push(`  L${inner.loc.start.line}-${inner.loc.end.line} function ${inner.id.name}`);
      } else if (inner.type === "VariableDeclaration") {
        for (const decl of inner.declarations) {
          if (decl.init && (decl.init.type === "FunctionExpression" || decl.init.type === "ArrowFunctionExpression") && decl.id) {
            lines.push(`  L${inner.loc.start.line}-${inner.loc.end.line} const ${decl.id.name}`);
          }
        }
      } else if (inner.type === "MethodDefinition" && inner.key) {
        const mName = inner.key.name || inner.key.value;
        lines.push(`  L${inner.loc.start.line}-${inner.loc.end.line} function ${mName}`);
      }
    }
  }

  for (const stmt of ast.body) {
    let node = stmt;
    let wrapper = null;
    if (node.type === "ExportNamedDeclaration" || node.type === "ExportDefaultDeclaration") {
      wrapper = node;
      node = node.declaration;
    }
    if (!node) continue;

    const start = (wrapper || node).loc.start.line;
    const end = (wrapper || node).loc.end.line;

    if (node.type === "FunctionDeclaration") {
      const fnName = node.id ? node.id.name : "default";
      lines.push(`L${start}-${end} function ${fnName}`);
      if (node.body && node.body.body) processNested(node.body.body);
    } else if (node.type === "ClassDeclaration") {
      const clsName = node.id ? node.id.name : "default";
      lines.push(`L${start}-${end} class ${clsName}`);
      if (node.body && node.body.body) processNested(node.body.body);
    } else if (node.type === "VariableDeclaration") {
      for (const decl of node.declarations) {
        if (!decl.id) continue;
        if (decl.init && (decl.init.type === "FunctionExpression" || decl.init.type === "ArrowFunctionExpression")) {
          lines.push(`L${start}-${end} const ${decl.id.name}`);
          if (decl.init.body && decl.init.body.body) processNested(decl.init.body.body);
        } else if (decl.init && decl.init.type === "ClassExpression") {
          lines.push(`L${start}-${end} class ${decl.id.name}`);
          if (decl.init.body && decl.init.body.body) processNested(decl.init.body.body);
        }
      }
    }
  }
  process.stdout.write(lines.join("\n") + (lines.length ? "\n" : ""));
  process.exit(0);
}

function nameOf(n) {
  if ((n.type === "FunctionDeclaration" || n.type === "ClassDeclaration") && n.id) return n.id.name;
  if (n.type === "VariableDeclarator" && n.id && n.id.type === "Identifier" && n.init && /Function|Class/.test(n.init.type)) return n.id.name;
  if ((n.type === "MethodDefinition" || n.type === "Property" || n.type === "PropertyDefinition") && n.key) return n.key.name || n.key.value;
  return null;
}
function* walk(node, parents) {
  yield [node, parents];
  for (const k of Object.keys(node)) {
    if (k === "loc") continue;
    const v = node[k];
    const kids = Array.isArray(v) ? v : [v];
    for (const c of kids) if (c && typeof c.type === "string") yield* walk(c, [...parents, node]);
  }
}
function span(node, parents) {
  let target = node;
  let ps = parents;
  const p = ps[ps.length - 1];
  if (node.type === "VariableDeclarator" && p && p.type === "VariableDeclaration" && p.declarations.length === 1) { target = p; ps = ps.slice(0, -1); }
  const pp = ps[ps.length - 1];
  if (pp && /^Export(Named|Default)Declaration$/.test(pp.type)) target = pp;
  return { start: target.loc.start.line, end: target.loc.end.line };
}

let scope = ast;
let hit = null;
for (const part of name.split(".")) {
  hit = null;
  for (const [n, ps] of walk(scope, [])) {
    if (n !== scope && nameOf(n) === part) { hit = [n, ps]; break; }
  }
  if (!hit) process.exit(1);
  scope = hit[0];
}
process.stdout.write(JSON.stringify({ ...span(hit[0], hit[1]), kind: hit[0].type }));
