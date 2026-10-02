"""Sandbox-only AST check for a proven self-cancelling React timer effect.

The controller owns the code; it reads candidate source without executing it.
It deliberately checks one certain failure, rather than prohibiting state effects.
"""

from yleum_orchestrator.core.project_machine import MachineManifest

REACT_EFFECT_CHECK_PHASE = "controller:react-effect-lifetime:v1"

REACT_EFFECT_CONTRACT_JS = r"""
const fs = require('node:fs');
const path = require('node:path');
const root = process.cwd();
const pkgPath = path.join(root, 'package.json');
if (!fs.existsSync(pkgPath)) process.exit(0);
const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
if (!(pkg.dependencies?.react || pkg.devDependencies?.react)) process.exit(0);
let parser;
try { parser = require.resolve(path.join(root,'node_modules/typescript')); }
catch (error) {
  if (pkg.dependencies?.next || pkg.devDependencies?.next ||
    pkg.dependencies?.typescript || pkg.devDependencies?.typescript) throw error;
  console.log('React effect lifetime check not applicable: non-Next project without TypeScript');
  process.exit(0);
}
const ts = require(parser);
const issues = [];
let files = 0, bytes = 0;
const unwrap = n => {
  while (n && (ts.isParenthesizedExpression(n) || ts.isAsExpression(n) ||
    ts.isTypeAssertionExpression(n) || ts.isNonNullExpression(n))) n = n.expression;
  return n;
};
const ident = (n, name) => n && ts.isIdentifier(n) && n.text === name;
const string = n => { n=unwrap(n); return n && ts.isStringLiteral(n) ? n.text : null; };
const functionLike = n => ts.isArrowFunction(n) || ts.isFunctionExpression(n) ||
  ts.isFunctionDeclaration(n) || ts.isMethodDeclaration(n);
function inScope(node, fn) {
  ts.forEachChild(node, child => {
    fn(child);
    if (functionLike(child)) return;
    inScope(child, fn);
  });
}
function bindingNames(name, names) {
  if (ts.isIdentifier(name)) names.add(name.text);
  else if (ts.isObjectBindingPattern(name) || ts.isArrayBindingPattern(name))
    for (const item of name.elements)
      if (ts.isBindingElement(item)) bindingNames(item.name,names);
}
function inspect(filename, text) {
  const ast = ts.createSourceFile(filename, text, ts.ScriptTarget.Latest, true,
    filename.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  // Syntax errors remain the normal typecheck's responsibility.
  if (ast.parseDiagnostics.length) return;
  const hooks = new Map(), namespaces = new Set();
  for (const stmt of ast.statements) {
    if (!ts.isImportDeclaration(stmt) || string(stmt.moduleSpecifier) !== 'react') continue;
    const clause = stmt.importClause;
    if (clause?.name) namespaces.add(clause.name.text);
    const named = clause?.namedBindings;
    if (named && ts.isNamespaceImport(named)) namespaces.add(named.name.text);
    if (named && ts.isNamedImports(named)) for (const spec of named.elements) {
      hooks.set(spec.name.text, (spec.propertyName || spec.name).text);
    }
  }
  function hook(n, name) {
    n=unwrap(n);
    return n && (ts.isIdentifier(n) && hooks.get(n.text) === name ||
      ts.isPropertyAccessExpression(n) && ts.isIdentifier(n.expression) &&
      namespaces.has(n.expression.text) && n.name.text === name);
  }
  function declarations(scope, names) {
    inScope(scope, n => {
      if (ts.isVariableDeclaration(n)) bindingNames(n.name,names);
      if ((ts.isFunctionDeclaration(n) || ts.isClassDeclaration(n)) && n.name)
        bindingNames(n.name,names);
    });
  }
  const moduleBindings = new Set();
  declarations(ast,moduleBindings);
  // Other imports can shadow the timer globals; React's own imports are the hooks.
  for (const stmt of ast.statements) {
    if (!ts.isImportDeclaration(stmt) || string(stmt.moduleSpecifier) === 'react') continue;
    const clause=stmt.importClause;
    if (clause?.name) bindingNames(clause.name,moduleBindings);
    const named=clause?.namedBindings;
    if (named && ts.isNamespaceImport(named)) bindingNames(named.name,moduleBindings);
    if (named && ts.isNamedImports(named))
      for (const item of named.elements) bindingNames(item.name,moduleBindings);
  }
  function component(fn) {
    if (!fn.body || !ts.isBlock(fn.body)) return;
    const states = new Map(), shadowed = new Set(moduleBindings);
    for (let scope=fn; scope && scope!==ast; scope=scope.parent) {
      if (functionLike(scope)) {
        if (scope.name) bindingNames(scope.name,shadowed);
        for (const param of scope.parameters) bindingNames(param.name,shadowed);
        if (scope.body) declarations(scope.body,shadowed);
      }
      if ((ts.isClassDeclaration(scope) || ts.isClassExpression(scope)) && scope.name)
        bindingNames(scope.name,shadowed);
    }
    inScope(fn.body, n => {
      if (ts.isVariableDeclaration(n)) bindingNames(n.name,shadowed);
      if ((ts.isFunctionDeclaration(n) || ts.isClassDeclaration(n)) && n.name)
        bindingNames(n.name,shadowed);
      if (!ts.isVariableDeclaration(n) || !ts.isArrayBindingPattern(n.name) ||
        !n.initializer || !ts.isCallExpression(n.initializer) ||
        !hook(n.initializer.expression,'useState') || n.name.elements.length !== 2) return;
      const [state,setter] = n.name.elements;
      if (!ts.isBindingElement(state) || !ts.isIdentifier(state.name) ||
        !ts.isBindingElement(setter) || !ts.isIdentifier(setter.name)) return;
      const initial = string(n.initializer.arguments[0]);
      if (initial !== null) states.set(state.name.text, {setter:setter.name.text, initial});
    });
    if (['setTimeout','clearTimeout',...hooks.keys(),...namespaces]
      .some(name=>shadowed.has(name))) return;
    inScope(fn.body, n => {
      if (!ts.isCallExpression(n) || !hook(n.expression,'useEffect')) return;
      const [effect,deps] = n.arguments;
      if (!effect || !functionLike(effect) || effect.parameters.length ||
                !ts.isBlock(effect.body) ||
        !deps || !ts.isArrayLiteralExpression(deps)) return;
      const stmts = effect.body.statements;
      // Only the fully attested four-statement shape: no extra branch/throw/work.
      if (stmts.length !== 4 || !ts.isVariableStatement(stmts[2]) ||
        stmts[2].declarationList.declarations.length !== 1 ||
        !ts.isReturnStatement(stmts[3])) return;
      for (const [state,info] of states) {
        if (!deps.elements.some(x=>ident(x,state))) continue;
        // Exact early return makes the replacement render abandon this attempt.
        const guard = stmts[0];
        if (!guard || !ts.isIfStatement(guard) || guard.elseStatement) continue;
        const condition = unwrap(guard.expression);
        const ret = ts.isBlock(guard.thenStatement) ?
          guard.thenStatement.statements : [guard.thenStatement];
        if (ret.length !== 1 || !ts.isReturnStatement(ret[0]) || ret[0].expression ||
          !ts.isBinaryExpression(condition) ||
          ![ts.SyntaxKind.ExclamationEqualsEqualsToken,ts.SyntaxKind.ExclamationEqualsToken]
            .includes(condition.operatorToken.kind)) continue;
        if (!(ident(condition.left,state) && string(condition.right) === info.initial ||
          ident(condition.right,state) && string(condition.left) === info.initial)) continue;
        const updates = stmts.filter(s=>ts.isExpressionStatement(s) &&
          ts.isCallExpression(s.expression) &&
          ident(s.expression.expression,info.setter));
        if (updates.length !== 1 || stmts[1] !== updates[0]) continue;
        const update = updates[0];
        const next = string(update.expression.arguments[0]);
        if (next === null || next === info.initial) continue;
        // Unconditional top-level timer after the synchronous state transition.
        for (const stmt of stmts.slice(stmts.indexOf(update)+1)) {
          if (!ts.isVariableStatement(stmt)) continue;
          for (const decl of stmt.declarationList.declarations) {
            if (!ts.isIdentifier(decl.name) || !decl.initializer ||
              !ts.isCallExpression(decl.initializer) ||
              !ident(decl.initializer.expression,'setTimeout')) continue;
            const cleanup = stmts.find(s=>ts.isReturnStatement(s) && s.expression &&
              functionLike(s.expression));
            if (!cleanup || cleanup.expression.parameters.length) continue;
            const body = cleanup.expression.body;
            const expr = ts.isBlock(body) && body.statements.length === 1 &&
              ts.isExpressionStatement(body.statements[0]) ? body.statements[0].expression : body;
            if (!ts.isCallExpression(expr) || !ident(expr.expression,'clearTimeout') ||
              expr.arguments.length !== 1 || !ident(expr.arguments[0],decl.name.text)) continue;
            const line = ast.getLineAndCharacterOfPosition(n.getStart(ast)).line + 1;
            issues.push(`${path.relative(root,filename)}:${line}: self-cancelling React effect: `+
              `changing dependency ${state} from ${info.initial} to ${next} `+
              'cancels its completion timer. '+
              'Start the attempt from a stable identity/query/retry key, '+
              'or drive it from the loading state; '+
              'keep cleanup for unmount/key changes and test that loading reaches success/error.');
          }
        }
      }
    });
  }
  function visit(n) { if(functionLike(n)) component(n); ts.forEachChild(n,visit); }
  visit(ast);
}
function walk(dir) {
  if (!fs.existsSync(dir) || fs.lstatSync(dir).isSymbolicLink()) return;
  for (const entry of fs.readdirSync(dir,{withFileTypes:true})) {
    if (entry.isSymbolicLink()) continue;
    const file=path.join(dir,entry.name);
    if(entry.isDirectory()) {
      if(!['node_modules','.next','.git'].includes(entry.name)) walk(file);
    }
    else if(entry.isFile() && /\.[cm]?[jt]sx?$/.test(entry.name)) {
      const size=fs.statSync(file).size;
      if(++files>500 || size>1024*1024 || (bytes+=size)>8*1024*1024)
        throw Error('React effect source check exceeded its bounded source budget');
      inspect(file,fs.readFileSync(file,'utf8'));
    }
  }
}
for (const dir of ['src','app','pages','components']) walk(path.join(root,dir));
if(issues.length) {console.log(issues.slice(0,10).join('\n')); process.exitCode=1;}
else console.log('React effect lifetime check passed');
"""


def node_manifest_commands(manifest: MachineManifest) -> bool:
    """Leave independent Python/Ruby/Go manifests untouched."""
    return any(
        task.argv and task.argv[0] in {"pnpm", "npm", "npx", "node"} for task in manifest.tasks
    )
