// PKG-F2 — attachment upload module. Exports: uploadAll, supported.
const TOKEN = document.querySelector('meta[name="subway-token"]')?.content || "";
const MAX_BODY = 10 * 1024 * 1024; // server cap via attachments.UPLOAD_MAX_BODY

const TEXT_EXT = /\.(md|txt|py|js|mjs|ts|tsx|jsx|json|css|html|sh|toml|ya?ml|sql|rs|go|java|kt|swift|c|h|cpp|rb|php|diff|patch|log|csv|ini|cfg)$/i;
const TEXT_MIME = /^(text\/|application\/(json|xml|javascript|x-sh|toml|yaml|sql))/i;

function mimeOf(file) {
  return file.type || (TEXT_EXT.test(file.name) ? "text/plain" : "application/octet-stream");
}

export function supported(file) {
  if (!file || !file.name) return false;
  if (file.size > MAX_BODY) return false;
  return TEXT_EXT.test(file.name) || TEXT_MIME.test(mimeOf(file)) || file.type === "image/png" || file.type === "image/jpeg";
}

function b64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] || "");
    r.onerror = () => reject(new Error(`Could not read ${file.name}`));
    r.readAsDataURL(file);
  });
}

export async function uploadAll(attachments) {
  const paths = [];
  const failed = [];
  for (const file of attachments || []) {
    try {
      if (!supported(file)) throw new Error(`Unsupported: ${file.name}`);
      const data_base64 = await b64(file);
      const res = await fetch("/api/composer/upload", {
        method: "POST", cache: "no-store",
        headers: { "X-Subway-Token": TOKEN, "Content-Type": "application/json" },
        body: JSON.stringify({ name: file.name, type: mimeOf(file), data_base64 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error(data.error || `Upload failed (${res.status})`);
      paths.push(data.path || data.record?.path || "");
    } catch (err) {
      failed.push({ name: file.name, error: err.message });
    }
  }
  return { paths, failed };
}
