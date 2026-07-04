import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["obtaining-benchmark-transform-scoop.trycloudflare.com"],
  turbopack: {
    root: process.cwd(),
  },
};

export default nextConfig;
