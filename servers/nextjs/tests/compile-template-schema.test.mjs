import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

import { build } from "esbuild";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function resolveNextAlias(importPath) {
  const basePath = path.join(projectRoot, importPath.slice(2));
  const candidates = [
    basePath,
    `${basePath}.ts`,
    `${basePath}.tsx`,
    `${basePath}.js`,
    path.join(basePath, "index.ts"),
    path.join(basePath, "index.tsx"),
    path.join(basePath, "index.js"),
  ];
  return candidates.find((candidate) => existsSync(candidate)) ?? basePath;
}

async function loadCompiler() {
  const outDir = await mkdtemp(path.join(tmpdir(), "template-schema-test-"));
  const outfile = path.join(outDir, "compiler.mjs");

  await build({
    entryPoints: [path.join(projectRoot, "lib", "compile-template-schema.ts")],
    outfile,
    bundle: true,
    format: "esm",
    platform: "node",
    sourcemap: false,
    logLevel: "silent",
    plugins: [
      {
        name: "next-alias",
        setup(builder) {
          builder.onResolve({ filter: /^@\// }, (args) => ({
            path: resolveNextAlias(args.path),
          }));
        },
      },
    ],
  });

  return import(pathToFileURL(outfile).href);
}

const compilerPromise = loadCompiler();

test("reads built-in slide layout metadata", async () => {
  const { compileTemplateSchema } = await compilerPromise;
  const result = compileTemplateSchema(`
import * as z from "zod";

export const slideLayoutId = "summary-cards-slide";
export const slideLayoutName = "Summary Cards Slide";
export const slideLayoutDescription = "A report summary layout.";
export const Schema = z.object({
  title: z.string().default("Executive Summary"),
});
`);

  assert.equal(result.layoutId, "summary-cards-slide");
  assert.equal(result.layoutName, "Summary Cards Slide");
  assert.equal(result.layoutDescription, "A report summary layout.");
});

test("keeps custom layout metadata compatibility", async () => {
  const { compileTemplateSchema } = await compilerPromise;
  const result = compileTemplateSchema(`
import * as z from "zod";

export const layoutId = "custom-report-slide";
export const layoutName = "Custom Report Slide";
export const layoutDescription = "A custom report layout.";
export const Schema = z.object({
  title: z.string().default("Executive Summary"),
});
`);

  assert.equal(result.layoutId, "custom-report-slide");
  assert.equal(result.layoutName, "Custom Report Slide");
  assert.equal(result.layoutDescription, "A custom report layout.");
});
