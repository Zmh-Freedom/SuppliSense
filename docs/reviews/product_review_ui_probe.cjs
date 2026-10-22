// Execute the current claimAssessment function on supported canonical claims.
// This is an isolated display-logic probe, not a browser screenshot or E2E test.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../..');
const ts = require(path.join(root, 'frontend/node_modules/typescript'));
const file = path.join(root, 'frontend/src/components/StructuredAgentResult.tsx');
const source = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const fn = source.statements.find(node => ts.isFunctionDeclaration(node) && node.name.text === 'claimAssessment');
if (!fn) throw new Error('claimAssessment not found');
const js = ts.transpileModule(fn.getText(source), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
// Inputs already use canonical fact_path; normalization is irrelevant to this probe.
const context = { canonicalFactPath: value => value };
vm.createContext(context);
vm.runInContext(js, context);
const rows = [
  { dimension: 'quality', score: 4.8, serviceLevel: 'low' },
  { dimension: 'delivery', score: 90, serviceLevel: 'high' },
].map(item => ({
  ...item,
  displayed: context.claimAssessment({ dimension: item.dimension, value: item.score, fact_path: 'risk_score', validation_status: 'supported', statement: '离线样本' }),
}));
console.log(JSON.stringify({ mode: 'isolated production display function; not browser E2E', rows }, null, 2));
