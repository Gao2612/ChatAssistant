pypdfium2 runtime dependency — license / provenance record (T6, 2026-10-18)
==========================================================================

Package:      pypdfium2 5.14.0 (PyPI wheel, Windows x64 CPython 3.12)
Requirement:  windows/lite/requirements.txt -> pypdfium2>=5.14,<6
Used for:     PDF page rasterization for optional local OCR of text-less pages
              (T6 history import). Text pages are extracted without rendering.

PDFium binary bundled by the wheel
----------------------------------
Version:      156.0.8076 (build "pdfium-binaries", flags: [] — standard build;
              no V8 / XFA flags present in version.json)
Binary:       site-packages/pypdfium2_raw/pdfium.dll (Windows x64 only)
Context:      the wheel is a prebuilt PDFium distribution. The wheel contents were
              inspected with `pip show`/site-packages listing: only the
              PDFium DLL + Python bindings are shipped; no V8, no XFA, no
              network stack.

License summary (from wheel METADATA: "BSD-3-Clause, Apache-2.0, dependency
licenses"):
- pypdfium2 wrapper: Apache-2.0 (LICENSES/Apache-2.0.txt) and BSD-3-Clause
  (LICENSES/BSD-3-Clause.txt); documentation CC-BY-4.0
  (LICENSES/CC-BY-4.0.txt).
- PDFium and its bundled third-party libraries: see
  data/windows_x64/BUILD_LICENSES/*.txt (pdfium.txt, pdfium-binaries.txt,
  abseil, agg23, fast_float, freetype, icu, lcms, libjpeg_turbo,
  libopenjpeg, libpng, llvm-libc, simdutf, zlib).

version.json (copied from the installed wheel) records the exact PDFium build.

Distribution note: when packaging the Windows installer (T11), these files must
be shipped alongside the application and referenced from the third-party notice
list. The URL/source of the wheel is the configured PyPI registry; no custom
PDFium build is used.
