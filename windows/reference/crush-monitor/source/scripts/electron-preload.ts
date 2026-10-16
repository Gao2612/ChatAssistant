import { contextBridge, ipcRenderer, webUtils } from "electron";

type DesktopPdfExportRequest = {
  html: string;
  fileName: string;
};

type DesktopPdfExportResult = {
  saved: boolean;
  path?: string;
};

type DesktopCompanionImageRequest = {
  sourceFilePath: string;
  relativePath: string;
};

type DesktopCompanionMediaRequest = DesktopCompanionImageRequest;

const desktopApi = {
  exportPdf: (
    request: DesktopPdfExportRequest,
  ): Promise<DesktopPdfExportResult> =>
    ipcRenderer.invoke("export-analysis-pdf", request) as Promise<DesktopPdfExportResult>,
  readCompanionImage: (
    sourceFile: File,
    relativePath: string,
  ): Promise<string | null> => {
    const sourceFilePath = webUtils.getPathForFile(sourceFile);
    return ipcRenderer.invoke("read-companion-image", {
      sourceFilePath,
      relativePath,
    } satisfies DesktopCompanionImageRequest) as Promise<string | null>;
  },
  readCompanionMedia: (
    sourceFile: File,
    relativePath: string,
  ): Promise<string | null> => {
    const sourceFilePath = webUtils.getPathForFile(sourceFile);
    return ipcRenderer.invoke("read-companion-media", {
      sourceFilePath,
      relativePath,
    } satisfies DesktopCompanionMediaRequest) as Promise<string | null>;
  },
};

contextBridge.exposeInMainWorld("crushDesktop", desktopApi);
