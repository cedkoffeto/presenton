import fs from "fs";
import fsPromises from "fs/promises";
import path from "path";
import { Readable } from "stream";
import { NextRequest, NextResponse } from "next/server";

const CONTENT_TYPES: Record<string, string> = {
  ".pdf": "application/pdf",
  ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  ".zip": "application/zip",
};

function getFastApiBaseUrl(): string {
  const internal = process.env.FAST_API_INTERNAL_URL?.trim();
  if (internal) {
    return internal.replace(/\/+$/, "");
  }
  const configured = process.env.NEXT_PUBLIC_FAST_API?.trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }
  return "http://127.0.0.1:8000";
}

function getExportsDirectory(): string {
  const appDataDirectory = process.env.APP_DATA_DIRECTORY?.trim();
  if (!appDataDirectory) {
    throw new Error("APP_DATA_DIRECTORY is required to download exported files.");
  }
  return path.join(appDataDirectory, "exports");
}

function getSafeExportName(request: NextRequest): string | null {
  const decodedName = request.nextUrl.searchParams.get("name");

  if (
    !decodedName ||
    decodedName.includes("/") ||
    decodedName.includes("\\") ||
    decodedName !== path.basename(decodedName)
  ) {
    return null;
  }

  return decodedName;
}

function contentDisposition(filename: string): string {
  const fallback = filename.replace(/[^a-zA-Z0-9._-]/g, "_");
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encodeURIComponent(filename)}`;
}

export async function GET(request: NextRequest) {
  const filename = getSafeExportName(request);
  const presentationId = request.nextUrl.searchParams.get("id")?.trim();
  const shareToken = request.nextUrl.searchParams.get("share_token")?.trim();
  if (!filename) {
    return NextResponse.json({ error: "Invalid export file name" }, { status: 400 });
  }
  if (!presentationId) {
    return NextResponse.json(
      { error: "Missing presentation id" },
      { status: 400 }
    );
  }

  try {
    const presentationUrl = new URL(
      `${getFastApiBaseUrl()}/api/v1/ppt/presentation/${encodeURIComponent(
        presentationId
      )}`
    );
    if (shareToken) {
      presentationUrl.searchParams.set("share_token", shareToken);
    }
    const authResponse = await fetch(presentationUrl, {
      method: "GET",
      headers: request.headers.get("cookie")
        ? { cookie: request.headers.get("cookie") as string }
        : undefined,
      cache: "no-store",
    });
    if (!authResponse.ok) {
      return NextResponse.json({ error: "Unauthorized" }, { status: authResponse.status });
    }

    const exportsDirectory = getExportsDirectory();
    const resolvedExportsDirectory = await fsPromises.realpath(exportsDirectory);
    const filePath = path.join(exportsDirectory, filename);
    const resolvedFilePath = await fsPromises.realpath(filePath);

    if (
      resolvedFilePath !== resolvedExportsDirectory &&
      !resolvedFilePath.startsWith(resolvedExportsDirectory + path.sep)
    ) {
      return NextResponse.json({ error: "Access denied" }, { status: 403 });
    }

    const ext = path.extname(filename).toLowerCase();
    const stats = await fsPromises.stat(resolvedFilePath);
    const stream = Readable.toWeb(fs.createReadStream(resolvedFilePath));
    return new NextResponse(stream as unknown as BodyInit, {
      headers: {
        "Content-Type": CONTENT_TYPES[ext] ?? "application/octet-stream",
        "Content-Disposition": contentDisposition(filename),
        "Content-Length": String(stats.size),
        "Cache-Control": "no-store",
      },
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException)?.code === "ENOENT") {
      return NextResponse.json({ error: "Export file not found" }, { status: 404 });
    }

    console.error("[export-presentation:file]", error);
    return NextResponse.json(
      { error: "Failed to download export file" },
      { status: 500 }
    );
  }
}
