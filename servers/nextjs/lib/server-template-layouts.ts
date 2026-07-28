import fs from "fs/promises";
import path from "path";

import { compileTemplateSchema } from "@/lib/compile-template-schema";
import { getFastApiAuthHeaders, getFastApiBaseUrl } from "@/lib/fastapi-internal";

export type BuiltinLayoutSlide = {
  id: string;
  name: string;
  description: string;
  json_schema: unknown;
};

const DEFAULT_ICON_WEIGHT = "bold";
const ALLOWED_ICON_WEIGHTS = new Set([
  "bold",
  "duotone",
  "fill",
  "light",
  "regular",
  "thin",
]);
function normalizeIconWeight(value: unknown): string {
  if (typeof value !== "string") {
    return DEFAULT_ICON_WEIGHT;
  }
  const normalized = value.trim().toLowerCase().replace("_", "-");
  return ALLOWED_ICON_WEIGHTS.has(normalized) ? normalized : DEFAULT_ICON_WEIGHT;
}

function getIconWeightFromSettings(settings: Record<string, unknown>): string {
  return normalizeIconWeight(settings.icon_weight);
}

function normalizeTemplateGroupId(directoryName: string): string {
  return directoryName
    .replace(/(?<!^)(?=[A-Z])/g, "-")
    .toLowerCase()
    .replace(/_/g, "-");
}

async function resolveBuiltinTemplateDirectory(
  templatesRoot: string,
  group: string,
): Promise<{ id: string; dir: string } | null> {
  const normalizedGroup = group.trim().toLowerCase().replace(/_/g, "-");
  const exactDir = path.join(templatesRoot, group);

  try {
    await fs.access(exactDir);
    return { id: normalizedGroup, dir: exactDir };
  } catch {
    // Fall through to normalized directory lookup.
  }

  const entries = await fs.readdir(templatesRoot, { withFileTypes: true });
  const match = entries.find(
    (entry) =>
      entry.isDirectory() &&
      normalizeTemplateGroupId(entry.name) === normalizedGroup,
  );

  if (!match) {
    return null;
  }

  return {
    id: normalizedGroup,
    dir: path.join(templatesRoot, match.name),
  };
}

/**
 * Build layout + JSON schemas for a built-in template directory without importing
 * `presentation-templates/index.tsx` (that pulls Recharts/client bundles into RSC).
 *
 * Scans `app/presentation-templates/<group>/*.tsx` and runs the same schema compiler
 * the UI uses. Slide IDs match `getSchemaByTemplateId` in `presentation-templates/index.tsx`
 * (`id` is the fully-qualified `<group>:<layoutId>` form used by layout lookup).
 * Slide order is sorted by filename for stability (may differ slightly from registration
 * order in index.tsx).
 */
export async function buildBuiltinTemplateLayoutPayload(group: string): Promise<{
  name: string;
  ordered: boolean;
  icon_weight: string;
  slides: BuiltinLayoutSlide[];
} | null> {
  const templatesRoot = path.join(
    process.cwd(),
    "app",
    "presentation-templates",
  );
  let templateDirectory: { id: string; dir: string } | null = null;

  try {
    templateDirectory = await resolveBuiltinTemplateDirectory(
      templatesRoot,
      group,
    );
  } catch {
    return null;
  }
  if (!templateDirectory) {
    return null;
  }

  const { id: templateId, dir } = templateDirectory;

  let ordered = false;
  let icon_weight = DEFAULT_ICON_WEIGHT;
  try {
    const raw = await fs.readFile(path.join(dir, "settings.json"), "utf8");
    const s = JSON.parse(raw) as Record<string, unknown> & { ordered?: boolean };
    if (typeof s.ordered === "boolean") {
      ordered = s.ordered;
    }
    icon_weight = getIconWeightFromSettings(s);
  } catch {
    // settings.json optional
  }

  const entries = await fs.readdir(dir, { withFileTypes: true });
  const tsxFiles = entries
    .filter((e) => e.isFile() && e.name.endsWith(".tsx"))
    .map((e) => e.name)
    .filter(
      (n) =>
        !n.includes(".test.") &&
        !n.includes(".spec.") &&
        n !== "ExampleSlideLayoutTemplate.tsx",
    )
    .sort();

  const slides: BuiltinLayoutSlide[] = [];

  for (const file of tsxFiles) {
    const full = path.join(dir, file);
    const code = await fs.readFile(full, "utf8");
    const compiled = compileTemplateSchema(code);
    if (!compiled) {
      continue;
    }
    const qualifiedLayoutId = compiled.layoutId.includes(":")
      ? compiled.layoutId
      : `${templateId}:${compiled.layoutId}`;
    slides.push({
      id: qualifiedLayoutId,
      name: compiled.layoutName,
      description: compiled.layoutDescription,
      json_schema: compiled.schemaJSON,
    });
  }

  if (slides.length === 0) {
    return null;
  }

  return { name: templateId, ordered, icon_weight, slides };
}

export type CustomLayoutCompileInput = {
  layout_id: string;
  layout_name: string;
  layout_code: string;
};

/**
 * Compile custom template slide layouts stored in the DB (same IDs as /schema?group=custom-*).
 */
export function buildCustomTemplateLayoutPayload(
  group: string,
  layouts: CustomLayoutCompileInput[],
): {
  name: string;
  ordered: boolean;
  icon_weight: string;
  slides: BuiltinLayoutSlide[];
} | null {
  if (!group.startsWith("custom-") || layouts.length === 0) {
    return null;
  }

  const slides: BuiltinLayoutSlide[] = [];

  for (const layout of layouts) {
    const compiled = compileTemplateSchema(layout.layout_code);
    if (!compiled) {
      console.warn(
        `[server-template-layouts] failed to compile custom layout ${layout.layout_name}`,
      );
      continue;
    }

    const qualifiedLayoutId = `${group}:${compiled.layoutId}`;
    slides.push({
      id: qualifiedLayoutId,
      name: compiled.layoutName || layout.layout_name,
      description: compiled.layoutDescription,
      json_schema: compiled.schemaJSON,
    });
  }

  if (slides.length === 0) {
    return null;
  }

  return {
    name: group,
    ordered: false,
    icon_weight: DEFAULT_ICON_WEIGHT,
    slides,
  };
}

type FastApiTemplateLayoutsResponse = {
  layouts?: Array<{
    layout_id: string;
    layout_name: string;
    layout_code: string;
  }>;
};

/**
 * Fetch custom template slide code from FastAPI and compile (for `/api/template?group=custom-*`).
 */
export async function buildCustomTemplateLayoutPayloadFromApi(
  group: string,
): Promise<{
  name: string;
  ordered: boolean;
  icon_weight: string;
  slides: BuiltinLayoutSlide[];
} | null> {
  if (!group.startsWith("custom-")) {
    return null;
  }

  const url = `${getFastApiBaseUrl()}/api/v1/ppt/template/${encodeURIComponent(group)}/layouts`;
  const response = await fetch(url, {
    headers: getFastApiAuthHeaders(),
    cache: "no-store",
  });

  if (!response.ok) {
    return null;
  }

  const data = (await response.json()) as FastApiTemplateLayoutsResponse;
  const layouts = data.layouts ?? [];
  if (layouts.length === 0) {
    return null;
  }

  return buildCustomTemplateLayoutPayload(
    group,
    layouts.map((layout) => ({
      layout_id: layout.layout_id,
      layout_name: layout.layout_name,
      layout_code: layout.layout_code,
    })),
  );
}
