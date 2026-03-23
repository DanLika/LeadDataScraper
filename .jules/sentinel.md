## 2026-03-18 - Prevent Data Exfiltration to LLMs
**Vulnerability:** Prompt injection and sensitive data exfiltration risk. The application was passing the entire database row (via `select("*")`) to an external AI model (Gemini) when answering general queries.
**Learning:** When fetching context for LLM prompts from a database, using `select("*")` blindly exposes all columns, including PII and potentially malicious user-provided strings. This violates the principle of least privilege.
**Prevention:** Always explicitly define the `select()` statement to only include safe, non-sensitive columns necessary for the prompt context.

## 2026-03-23 - Overly Permissive CORS Policy
**Vulnerability:** The API was configured with `allow_origins=["*"]`, allowing any website to make cross-origin requests to the backend.
**Learning:** Defaulting to a wildcard origin is often done for ease of development but poses a significant security risk by allowing CSRF and data exfiltration from unauthorized domains.
**Prevention:** Always use a whitelist of allowed domains, preferably configured via environment variables, even in development.
