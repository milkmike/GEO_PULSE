export function safeHttpUrl(value: string | null | undefined): string | null {
  if (
    !value
    || /[\s\\\u0000-\u001f\u007f]/u.test(value)
    || /%(?:0[0-9a-f]|1[0-9a-f]|7f)/iu.test(value)
  ) return null;
  try {
    const parsed = new URL(value);
    // Accessing port also rejects malformed/out-of-range explicit ports.
    parsed.port;
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) {
      return null;
    }
    const hostname = parsed.hostname.endsWith(".")
      ? parsed.hostname.slice(0, -1)
      : parsed.hostname;
    if (!hostname || hostname.length > 253) return null;
    const isIpv6 = hostname.startsWith("[") && hostname.endsWith("]");
    if (!isIpv6) {
      const labels = hostname.split(".");
      const validLabel = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/iu;
      if (labels.some((label) => !validLabel.test(label))) return null;
    }
    return value;
  } catch {
    return null;
  }
}
