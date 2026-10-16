type DesktopPdfExportRequest = {
  html: string;
  fileName: string;
};

type DesktopPdfExportResult = {
  saved: boolean;
  path?: string;
};

type DesktopCompanionImageReader = (
  sourceFile: File,
  relativePath: string,
) => Promise<string | null>;

type DesktopCompanionMediaReader = DesktopCompanionImageReader;

declare global {
  interface Window {
    crushDesktop?: {
      exportPdf: (
        request: DesktopPdfExportRequest,
      ) => Promise<DesktopPdfExportResult>;
      readCompanionImage: DesktopCompanionImageReader;
      readCompanionMedia: DesktopCompanionMediaReader;
    };
  }
}

export {};
