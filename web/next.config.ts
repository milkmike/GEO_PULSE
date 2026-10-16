import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  async redirects() {
    return [
      { source: "/signals", destination: "/changes", permanent: false },
      { source: "/radar", destination: "/changes?type=waves", permanent: false },
    ];
  },
};

export default nextConfig;
