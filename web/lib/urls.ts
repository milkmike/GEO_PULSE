export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value || /[\s\\]/u.test(value)) return null;
  try {
    const parsed = new URL(value);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) {
      return null;
    }
    return value;
  } catch {
    return null;
  }
}
