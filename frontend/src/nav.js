// Ask the page viewer to show a page (0-based). Works for any page of any size of file: the viewer slides its window.
export const goPage = (p) => window.dispatchEvent(new CustomEvent("pdfdesk-goto", { detail: p }));
