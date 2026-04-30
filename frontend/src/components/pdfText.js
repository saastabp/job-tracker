/**
 * Extract plain text from a PDF File using pdfjs-dist.
 *
 * Runs entirely in the browser — the AI Lambda never sees the PDF bytes,
 * which keeps it text-only and out of the VPC. The pdfjs worker is loaded
 * lazily so first-paint of the SPA isn't slowed down by a feature most
 * pages don't use.
 */
import * as pdfjs from 'pdfjs-dist';
// Vite resolves the `?url` suffix to the worker bundle URL at build time.
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
export async function extractPdfText(file) {
    const buf = await file.arrayBuffer();
    const doc = await pdfjs.getDocument({ data: buf }).promise;
    const pages = [];
    for (let i = 1; i <= doc.numPages; i++) {
        const page = await doc.getPage(i);
        const content = await page.getTextContent();
        const pageText = content.items
            .map((item) => ('str' in item ? item.str : ''))
            .join(' ');
        pages.push(pageText);
    }
    await doc.destroy();
    return pages.join('\n\n').trim();
}
